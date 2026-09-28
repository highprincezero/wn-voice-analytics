from datetime import datetime

from pydantic import BaseModel, Field


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
    option_id: str
    params: dict = Field(default_factory=dict)


class PromptConfigRequest(BaseModel):
    selections: list[PromptSelection]


class PromptConfigResponse(BaseModel):
    selections: list[dict]


class RollupRequest(BaseModel):
    group_by: str = "user"
    time_from: datetime | None = None
    time_to: datetime | None = None
