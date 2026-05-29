"""`netnoder-ingest` command: discover pcaps, extract in parallel, aggregate.

Resumable: files already extracted (matching size+mtime in the manifest, with their
shard present) are skipped. Aggregation always rebuilds the rollup tables from all
current shards.
"""
import argparse
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import duckdb

from . import config, tshark
from .aggregate import aggregate
from .extract import extract_file, shard_path

_SCHEMA = Path(__file__).parent / "schema.sql"
_PCAP_EXTS = {".pcap", ".pcapng", ".cap"}


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
        for t in ("services", "conversations", "endpoints", "manifest"):
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

    # Refresh given-name labels if a names CSV dir is present (decoupled from packets).
    from .names import load_names  # lazy import: names.py imports this module

    try:
        n = load_names(con, config.NAMES_DIR)
        print(f"names loaded: {n} (from {config.NAMES_DIR})")
    except FileNotFoundError:
        pass  # no names provided; leave any existing names untouched

    e, c, s = con.execute(
        "SELECT (SELECT count(*) FROM endpoints), "
        "(SELECT count(*) FROM conversations), (SELECT count(*) FROM services)"
    ).fetchone()
    print(f"endpoints={e} conversations={c} services={s}")
    print(f"DB: {config.DB_PATH}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
