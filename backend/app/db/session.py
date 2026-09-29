from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base


# Engine = connection pool to the database (Postgres in Compose, SQLite in tests).
def _build_engine():
    url = get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # SQLite (tests): StaticPool reuses one connection, usable across threads.
        from sqlalchemy.pool import StaticPool

        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    else:
        # pool_pre_ping checks a pooled connection is alive before using it.
        kwargs["pool_pre_ping"] = True
    return create_engine(url, **kwargs)


engine = _build_engine()
# Session factory: SessionLocal() returns a new session; expire_on_commit=False keeps
# loaded attributes usable after commit.
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


# SQLAlchemy event hook: runs on each new DB connection (enables FKs for SQLite).
@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if get_settings().database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# FastAPI dependency: `yield` hands a session to the request handler, and the
# finally block closes it after the response, even if the handler raised.
def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_sqlite_schema() -> None:
    Base.metadata.create_all(bind=engine)
