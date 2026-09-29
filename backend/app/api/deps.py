from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.rate_limit import RateLimiterUnavailable, consume
from app.auth.security import AuthError, decode_access_token
from app.db.models import User
from app.db.session import get_db

# Reads 'Authorization: Bearer <token>'; auto_error=False lets us return our own 401.
bearer = HTTPBearer(auto_error=False)


# Dependency used by protected routes via Depends(get_current_user):
# verifies the bearer token, loads the user, then applies the per-user rate limit.
def get_current_user(
    # Depends(...) chains dependencies: FastAPI resolves bearer and get_db first.
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        # Decode/verify the token and extract the user id.
        user_id = decode_access_token(creds.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="Not authenticated") from exc
    # db.get: primary-key lookup.
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        # Rate limit is checked here so every authenticated endpoint gets it.
        allowed, retry_after = consume(str(user.id))
    except RateLimiterUnavailable as exc:
        # Redis down -> 503 (fail closed) rather than letting traffic through unlimited.
        raise HTTPException(status_code=503, detail="rate limiter unavailable") from exc
    if not allowed:
        # 429 Too Many Requests with a Retry-After header telling the client when to retry.
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(max(1, retry_after))},
        )
    return user
