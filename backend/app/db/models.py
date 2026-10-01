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


# Declarative model: subclassing Base maps this class to the `users` table.
# users is NOT partitioned, so email stays unique across the whole region.
class User(Base):
    __tablename__ = "users"

    # SQLAlchemy 2.0 style: Mapped[...] is the Python type, mapped_column() the DB column.
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # unique=True + index=True: one account per email, fast login lookup.
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    home_region: Mapped[str] = mapped_column(String(64), default="local")
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


# Tenant table: in Postgres it is hash-partitioned by user_id (16 partitions, see
# sql/001_schema.sql and db/partitions.py). ORM code queries the parent table as usual.
class AudioFile(Base):
    __tablename__ = "audio_files"
    # __table_args__: table-level constraints (here a CHECK on allowed status values).
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'completed', 'blocked', 'failed')",
            name="audio_files_status_chk",
        ),
    )

    # Composite primary key (user_id, id): a partitioned table's PK must include the
    # partition key. ondelete=CASCADE removes a user's rows when the user is deleted.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # default=uuid.uuid4 is a Python-side default applied on INSERT.
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    # `float | None` + nullable=True: optional column (filled in after analysis).
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    stage: Mapped[str] = mapped_column(String(32), default="upload")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"
    # One transcript per file (unique on user_id + file_id).
    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="transcripts_file_uq"),
        # Composite foreign key to audio_files' composite PK; deleting the file cascades here.
        # No relationship() is defined - the code joins explicitly by (user_id, file_id).
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
    # json_doc(): JSONB on Postgres, plain JSON on SQLite (see db/types.py).
    taxonomy: Mapped[dict | None] = mapped_column(json_doc(), nullable=True)
    layer2: Mapped[dict | None] = mapped_column(json_doc(), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    storage_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class PromptConfig(Base):
    __tablename__ = "prompt_configs"

    # One prompt config per user, so user_id alone is the primary key.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    selections: Mapped[list] = mapped_column(json_doc())
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


class RollupSummary(Base):
    __tablename__ = "rollup_summaries"
    __table_args__ = (
        CheckConstraint(
            "group_by IN ('user', 'taxonomy_label', 'day', 'week', 'month', 'sentiment')",
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


# One "All groupings" report: every grouping over the user's files, built by a background job.
class GroupReport(Base):
    __tablename__ = "group_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="group_reports_status_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    groupings: Mapped[list] = mapped_column(json_doc())
    time_from: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    time_to: Mapped[object | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict | None] = mapped_column(json_doc(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[object | None] = mapped_column(DateTime, nullable=True)


# Append-only progress log per file (what the UI timeline shows).
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
    # seq orders events within a file.
    seq: Mapped[int] = mapped_column(BigInteger)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


# One chat for this account. Hash-partitioned by user_id, same as the other tenant tables.
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


# Questions and answers for one chat. seq is the order inside the session.
class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["chat_sessions.user_id", "chat_sessions.id"],
            ondelete="CASCADE",
            name="chat_messages_session_fk",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="chat_messages_role_chk",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
