import os
import uuid
from pathlib import Path

import pytest

from app.db.init_db import split_sql
from app.db.partitions import PARTITION_MODULUS, USER_SCOPED_TABLES, partition_ddl
from app.storage.keys import (
    analysis_key,
    assert_user_key,
    audio_key,
    safe_extension,
    transcript_key,
)

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql"


def test_schema_hash_partitions_user_scoped_tables():
    sql = SCHEMA.read_text()
    assert sql.count("PARTITION BY HASH (user_id)") == len(USER_SCOPED_TABLES)
    assert "CREATE TABLE IF NOT EXISTS users (" in sql
    users_body = sql.split("CREATE TABLE IF NOT EXISTS audio_files", 1)[0]
    assert "PARTITION BY HASH" not in users_body
    for table in USER_SCOPED_TABLES:
        start = sql.index(f"CREATE TABLE IF NOT EXISTS {table} (")
        end = sql.index("PARTITION BY HASH (user_id);", start)
        body = sql[start:end]
        assert "PRIMARY KEY (user_id" in body
        for statement in partition_ddl(table, PARTITION_MODULUS):
            assert statement + ";" in sql
    assert len(split_sql(sql)) > 80


def test_storage_keys_are_partitioned_by_user():
    user_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    file_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    assert audio_key(user_id, file_id, "wav") == f"users/{user_id}/audio/{file_id}.wav"
    assert transcript_key(user_id, file_id) == f"users/{user_id}/transcripts/{file_id}.json"
    assert analysis_key(user_id, file_id) == f"users/{user_id}/analysis/{file_id}.json"
    assert safe_extension("folder/call.WAVE") == "wav"
    with pytest.raises(ValueError):
        safe_extension("payload.exe")
    with pytest.raises(ValueError):
        audio_key(user_id, file_id, "../secret")
    assert_user_key(user_id, audio_key(user_id, file_id, "wav"))
    with pytest.raises(PermissionError):
        assert_user_key(user_id, f"users/{uuid.uuid4()}/audio/{file_id}.wav")
    with pytest.raises(PermissionError):
        assert_user_key(user_id, f"users/{user_id}/../audio/{file_id}.wav")


@pytest.mark.skipif(not os.getenv("POSTGRES_TEST_URL"), reason="postgres not configured")
def test_schema_applies_and_routes_rows_to_multiple_partitions():
    import psycopg

    url = os.environ["POSTGRES_TEST_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        statements = split_sql(SCHEMA.read_text())
        for statement in statements:
            conn.execute(statement)
        for statement in statements:
            conn.execute(statement)
        for index in range(32):
            user_id = uuid.uuid4()
            file_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO users (id, email, password_hash, home_region, created_at) "
                "VALUES (%s, %s, %s, %s, NOW())",
                (user_id, f"user{index}@example.com", "hash", "local"),
            )
            conn.execute(
                "INSERT INTO audio_files "
                "(user_id, id, original_filename, content_type, byte_size, sha256, "
                "storage_key, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                (
                    user_id,
                    file_id,
                    "a.wav",
                    "audio/wav",
                    10,
                    "a" * 64,
                    f"users/{user_id}/audio/{file_id}.wav",
                    "uploaded",
                ),
            )
        count = conn.execute("SELECT count(*) FROM audio_files").fetchone()[0]
        partitions = conn.execute("SELECT count(DISTINCT tableoid) FROM audio_files").fetchone()[0]
    assert count == 32
    assert partitions >= 2
