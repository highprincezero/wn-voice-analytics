from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth.security import (
    AuthError,
    create_access_token,
    hash_password,
    normalize_email,
    verify_password,
)
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.schemas.api import LoginRequest, SignupRequest, TokenResponse, UserResponse
from app.timeutil import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        user_id=str(user.id),
        email=user.email,
    )


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        email = normalize_email(body.email)
        password_hash = hash_password(body.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = User(
        email=email,
        password_hash=password_hash,
        home_region=get_settings().home_region,
        created_at=utcnow(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="email already registered") from exc
    db.refresh(user)
    return _token(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        email = normalize_email(body.email)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="invalid credentials") from exc
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return _token(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        home_region=user.home_region,
        created_at=user.created_at,
    )
