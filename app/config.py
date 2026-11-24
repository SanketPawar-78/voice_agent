from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    public_host: str = "localhost:8000"
    api_key: str = "change-me"
    database_url: str = "postgresql+asyncpg://voice:voice@localhost:5432/voiceagent"

    exotel_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_subdomain: str = "api.in.exotel.com"
    exotel_number: str = ""
    exotel_flow_app_id: str = ""

    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saarika:v2.5"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_llm_model: str = "sarvam-105b-conversations"

    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-5-5"

    owner_name: str = "the owner"
    assistant_name: str = "Aria"

    recordings_dir: str = "recordings"

    vad_aggressiveness: int = 2
    vad_silence_ms: int = 700
    vad_min_speech_ms: int = 200


settings = Settings()
