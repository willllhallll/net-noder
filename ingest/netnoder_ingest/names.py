"""Load user-curated IP -> given-name labels from a CSV into the `names` table.

Names are now part of the single DuckDB store (no separate API metadata DB). The CSV
is the source of truth: edit it and re-run `netnoder-names` (or just re-run
`netnoder-ingest`, which auto-loads it if present). The expected header is:

    ip,given_name
    192.168.1.10,Alice Laptop
    10.0.0.6,Database

`netnoder-ingest` preserves `names` across --reset, and this loader replaces the
table's contents from the CSV, so the file always wins.
"""
import argparse
import sys
from pathlib import Path

import duckdb

from . import config


def _sqlstr(p: str) -> str:
    return "'" + p.replace("'", "''") + "'"


def load_names(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Replace the `names` table contents from a CSV. Raises FileNotFoundError if absent."""
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    con.execute("DELETE FROM names")
    con.execute(
        f"""
        INSERT INTO names (ip, given_name)
        SELECT CAST(ip AS VARCHAR), CAST(given_name AS VARCHAR)
        FROM read_csv({_sqlstr(str(csv_path))}, header=true,
                      columns={{'ip': 'VARCHAR', 'given_name': 'VARCHAR'}})
        WHERE nullif(ip, '') IS NOT NULL
        """
    )
    return con.execute("SELECT count(*) FROM names").fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-names",
        description="Load IP given-names from a CSV (header: ip,given_name) into the store.",
    )
    ap.add_argument(
        "csv", nargs="?", default=str(config.NAMES_CSV),
        help=f"CSV file (default: {config.NAMES_CSV})",
    )
    args = ap.parse_args(argv)

    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    schema = (Path(__file__).parent / "schema.sql").read_text()
    con.execute(schema)
    try:
        n = load_names(con, Path(args.csv))
        print(f"names loaded: {n} (from {args.csv})")
    except FileNotFoundError:
        print(f"ERROR: names CSV not found: {args.csv}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
