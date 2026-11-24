import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    """Per-agent configuration (prompt, language, voice). Editable via API, no redeploy."""
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    system_prompt: Mapped[str] = mapped_column(Text)
    greeting: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="en-IN")  # BCP-47 e.g. hi-IN
    voice: Mapped[str] = mapped_column(String(50), default="anushka")   # Sarvam speaker
    config: Mapped[dict] = mapped_column(JSONB, default=dict)           # free-form extras
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    stream_sid: Mapped[str | None] = mapped_column(String(64))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    direction: Mapped[str] = mapped_column(String(10), default="inbound")
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="started")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    recording_path: Mapped[str | None] = mapped_column(String(300))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    turns: Mapped[list["Turn"]] = relationship(back_populates="call", order_by="Turn.id")


class Turn(Base):
    __tablename__ = "turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    text: Mapped[str] = mapped_column(Text)
    # Latency breakdown (ms). total_ms = caller stopped speaking -> first agent audio sent
    # = vad_ms (end-of-speech silence) + stt_ms + llm_ms (first sentence) + tts_ms (first sentence)
    stt_ms: Mapped[int | None] = mapped_column(Integer)
    vad_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ms: Mapped[int | None] = mapped_column(Integer)
    tts_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    offset_ms: Mapped[int | None] = mapped_column(Integer)  # position in the call recording
    interrupted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    call: Mapped[Call] = relationship(back_populates="turns")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy import select
    from .prompts import AGENTS
    async with Session() as s:
        for spec in AGENTS:
            a = (await s.execute(select(Agent).where(Agent.name == spec["name"]))).scalar()
            if not a:
                s.add(Agent(**spec))
            elif not (a.config or {}).get("locked"):   # code is the source of truth until edited via API
                for k, v in spec.items():
                    setattr(a, k, v)
        await s.commit()
