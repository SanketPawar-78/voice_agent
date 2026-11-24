import base64

import httpx

from .audio import wav_to_pcm8k
from .config import settings


async def synthesize(text: str, language: str, voice: str, client: httpx.AsyncClient) -> bytes:
    """Returns 8 kHz mono PCM ready to stream to Exotel."""
    r = await client.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={"api-subscription-key": settings.sarvam_api_key},
        json={"text": text, "target_language_code": language, "speaker": voice,
              "model": settings.sarvam_tts_model, "speech_sample_rate": 8000},
        timeout=20,
    )
    r.raise_for_status()
    return wav_to_pcm8k(base64.b64decode(r.json()["audios"][0]))


_cache: dict[tuple, bytes] = {}


async def cached_synthesize(text: str, language: str, voice: str, client: httpx.AsyncClient) -> bytes:
    """For fixed phrases (greetings). First call synthesizes, later calls are instant."""
    key = (text, language, voice)
    if key not in _cache:
        _cache[key] = await synthesize(text, language, voice, client)
    return _cache[key]
