"""Load user-curated VLAN definitions from a CSV into the `vlans` table.

VLANs are part of the single DuckDB store (alongside names + layer_colours). The CSV
is the source of truth: edit it and re-run `netnoder-vlans` (or just re-run
`netnoder-ingest`, which auto-loads it if present). The expected header is:

    vlan_id,base_ip,subnet_mask
    200,10.200.0.0,255.255.255.0

with an OPTIONAL 4th `label` column for a friendly name:

    vlan_id,base_ip,subnet_mask,label
    200,10.200.0.0,255.255.255.0,VLAN 200 — Servers

3-column files still work, with `label` left NULL (the query layer falls back to
`VLAN <id>`). `netnoder-ingest` preserves `vlans` across --reset, and this loader
replaces the table's contents from the CSV, so the file always wins. Endpoints are
classified by subnet membership of their IP against these ranges at query time.
"""
import argparse
import sys
from pathlib import Path

import duckdb

from . import config


def _sqlstr(p: str) -> str:
    return "'" + p.replace("'", "''") + "'"


def load_vlans(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Replace the `vlans` table contents from a CSV. Raises FileNotFoundError if absent.

    The `label` column is OPTIONAL: we sniff the header columns first and substitute a
    NULL literal when it is absent, so both 3- and 4-column files load cleanly.
    """
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    src = _sqlstr(str(csv_path))
    cols = [
        r[0]
        for r in con.execute(
            f"DESCRIBE SELECT * FROM read_csv({src}, header=true)"
        ).fetchall()
    ]
    label_expr = "label" if "label" in cols else "NULL"
    con.execute("DELETE FROM vlans")
    con.execute(
        f"""
        INSERT INTO vlans (vlan_id, base_ip, subnet_mask, label)
        SELECT CAST(vlan_id AS INTEGER), CAST(base_ip AS VARCHAR),
               CAST(subnet_mask AS VARCHAR), CAST({label_expr} AS VARCHAR)
        FROM read_csv({src}, header=true)
        WHERE nullif(CAST(base_ip AS VARCHAR), '') IS NOT NULL
        """
    )
    return con.execute("SELECT count(*) FROM vlans").fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-vlans",
        description=(
            "Load VLAN definitions from a CSV "
            "(header: vlan_id,base_ip,subnet_mask[,label]) into the store."
        ),
    )
    ap.add_argument(
        "csv", nargs="?", default=str(config.VLANS_CSV),
        help=f"CSV file (default: {config.VLANS_CSV})",
    )
    args = ap.parse_args(argv)

    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    schema = (Path(__file__).parent / "schema.sql").read_text()
    con.execute(schema)
    try:
        n = load_vlans(con, Path(args.csv))
        from . import palette
        added = palette.seed_broadcast_domain_colours(con)
        print(f"vlans loaded: {n} (from {args.csv}); broadcast domain colours +{added}")
    except FileNotFoundError:
        print(f"ERROR: vlans CSV not found: {args.csv}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
