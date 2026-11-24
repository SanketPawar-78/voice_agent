"""Languages the agent can detect (Sarvam STT) and speak (Sarvam TTS bulbul:v3)."""

LANGS = {
    "en-IN": "English", "hi-IN": "Hindi", "mr-IN": "Marathi", "bn-IN": "Bengali",
    "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam",
    "gu-IN": "Gujarati", "pa-IN": "Punjabi", "od-IN": "Odia",
}


def language_instruction(code: str) -> str:
    name = LANGS.get(code, "English")
    return (
        "\n\n# Language (this turn)\n"
        f"The caller is speaking {name}. Reply ONLY in {name}"
        + ("" if code == "en-IN" else f", written in the native script of {name} (never in English letters)")
        + ". Keep the same short spoken style. If the caller switches language, follow them."
    )


def reply_language_note(code: str) -> str:
    """Appended to the caller's latest message (not stored in history)."""
    name = LANGS.get(code, "English")
    script = "" if code == "en-IN" else f" in {name} script"
    return f"\n\n[Reply in {name}{script} only.]"
