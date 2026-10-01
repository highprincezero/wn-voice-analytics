from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: str
    email: str
    home_region: str
    created_at: datetime


class PromptSelection(BaseModel):
    # Extra keys are an error (HTTP 422), never silently dropped or passed on.
    model_config = ConfigDict(extra="forbid")

    option_id: str
    params: dict = Field(default_factory=dict)


class PromptConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[PromptSelection]


class PromptConfigResponse(BaseModel):
    selections: list[dict]


class ReportRequest(BaseModel):
    """All groupings report. Omit groupings for every grouping in the whitelist."""

    model_config = ConfigDict(extra="forbid")

    groupings: list[str] | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None


class RollupRequest(BaseModel):
    group_by: str = "user"
    template_id: str | None = None
    slot: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
