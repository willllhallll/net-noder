"""Per-file extraction: tshark -> transient TSV -> flow-grain parquet shard.

This is the *combine* stage of the map/combine/reduce pipeline. Each worker runs in
its own process with its own in-memory DuckDB connection, reads the per-packet TSV,
and pre-aggregates it down to **flow grain** before writing one compact parquet
shard. Collapsing millions of packets into a handful of flow rows here -- per small
file, in bounded RAM -- is the single biggest lever keeping the global merge cheap.

Canonical 5-tuple: ``ip_a <= ip_b`` and ``port_a`` always belongs to ``ip_a``. A
packet is in the ``a2b`` direction when its source is ``ip_a``. There is NO role,
NO ephemeral filtering, and NO port cap -- every real port-pair becomes its own row.

The deepest ``frame.protocols`` stack seen for a 5-tuple is carried on the shard via
``arg_max(stack, depth)``; ``depth`` rides along so the global merge can pick the
deepest stack across files. The transient TSV is deleted immediately, so peak extra
disk is bounded by the largest single pcap, never the whole corpus.
"""
import hashlib
from pathlib import Path

import duckdb

from . import config, tshark
from .transform import PROTO_SQL

# All 13 tshark columns (c0..c12) read as VARCHAR, then normalised below.
_COLUMNS = "{" + ", ".join(f"'c{i}': 'VARCHAR'" for i in range(13)) + "}"
_TAB = "\t"  # interpolated as a real tab so DuckDB's delim is unambiguous


def _sqlstr(p: str) -> str:
    """Quote a path as a SQL string literal (paths are internal, but be safe)."""
    return "'" + p.replace("'", "''") + "'"


def _extract_sql(tsv: Path, shard: Path) -> str:
    # pkt: one row per packet, normalised (IPv4/IPv6 unified, ports unified).
    # canon: canonicalise to ip_a<=ip_b, attribute the packet to a2b/b2a, and
    #        compute the L4 token + stack depth. Then GROUP BY the 5-tuple.
    return f"""
COPY (
  WITH pkt AS (
    SELECT
      TRY_CAST(c0 AS DOUBLE) AS ts,
      TRY_CAST(c1 AS BIGINT) AS len,
      coalesce(nullif(c2, ''), nullif(c4, '')) AS src_ip,
      coalesce(nullif(c3, ''), nullif(c5, '')) AS dst_ip,
      coalesce(nullif(c6, ''), nullif(c7, '')) AS proto_num,
      TRY_CAST(coalesce(nullif(c8, ''),  nullif(c10, '')) AS INTEGER) AS src_port,
      TRY_CAST(coalesce(nullif(c9, ''),  nullif(c11, '')) AS INTEGER) AS dst_port,
      (nullif(c8, '')  IS NOT NULL) AS is_tcp,
      (nullif(c10, '') IS NOT NULL) AS is_udp,
      lower(nullif(c12, '')) AS stack
    FROM read_csv({_sqlstr(str(tsv))}, delim='{_TAB}', header=false,
                  quote='', escape='', columns={_COLUMNS})
  ),
  canon AS (
    SELECT
      CASE WHEN src_ip <= dst_ip THEN src_ip ELSE dst_ip END AS ip_a,
      CASE WHEN src_ip <= dst_ip THEN dst_ip ELSE src_ip END AS ip_b,
      ({PROTO_SQL}) AS l4_proto,
      CASE WHEN src_ip <= dst_ip THEN src_port ELSE dst_port END AS port_a,
      CASE WHEN src_ip <= dst_ip THEN dst_port ELSE src_port END AS port_b,
      (src_ip <= dst_ip) AS forward,
      ts, len, stack,
      CASE WHEN stack IS NULL THEN 0 ELSE len(string_split(stack, ':')) END AS depth
    FROM pkt
    WHERE src_ip IS NOT NULL AND dst_ip IS NOT NULL
  )
  SELECT
    ip_a, ip_b, l4_proto, port_a, port_b,
    sum(CASE WHEN forward      THEN 1   ELSE 0 END) AS pkts_a2b,
    sum(CASE WHEN forward      THEN len ELSE 0 END) AS bytes_a2b,
    sum(CASE WHEN NOT forward  THEN 1   ELSE 0 END) AS pkts_b2a,
    sum(CASE WHEN NOT forward  THEN len ELSE 0 END) AS bytes_b2a,
    min(ts) AS first_seen,
    max(ts) AS last_seen,
    arg_max(stack, depth) AS stack,
    max(depth)            AS depth
  FROM canon
  GROUP BY ip_a, ip_b, l4_proto, port_a, port_b
) TO {_sqlstr(str(shard))} (FORMAT parquet, COMPRESSION zstd)
"""


def shard_path(pcap: Path) -> Path:
    """Stable shard name derived from the file path, so re-runs overwrite cleanly."""
    h = hashlib.sha1(str(pcap.resolve()).encode()).hexdigest()[:16]
    return config.SCRATCH_DIR / f"{h}.parquet"


def extract_file(pcap_str: str) -> dict:
    """Extract one pcap to a flow-grain shard. Returns a manifest-row dict (never raises)."""
    pcap = Path(pcap_str)
    shard = shard_path(pcap)
    tsv = shard.with_suffix(".tsv")
    try:
        st = pcap.stat()
        tshark.extract_to_tsv(pcap, tsv)
        con = duckdb.connect()
        try:
            con.execute(_extract_sql(tsv, shard))
            rows = con.execute(
                f"SELECT count(*) FROM read_parquet({_sqlstr(str(shard))})"
            ).fetchone()[0]
        finally:
            con.close()
        return {
            "path": str(pcap.resolve()), "shard": str(shard), "rows": rows,
            "size": st.st_size, "mtime": st.st_mtime, "status": "done", "error": None,
        }
    except Exception as e:  # noqa: BLE001 - report, don't crash the pool
        size = pcap.stat().st_size if pcap.exists() else 0
        mtime = pcap.stat().st_mtime if pcap.exists() else 0.0
        return {
            "path": str(pcap.resolve()), "shard": str(shard), "rows": 0,
            "size": size, "mtime": mtime, "status": "error", "error": str(e)[:1000],
        }
    finally:
        if tsv.exists():
            tsv.unlink()
