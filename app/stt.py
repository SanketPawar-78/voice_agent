import httpx

from .audio import wav_bytes
from .config import settings


async def transcribe(pcm: bytes, language: str, client: httpx.AsyncClient) -> tuple[str, str | None]:
    """Returns (text, detected_language_code). Pass language='unknown' to auto-detect."""
    r = await client.post(
        "https://api.sarvam.ai/speech-to-text",
        headers={"api-subscription-key": settings.sarvam_api_key},
        files={"file": ("utt.wav", wav_bytes(pcm), "audio/wav")},
        data={"model": settings.sarvam_stt_model, "language_code": language},
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()
    return (j.get("transcript") or "").strip(), j.get("language_code")
