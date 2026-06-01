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
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import duckdb

from . import config, palette, tshark
from .aggregate import aggregate
from .extract import extract_file, shard_path
from .names import load_names
from .vlans import load_vlans

_SCHEMA = Path(__file__).parent / "schema.sql"
# Accept the canonical capture extensions *and* split-capture suffixes such as
# `.pcap001` / `.pcapng07` produced when a large capture is chopped into capped-size
# chunks (each chunk still carries its own pcap global header). Trailing digits after
# a known base extension are treated as a split index.
_PCAP_RE = re.compile(r"^\.(pcapng|pcap|cap)\d*$", re.IGNORECASE)


def _is_capture(p: Path) -> bool:
    return bool(_PCAP_RE.match(p.suffix))

# Cleared (and shards removed) by --reset. protocols is re-dumped every run anyway.
# NOTE: the durable metadata tables (see _METADATA_TABLES) are intentionally NOT here --
# they persist across --reset, so given-names, a token's colour, the VLAN definitions, a
# category's colour, and the cached RDAP names all stay stable when captures are re-ingested.
_RESET_TABLES = (
    "connection_protocols", "connection_layers", "connections", "endpoints",
    "flow_layers", "flows", "protocols", "manifest",
)

# Durable metadata, normally PRESERVED across --reset (only lost if the DuckDB file is
# deleted). --reset-all ADDITIONALLY clears these so every table is rebuilt from its source
# this run: names/vlans from their CSVs, the colour registries by re-seeding, and ip_whois
# by re-resolving against RDAP. (Empties are skipped by their loaders, leaving them blank.)
_METADATA_TABLES = (
    "names", "vlans", "vlan_colours", "layer_colours", "ip_whois",
)


def connect() -> duckdb.DuckDBPyConnection:
    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    con.execute(_SCHEMA.read_text())
    return con


def discover(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.rglob("*") if _is_capture(p))


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
                    help="wipe analytical tables, manifest, and shards before ingest "
                         "(durable metadata is preserved)")
    ap.add_argument("--reset-all", action="store_true",
                    help="full reset: like --reset but ALSO wipe durable metadata (names, "
                         "vlans, colours, cached RDAP names) so every table is rebuilt from "
                         "its sources")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="skip extraction; re-aggregate existing shards")
    ap.add_argument("--no-whois", action="store_true",
                    help="skip RDAP/WHOIS name resolution of public IPs")
    args = ap.parse_args(argv)

    if not tshark.have_tshark():
        print("ERROR: tshark not found on PATH. Install Wireshark/tshark.", file=sys.stderr)
        return 2

    con = connect()

    if args.reset or args.reset_all:
        tables = _RESET_TABLES + (_METADATA_TABLES if args.reset_all else ())
        for t in tables:
            con.execute(f"DELETE FROM {t}")
        for shard in config.SCRATCH_DIR.glob("*.parquet"):
            shard.unlink()
        scope = "all tables (incl. metadata)" if args.reset_all else "tables"
        print(f"reset: cleared {scope} and shards")

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

    # Auto-load VLAN definitions if present, then seed/extend the category-colour
    # registry (the seeder needs the `vlans` rows to allocate, so load first).
    try:
        n = load_vlans(con, config.VLANS_CSV)
        print(f"vlans loaded: {n} (from {config.VLANS_CSV})")
    except FileNotFoundError:
        pass  # no vlans CSV; leave the (preserved) vlans table as-is
    added = palette.seed_vlan_colours(con)
    print(f"vlan colours: +{added} new (registry preserved across runs)")

    # Resolve public IPs to RDAP names (cached with a TTL). Network-bound and optional:
    # a failure here must never abort an otherwise-complete ingest.
    if config.WHOIS_ENABLED and not args.no_whois:
        try:
            from .whois import resolve_whois
            resolve_whois(con)
        except Exception as e:  # noqa: BLE001 -- best-effort enrichment, keep ingest alive
            print(f"WARNING: RDAP resolution failed: {e}", file=sys.stderr)

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
