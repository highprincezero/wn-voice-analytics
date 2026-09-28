from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB


def json_doc():
    return JSON().with_variant(JSONB(), "postgresql")
