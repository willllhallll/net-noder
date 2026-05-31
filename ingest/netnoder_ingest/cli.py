"""`netnoder-ingest` command: discover pcaps, extract in parallel, aggregate.

Resumable: files already extracted (matching size+mtime in the manifest, with their
shard present) are skipped. Aggregation always rebuilds the rollup tables from all
current shards. After aggregation the authoritative `tshark -G protocols` reference
list is persisted into the `protocols` table.

There is one DuckDB store. After aggregation we persist the `tshark -G protocols`
reference, seed/extend the `layer_colours` registry, and auto-load given-names from
the names CSV if present. The IANA port map is gone entirely -- the peer/dissector
model takes protocols straight from the dissector.
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import duckdb

from . import config, palette, tshark
from .aggregate import aggregate
from .extract import extract_file, shard_path
from .names import load_names

_SCHEMA = Path(__file__).parent / "schema.sql"
_PCAP_EXTS = {".pcap", ".pcapng", ".cap"}

# Cleared (and shards removed) by --reset. protocols is re-dumped every run anyway.
# NOTE: `names` and `layer_colours` are intentionally NOT here -- they are durable
# metadata that persists across --reset (only lost if the DuckDB file is deleted),
# so given-names and a token's colour stay stable.
_RESET_TABLES = (
    "connection_protocols", "connection_layers", "connections", "endpoints",
    "flow_layers", "flows", "protocols", "manifest",
)


def connect() -> duckdb.DuckDBPyConnection:
    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(_SCHEMA.read_text())
    return con


def discover(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.rglob("*") if p.suffix.lower() in _PCAP_EXTS)


def needs_extract(con: duckdb.DuckDBPyConnection, pcap: Path) -> bool:
    row = con.execute(
        "SELECT status, size, mtime FROM manifest WHERE path=?", [str(pcap.resolve())]
    ).fetchone()
    if not row:
        return True
    status, size, mtime = row
    if status != "done":
        return True
    st = pcap.stat()
    if size != st.st_size or abs((mtime or 0.0) - st.st_mtime) > 1e-3:
        return True
    return not shard_path(pcap).exists()


def _upsert_manifest(con: duckdb.DuckDBPyConnection, r: dict) -> None:
    con.execute(
        """INSERT OR REPLACE INTO manifest(path, size, mtime, status, rows, shard, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, now())""",
        [r["path"], r["size"], r["mtime"], r["status"], r["rows"], r["shard"]],
    )


def _persist_protocols(con: duckdb.DuckDBPyConnection) -> int:
    """Dump `tshark -G protocols` into the reference table (validation / tier seeding)."""
    rows = tshark.dump_protocols()
    con.execute("DELETE FROM protocols")
    if rows:
        con.executemany("INSERT OR REPLACE INTO protocols VALUES (?, ?, ?)", rows)
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-ingest",
        description="Ingest pcap files into the net-noder DuckDB store.",
    )
    ap.add_argument("input", help="pcap file or a directory to scan recursively")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4,
                    help="parallel extraction workers (default: all cores)")
    ap.add_argument("-m", "--memory", default="4GB",
                    help="DuckDB memory limit for aggregation (default: 4GB)")
    ap.add_argument("--reset", action="store_true",
                    help="wipe tables, manifest, and shards before ingest")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="skip extraction; re-aggregate existing shards")
    args = ap.parse_args(argv)

    if not tshark.have_tshark():
        print("ERROR: tshark not found on PATH. Install Wireshark/tshark.", file=sys.stderr)
        return 2

    con = connect()

    if args.reset:
        for t in _RESET_TABLES:
            con.execute(f"DELETE FROM {t}")
        for shard in config.SCRATCH_DIR.glob("*.parquet"):
            shard.unlink()
        print("reset: cleared tables and shards")

    if not args.aggregate_only:
        files = discover(Path(args.input))
        if not files:
            print(f"No pcap files found under {args.input}")
            return 1
        todo = [f for f in files if needs_extract(con, f)]
        print(f"{len(files)} pcap(s); {len(todo)} to extract, {len(files) - len(todo)} cached")
        if todo:
            t0 = time.time()
            paths = [str(f) for f in todo]
            with Pool(processes=max(1, args.jobs)) as pool:
                for i, r in enumerate(pool.imap_unordered(extract_file, paths), 1):
                    _upsert_manifest(con, r)
                    tag = "ok " if r["status"] == "done" else "ERR"
                    extra = f" :: {r['error']}" if r["error"] else ""
                    print(f"[{i}/{len(todo)}] {tag} {Path(r['path']).name} rows={r['rows']}{extra}")
            print(f"extraction done in {time.time() - t0:.1f}s")

    print("aggregating ...")
    t0 = time.time()
    aggregate(con, memory=args.memory, threads=args.jobs)
    print(f"aggregation done in {time.time() - t0:.1f}s")

    # Persist the authoritative protocol/abbrev reference list (never drives colour).
    try:
        n = _persist_protocols(con)
        print(f"protocols reference: {n} entries from `tshark -G protocols`")
    except RuntimeError as e:
        print(f"WARNING: could not dump protocols reference: {e}", file=sys.stderr)

    # Seed/extend the persisted layer-colour registry (first-seen-wins).
    added = palette.seed_layer_colours(con)
    print(f"layer colours: +{added} new (registry preserved across runs)")

    # Auto-load given-names from the CSV if present (the CSV is the source of truth).
    try:
        n = load_names(con, config.NAMES_CSV)
        print(f"names loaded: {n} (from {config.NAMES_CSV})")
    except FileNotFoundError:
        pass  # no names CSV; leave the (preserved) names table as-is

    e, c, f = con.execute(
        "SELECT (SELECT count(*) FROM endpoints), "
        "(SELECT count(*) FROM connections), (SELECT count(*) FROM flows)"
    ).fetchone()
    print(f"endpoints={e} connections={c} flows={f}")
    print(f"DB: {config.DB_PATH}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
