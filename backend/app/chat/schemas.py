import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SearchFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: datetime | None = None
    date_to: datetime | None = None
    min_duration: float | None = Field(default=None, ge=0, le=86400)
    max_duration: float | None = Field(default=None, ge=0, le=86400)
    taxonomy: str | None = Field(default=None, max_length=80)


class GetAnalysisArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: uuid.UUID


class ProfileSpeakerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: uuid.UUID | None = None


class RunSummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: Literal["user", "taxonomy_label", "day", "week", "month", "sentiment"]
    time_from: datetime | None = None
    time_to: datetime | None = None


class ChatReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=4000)
