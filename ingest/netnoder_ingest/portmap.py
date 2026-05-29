"""Load the port -> service-description reference CSV into the `port_services` table.

Mirrors `names.py`: the registry is independent of packet ingest, so refresh it by
re-running `netnoder-portmap` (no re-aggregation needed). The CSV needs a header row;
extra columns beyond those used are ignored:

    port,transport,description,service_code
    25,tcp,Simple Mail Transfer,smtp
    443,tcp,http protocol over TLS/SSL,https

Keyed by (port, transport); duplicate keys collapse to a single description.
"""
import argparse
import sys
from pathlib import Path

import duckdb

from . import config
from .cli import connect


def _sqlstr(p: str) -> str:
    """Quote a path as a SQL string literal (parameterised paths misbehave)."""
    return "'" + p.replace("'", "''") + "'"


def load_port_services(
    con: duckdb.DuckDBPyConnection, path: Path, replace: bool = True
) -> int:
    """Load the port registry from a CSV (header: port,transport,description,...).

    Returns the row count after loading. Raises FileNotFoundError when the file is
    missing, leaving the table untouched (no destructive replace on a missing source).
    Rows without an integer port or a transport are skipped; duplicate (port,
    transport) pairs collapse to one description.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no portmap CSV at {path}")
    if replace:
        con.execute("DELETE FROM port_services")
    con.execute(f"""
        INSERT OR REPLACE INTO port_services
        SELECT TRY_CAST(trim(port) AS INTEGER) AS port,
               lower(trim(transport))          AS transport,
               any_value(trim(description))    AS description
        FROM read_csv({_sqlstr(str(path))}, header=true, all_varchar=true)
        WHERE TRY_CAST(trim(port) AS INTEGER) IS NOT NULL
          AND nullif(trim(transport), '') IS NOT NULL
        GROUP BY port, transport
    """)
    return con.execute("SELECT count(*) FROM port_services").fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-portmap",
        description="Load a port -> service CSV (header: port,transport,description) into the store.",
    )
    ap.add_argument(
        "path", nargs="?", default=str(config.PORTMAP_PATH),
        help="portmap CSV file (default: data/portmap/portmap.csv)",
    )
    ap.add_argument(
        "--merge", action="store_true",
        help="upsert into existing port_services instead of replacing the whole table",
    )
    args = ap.parse_args(argv)

    path = Path(args.path)
    con = connect()
    try:
        total = load_port_services(con, path, replace=not args.merge)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"port services loaded: {total} (from {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
