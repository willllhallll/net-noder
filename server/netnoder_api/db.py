"""Read-only DuckDB connection for serving queries.

A single shared connection is opened lazily; per-request cursors give safe
concurrent reads. Note DuckDB allows many read-only openers but no concurrent
read-write process, so don't run ingest against the same file while serving.
"""
import os
from pathlib import Path

import duckdb

DB_PATH = Path(os.environ.get("NETNODER_DB", "./data/netnoder.duckdb")).resolve()


def get_con() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH} not found")
    return duckdb.connect(str(DB_PATH), read_only=True)
