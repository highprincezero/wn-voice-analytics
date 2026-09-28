import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import json_doc
from app.timeutil import utcnow

_STATUSES = ("uploaded", "processing", "completed", "blocked", "failed")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    home_region: Mapped[str] = mapped_column(String(64), default="local")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class AudioFile(Base):
    __tablename__ = "audio_files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'completed', 'blocked', 'failed')",
            name="audio_files_status_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    stage: Mapped[str] = mapped_column(String(32), default="upload")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="transcripts_file_uq"),
        ForeignKeyConstraint(
            ["user_id", "file_id"],
            ["audio_files.user_id", "audio_files.id"],
            ondelete="CASCADE",
            name="transcripts_file_fk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    text: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="analyses_file_uq"),
        ForeignKeyConstraint(
            ["user_id", "file_id"],
            ["audio_files.user_id", "audio_files.id"],
            ondelete="CASCADE",
            name="analyses_file_fk",
        ),
        CheckConstraint(
            "status IN ('completed', 'blocked', 'failed')",
            name="analyses_status_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32))
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    taxonomy: Mapped[dict | None] = mapped_column(json_doc(), nullable=True)
    layer2: Mapped[dict | None] = mapped_column(json_doc(), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    storage_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class PromptConfig(Base):
    __tablename__ = "prompt_configs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    selections: Mapped[list] = mapped_column(json_doc())
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class RollupSummary(Base):
    __tablename__ = "rollup_summaries"
    __table_args__ = (
        CheckConstraint(
            "group_by IN ('user', 'taxonomy_label', 'week', 'sentiment')",
            name="rollup_summaries_group_chk",
        ),
        CheckConstraint(
            "trigger IN ('schedule', 'on_demand')",
            name="rollup_summaries_trigger_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_by: Mapped[str] = mapped_column(String(64))
    time_from: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    time_to: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(json_doc())
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class FileEvent(Base):
    __tablename__ = "file_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "file_id"],
            ["audio_files.user_id", "audio_files.id"],
            ondelete="CASCADE",
            name="file_events_file_fk",
        ),
        CheckConstraint(
            "level IN ('info', 'error')",
            name="file_events_level_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    filename: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(BigInteger)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


ALLOWED_FILE_STATUSES = _STATUSES
PIPELINE_STAGES = ("upload", "queued", "transcribe", "safety", "layer1", "layer2", "saved")
