"""Read-only DuckDB connection for serving queries.

There is a single store (NETNODER_DB): the analytical tables plus `names` and
`layer_colours`, all built by ingest. The API opens it **read-only** -- given-names
come from names.csv via `netnoder-names`, and colours are seeded at ingest, so the
API never writes. DuckDB allows many read-only openers but only one read-write
process, so don't run ingest against the same file while the API is serving it.
"""
import os
from pathlib import Path

import duckdb

DB_PATH = Path(os.environ.get("NETNODER_DB", "./data/netnoder.duckdb")).resolve()


def get_con() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH} not found -- run ingest first")
    return duckdb.connect(str(DB_PATH), read_only=True)
