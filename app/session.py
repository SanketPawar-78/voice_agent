"""One live call: Exotel WS audio -> VAD -> Sarvam STT -> LLM -> Sarvam TTS -> Exotel WS.

Every stage is timed (ms), published live to the dashboard (events.hub) and stored in Postgres.
"""
import asyncio
import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import WebSocket
from sqlalchemy import select

from .audio import Recorder, b64, chunks
from .config import settings
from .db import Agent, Call, Session, Turn
from .events import hub
from .exotel import pending_outbound
from .langs import LANGS, language_instruction, reply_language_note
from .llm import stream_reply
from .stt import transcribe
from .tts import cached_synthesize, synthesize
from .vad import VAD

log = logging.getLogger("session")

# One shared client for all calls: keeps TLS connections to Sarvam/LLM warm (saves ~100-300 ms per request).
HTTP = httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=120))


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


class CallSession:
    def __init__(self, ws: WebSocket, agent_name: str, direction: str = "inbound"):
        self.ws, self.agent_name, self.direction = ws, agent_name, direction
        self.vad, self.http = VAD(), HTTP
        self.call_ready: asyncio.Future | None = None
        self.agent: Agent | None = None
        self.lang = "en-IN"                      # current reply language (follows the caller)
        self.call_id = uuid.uuid4()
        self.cid = str(self.call_id)
        self.stream_sid = None
        self.history: list[dict] = []
        self.task: asyncio.Task | None = None   # current agent response (cancel = barge-in)
        self.speaking = False
        self.rec = Recorder()
        self.t0 = time.time()

    def emit(self, type_: str, **data):
        hub.publish(type_, call_id=self.cid, **data)

    # ---------- lifecycle ----------
    async def run(self):
        try:
            async for raw in self.ws.iter_text():
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "start":
                    await self.on_start(msg.get("start", msg))
                elif ev == "media":
                    await self.on_media(msg["media"]["payload"])
                elif ev == "stop":
                    break
        except Exception as e:
            log.info("ws closed: %r", e)
        finally:
            await self.close()

    async def on_start(self, start: dict):
        self.stream_sid = start.get("stream_sid") or start.get("streamSid")
        self.rec, self.t0 = Recorder(), time.time()
        out_agent = pending_outbound.pop(start.get("call_sid"), None)
        if out_agent:                                  # we dialled this call -> outbound
            self.agent_name, self.direction = out_agent, "outbound"
        async with Session() as s:
            self.agent = (await s.execute(select(Agent).where(Agent.name == self.agent_name))).scalar_one()
        self.lang = self.agent.language
        # Start talking immediately; the DB row for the call is written in the background.
        self.task = asyncio.create_task(self.greet())
        self.call_ready = asyncio.create_task(self._create_call(start))
        log.info("call started %s sid=%s", self.cid, start.get("call_sid"))
        self.emit("call_started", caller=start.get("from"), called=start.get("to"),
                  agent=self.agent.name, direction=self.direction)

    async def _create_call(self, start: dict):
        async with Session() as s:
            s.add(Call(id=self.call_id, call_sid=start.get("call_sid"), stream_sid=self.stream_sid,
                       agent_id=self.agent.id, direction=self.direction,
                       from_number=start.get("from"), to_number=start.get("to"), meta=start))
            await s.commit()

    async def on_media(self, payload: str):
        if not self.agent:
            return
        pcm = base64.b64decode(payload)
        self.rec.add_user(pcm)
        for kind, data in self.vad.feed(pcm):
            if kind == "start":
                self.emit("speech_start")
                if self.speaking:                         # barge-in
                    await self.interrupt()
            elif kind == "end":
                if self.task and not self.task.done():
                    await self.interrupt()
                self.task = asyncio.create_task(self.respond(data, time.time(), len(self.rec.user) // 16))

    async def interrupt(self):
        if self.task and not self.task.done():
            self.task.cancel()
        self.speaking = False
        self.rec.cut_agent()
        self.emit("interrupted")
        try:
            await self.ws.send_text(json.dumps({"event": "clear", "stream_sid": self.stream_sid}))
        except Exception:
            pass

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
        if not self.agent:
            return
        await self.call_ready
        duration = int(time.time() - self.t0)
        path = None
        try:
            os.makedirs(settings.recordings_dir, exist_ok=True)
            path = os.path.join(settings.recordings_dir, f"{self.cid}.wav")
            await asyncio.to_thread(self.rec.save, path)
        except Exception:
            log.exception("saving recording failed")
            path = None
        async with Session() as s:
            call = await s.get(Call, self.call_id)
            if call:
                call.status, call.ended_at = "completed", datetime.now(timezone.utc)
                call.duration_s, call.recording_path = duration, path
                await s.commit()
        self.emit("call_ended", duration_s=duration, has_recording=bool(path))

    # ---------- turn handling ----------
    async def greet(self):
        a = self.agent
        self.speaking = True
        try:
            t = time.time()
            pcm = await cached_synthesize(a.greeting, a.language, a.voice, self.http)  # instant if pre-warmed
            tts_ms = _ms(t)
            off = await self.send_audio(pcm)
        finally:
            self.speaking = False
        self.history.append({"role": "assistant", "content": a.greeting})
        self.emit("agent_reply", text=a.greeting, tts_ms=tts_ms, offset_ms=off, greeting=True)
        await self._save("assistant", a.greeting, tts_ms=tts_ms, offset_ms=off)

    async def respond(self, pcm: bytes, t_end: float, end_off: int):
        try:
            t = time.time()
            multi = (self.agent.config or {}).get("multilingual")
            text, det = await transcribe(pcm, "unknown" if multi else self.agent.language, self.http)
            stt_ms = _ms(t)
            # Follow the caller's language; ignore flips caused by one-word noises like "hmm".
            if multi and det in LANGS and (det == self.lang or len(text.split()) >= 2):
                self.lang = det
            self.emit("stage", stage="stt", ms=stt_ms)
            if not text:
                return
            start_off = max(0, end_off - len(pcm) // 16)
            log.info("USER: %s (stt %dms)", text, stt_ms)
            self.history.append({"role": "user", "content": text})
            self.emit("user_utterance", text=text, stt_ms=stt_ms, offset_ms=start_off, lang=self.lang)
            await self._save("user", text, stt_ms=stt_ms, offset_ms=start_off)
            await self.speak_stream(t_end, stt_ms)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("turn failed")
            self.emit("error", message=f"turn failed: {type(e).__name__}: {str(e)[:180]}")

    async def speak_stream(self, t_end: float, stt_ms: int):
        a, reply = self.agent, []
        m = {"stt_ms": stt_ms, "vad_ms": settings.vad_silence_ms}
        t_llm0, first, interrupted = time.time(), True, False
        self.speaking = True
        try:
            system = a.system_prompt + (language_instruction(self.lang) if (a.config or {}).get("multilingual") else "")
            msgs = list(self.history)
            if (a.config or {}).get("multilingual") and msgs and msgs[-1]["role"] == "user":
                # reminder right next to the caller's message: earlier replies in another language would otherwise win
                msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] + reply_language_note(self.lang)}
            async for sent in stream_reply(system, msgs, self.http):
                t_sent = time.time()
                if first:
                    m["llm_ms"] = int((t_sent - t_llm0) * 1000)
                    self.emit("stage", stage="llm", ms=m["llm_ms"])
                reply.append(sent)
                pcm = await synthesize(sent, self.lang, a.voice, self.http)
                if first:
                    m["tts_ms"] = _ms(t_sent)
                    m["total_ms"] = _ms(t_end) + settings.vad_silence_ms
                    self.emit("stage", stage="tts", ms=m["tts_ms"])
                    self.emit("stage", stage="total", ms=m["total_ms"])
                off = await self.send_audio(pcm)
                if first:
                    m["offset_ms"], first = off, False
        except asyncio.CancelledError:
            interrupted = True
            raise
        finally:
            self.speaking = False
            if reply:
                full = " ".join(reply)
                self.history.append({"role": "assistant", "content": full})
                log.info("AGENT: %s | %s", full, m)
                self.emit("agent_reply", text=full, interrupted=interrupted, **m)
                asyncio.create_task(self._save("assistant", full, interrupted=interrupted, **m))

    async def send_audio(self, pcm: bytes) -> int:
        start = self.rec.add_agent(pcm)
        for c in chunks(pcm):
            await self.ws.send_text(json.dumps({"event": "media", "stream_sid": self.stream_sid,
                                                "media": {"payload": b64(c)}}))
            await asyncio.sleep(0)   # yield so a barge-in cancel can land between chunks
        return start

    # ---------- persistence ----------
    async def _save(self, role, text, **fields):
        try:
            await self.call_ready                      # FK: call row must exist first
            async with Session() as s:
                s.add(Turn(call_id=self.call_id, role=role, text=text, **fields))
                await s.commit()
        except Exception:
            log.exception("save turn failed")
