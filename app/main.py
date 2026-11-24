import asyncio
import json
import logging
import os
import secrets
import statistics
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from .config import settings
from .db import Agent, Call, Session, Turn, init_db
from .events import hub
from .exotel import make_outbound_call
from .session import CallSession

logging.basicConfig(level=logging.INFO)
STATIC = os.path.join(os.path.dirname(__file__), "static")


async def warm_greetings():
    """Pre-generate greeting audio so calls start speaking instantly."""
    from .session import HTTP
    from .tts import cached_synthesize
    async with Session() as s:
        agents_ = (await s.execute(select(Agent))).scalars().all()
    for a in agents_:
        try:
            await cached_synthesize(a.greeting, a.language, a.voice, HTTP)
        except Exception as e:
            logging.warning("greeting warm-up failed for %s: %r", a.name, e)


@asynccontextmanager
async def lifespan(app):
    await init_db()
    asyncio.create_task(warm_greetings())
    yield


app = FastAPI(title="Voice Agent", lifespan=lifespan)


def auth(x_api_key: str = Header(default=""), key: str = ""):
    # header for API clients; ?key= for EventSource / <audio> which cannot set headers
    if not secrets.compare_digest(x_api_key or key, settings.api_key):
        raise HTTPException(401, "bad api key")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def dashboard():
    return FileResponse(os.path.join(STATIC, "index.html"))


# Exotel Voicebot/Stream applet connects here (set this wss URL in the applet)
@app.websocket("/ws/exotel")
async def exotel_ws(ws: WebSocket, agent: str = "frontdesk", direction: str = "inbound"):
    await ws.accept()
    await CallSession(ws, agent, direction).run()


# Exotel call status webhook
@app.post("/exotel/status")
async def exotel_status(request: Request):
    form = dict(await request.form())
    async with Session() as s:
        call = (await s.execute(select(Call).where(Call.call_sid == form.get("CallSid")))).scalar()
        if call:
            call.status = form.get("Status", call.status)
            call.meta = {**(call.meta or {}), "status_callback": form}
            await s.commit()
    return {"ok": True}


# ---- Live events (Server-Sent Events) ----
@app.get("/api/stream", dependencies=[Depends(auth)])
async def stream(request: Request):
    q = hub.subscribe()

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'snapshot', 'active': list(hub.active.values())})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(msg, default=str)}\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- Management API ----
class OutboundReq(BaseModel):
    to: str
    agent: str = "frontdesk"


@app.post("/api/calls/outbound", dependencies=[Depends(auth)])
async def outbound(req: OutboundReq):
    return await make_outbound_call(req.to, req.agent)


def _call_json(c: Call, avg_total=None):
    return {"id": str(c.id), "call_sid": c.call_sid, "direction": c.direction, "from": c.from_number,
            "to": c.to_number, "status": c.status, "started_at": c.started_at, "duration_s": c.duration_s,
            "has_recording": bool(c.recording_path), "avg_total_ms": avg_total}


@app.get("/api/calls", dependencies=[Depends(auth)])
async def list_calls(limit: int = 50):
    async with Session() as s:
        calls = (await s.execute(select(Call).order_by(Call.started_at.desc()).limit(limit))).scalars().all()
        avgs = dict((await s.execute(
            select(Turn.call_id, func.avg(Turn.total_ms)).where(Turn.call_id.in_([c.id for c in calls]))
            .group_by(Turn.call_id))).all())
        return [_call_json(c, int(avgs[c.id]) if avgs.get(c.id) is not None else None) for c in calls]


@app.get("/api/calls/{call_id}", dependencies=[Depends(auth)])
async def get_call(call_id: str):
    async with Session() as s:
        call = await s.get(Call, call_id)
        if not call:
            raise HTTPException(404)
        turns = (await s.execute(select(Turn).where(Turn.call_id == call.id).order_by(Turn.id))).scalars()
        return {"call": _call_json(call), "turns": [
            {"role": t.role, "text": t.text, "stt_ms": t.stt_ms, "vad_ms": t.vad_ms, "llm_ms": t.llm_ms,
             "tts_ms": t.tts_ms, "total_ms": t.total_ms, "offset_ms": t.offset_ms,
             "interrupted": t.interrupted, "at": t.created_at} for t in turns]}


@app.get("/api/calls/{call_id}/recording", dependencies=[Depends(auth)])
async def recording(call_id: str):
    async with Session() as s:
        call = await s.get(Call, call_id)
    if not call or not call.recording_path or not os.path.exists(call.recording_path):
        raise HTTPException(404, "no recording")
    return FileResponse(call.recording_path, media_type="audio/wav")


def _pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(len(vals) * p))] if vals else None


@app.get("/api/stats", dependencies=[Depends(auth)])
async def stats(limit: int = 500):
    """Latency summary over the most recent agent replies."""
    async with Session() as s:
        rows = (await s.execute(select(Turn).where(Turn.role == "assistant", Turn.total_ms.is_not(None))
                                .order_by(Turn.id.desc()).limit(limit))).scalars().all()
        total_calls = (await s.execute(select(func.count()).select_from(Call))).scalar()
    out = {"calls": total_calls, "active": len(hub.active), "replies": len(rows)}
    for f in ("total_ms", "stt_ms", "vad_ms", "llm_ms", "tts_ms"):
        v = [getattr(r, f) for r in rows if getattr(r, f) is not None]
        out[f] = {"avg": int(statistics.mean(v)) if v else None, "p50": _pct(v, .5), "p95": _pct(v, .95)}
    return out


class AgentIn(BaseModel):
    name: str
    system_prompt: str
    greeting: str
    language: str = "en-IN"
    voice: str = "anushka"


@app.get("/api/agents", dependencies=[Depends(auth)])
async def agents():
    async with Session() as s:
        return [{"name": a.name, "language": a.language, "voice": a.voice, "greeting": a.greeting,
                 "system_prompt": a.system_prompt} for a in (await s.execute(select(Agent))).scalars()]


@app.put("/api/agents", dependencies=[Depends(auth)])
async def upsert_agent(body: AgentIn):
    async with Session() as s:
        a = (await s.execute(select(Agent).where(Agent.name == body.name))).scalar() or Agent(name=body.name)
        for k, v in body.model_dump().items():
            setattr(a, k, v)
        a.config = {**(a.config or {}), "locked": True}   # API edits win over prompts.py
        s.add(a)
        await s.commit()
    asyncio.create_task(warm_greetings())
    return {"ok": True}
