import logging
import time
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db.session import create_sqlite_schema, engine

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "001_schema.sql"


def split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_sql_script() -> None:
    statements = split_sql(SCHEMA_PATH.read_text())
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def init_database() -> None:
    if get_settings().database_url.startswith("sqlite"):
        create_sqlite_schema()
        return
    last_error: Exception | None = None
    for attempt in range(30):
        try:
            apply_sql_script()
            logger.info("database schema ready")
            return
        except Exception as exc:
            last_error = exc
            logger.warning("database not ready (attempt %s): %s", attempt + 1, exc)
            time.sleep(1)
    assert last_error is not None
    raise last_error
