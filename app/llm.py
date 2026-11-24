"""LLM layer: yields complete sentences as soon as they are ready so TTS can start early."""
import re

import httpx

from .config import settings

_SENT = re.compile(r"(.+?[.!?।]+)(\s+|$)", re.S)
_ac = None


def _split(buf: str):
    out = []
    while (m := _SENT.match(buf)):
        out.append(m.group(1).strip())
        buf = buf[m.end():]
    return out, buf


def _anthropic():
    global _ac
    if _ac is None:
        from anthropic import AsyncAnthropic
        _ac = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _ac


async def stream_reply(system: str, history: list[dict], client: httpx.AsyncClient):
    if settings.llm_provider == "sarvam":
        r = await client.post(
            "https://api.sarvam.ai/v1/chat/completions",
            headers={"api-subscription-key": settings.sarvam_api_key},
            json={"model": settings.sarvam_llm_model, "max_tokens": 300,
                  "messages": [{"role": "system", "content": system}, *history]},
            timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"].get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        sents, rest = _split(text + " ")
        for s in sents + ([rest.strip()] if rest.strip() else []):
            yield s
        return

    buf = ""
    async with _anthropic().messages.stream(model=settings.anthropic_model, max_tokens=300,
                                            system=system, messages=history) as st:
        async for delta in st.text_stream:
            buf += delta
            sents, buf = _split(buf)
            for s in sents:
                yield s
    if buf.strip():
        yield buf.strip()
