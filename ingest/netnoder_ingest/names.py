"""Load user-curated IP -> given-name CSVs into the `names` table.

The mapping is independent of packet ingest: edit the CSV(s) and re-run
`netnoder-names` (no re-aggregation needed). Point it at a single CSV file or a
directory of CSVs (default: data/names/). Each CSV needs a header row:

    ip,given_name
    192.168.1.1,Gateway
    192.168.1.10,Laptop
"""
import argparse
import sys
from pathlib import Path

import duckdb

from . import config
from .cli import connect


def _sqlstr(p: str) -> str:
    """Quote a path/glob as a SQL string literal (parameterised paths misbehave)."""
    return "'" + p.replace("'", "''") + "'"


def _read_target(path: Path) -> str | None:
    """Resolve a file or directory into a read_csv target (a *.csv glob for dirs)."""
    if path.is_dir():
        if not any(path.glob("*.csv")):
            return None
        return str(path / "*.csv")
    if path.is_file():
        return str(path)
    return None


def load_names(con: duckdb.DuckDBPyConnection, path: Path, replace: bool = True) -> int:
    """Load names from a CSV file or a directory of CSVs (header: ip,given_name).

    Returns the row count after loading. Raises FileNotFoundError when no CSV exists,
    leaving the table untouched (no destructive replace on a missing source).
    """
    target = _read_target(path)
    if target is None:
        raise FileNotFoundError(f"no .csv files found at {path}")
    if replace:
        con.execute("DELETE FROM names")
    con.execute(f"""
        INSERT OR REPLACE INTO names
        SELECT trim(ip), any_value(trim(given_name))
        FROM read_csv({_sqlstr(target)}, header=true, all_varchar=true)
        WHERE nullif(trim(ip), '') IS NOT NULL
          AND nullif(trim(given_name), '') IS NOT NULL
        GROUP BY trim(ip)
    """)
    return con.execute("SELECT count(*) FROM names").fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-names",
        description="Load IP -> given-name CSV(s) (header: ip,given_name) into the store.",
    )
    ap.add_argument(
        "path", nargs="?", default=str(config.NAMES_DIR),
        help="CSV file or a directory of CSVs (default: data/names/)",
    )
    ap.add_argument(
        "--merge", action="store_true",
        help="upsert into existing names instead of replacing the whole table",
    )
    args = ap.parse_args(argv)

    path = Path(args.path)
    con = connect()
    try:
        total = load_names(con, path, replace=not args.merge)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    print(f"names loaded: {total} (from {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
