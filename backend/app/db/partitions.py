"""Hash-partition contract for tenant tables.

PostgreSQL routes rows with its own hash, so application code never picks a
physical partition. This module is the source of the child-table DDL that the
schema script must contain.
"""

PARTITION_MODULUS = 16

USER_SCOPED_TABLES = (
    "audio_files",
    "transcripts",
    "analyses",
    "prompt_configs",
    "rollup_summaries",
    "group_reports",
    "file_events",
    "chat_sessions",
    "chat_messages",
)


def partition_ddl(table: str, modulus: int = PARTITION_MODULUS) -> list[str]:
    if table not in USER_SCOPED_TABLES:
        raise ValueError(f"{table} is not a user-scoped table")
    return [
        (
            f"CREATE TABLE IF NOT EXISTS {table}_p{remainder} PARTITION OF {table} "
            f"FOR VALUES WITH (MODULUS {modulus}, REMAINDER {remainder})"
        )
        for remainder in range(modulus)
    ]
