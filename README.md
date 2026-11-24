# Voice Agent

```
Caller -> Exotel number -> Voicebot/Stream applet --(WSS, 8kHz PCM)--> /ws/exotel (this server)
                                                                          | VAD (webrtcvad: utterance + barge-in)
                                                                          v Sarvam STT (saarika)
Caller <-- audio chunks over same WSS <-- Sarvam TTS (bulbul) <-- LLM (Claude or Sarvam-m)
                         PostgreSQL: agents (config), calls, turns (+ per-turn latencies)
```

Added beyond your original list: barge-in (interrupt + Exotel `clear`), 8k resampling/chunking,
sentence-level LLM->TTS streaming for low latency, call-status webhook, outbound calls,
DB-driven agent config and call logs, API-key auth.

## Dashboard (open `http://localhost:8000/`, enter `API_KEY` once)
- **Live calls**: who is on the line and their state (listening / caller speaking / thinking / speaking).
- **Live event feed** (Server-Sent Events): every STT / LLM / TTS stage with its ms, caller + agent text, barge-ins.
- **Latency cards**: p50 / p95 total reply latency and average VAD, STT, LLM, TTS.
- **Call replay**: stereo recording (left = caller, right = agent), waveform with turn markers,
  click a waveform or transcript line to seek, per-reply latency bar split into VAD / STT / LLM / TTS.
- Reply latency = caller stops speaking -> first agent audio sent
  = VAD wait + STT + LLM (first sentence) + TTS (first sentence). Recordings are saved in `./recordings`.

## Run
1. `copy .env.example .env` and fill keys (Sarvam, Anthropic, Exotel).
2. `docker compose up --build` (Postgres + app on :8000).
3. Expose it: `ngrok http 8000`, then set `PUBLIC_HOST` to the ngrok host.
4. Exotel dashboard: create a flow with the **Voicebot/Stream** applet, URL
   `wss://<PUBLIC_HOST>/ws/exotel?agent=frontdesk`, attach it to your ExoPhone, call the number.
5. Outbound: `POST /api/calls/outbound` with header `X-API-Key` and body `{"to":"+91..."}`.
6. Review: `GET /api/calls`, `GET /api/calls/{id}`. Edit prompt/language/voice: `PUT /api/agents`.

Without Docker: run Postgres, set `DATABASE_URL`, `pip install -r requirements.txt`, `uvicorn app.main:app`.
