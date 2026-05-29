"""Per-file extraction: tshark -> transient TSV -> normalised parquet shard.

Runs in worker processes. Each worker uses its own in-memory DuckDB connection to
parse the TSV and write a compact, typed parquet shard; the parent process owns the
on-disk database and records results in the manifest. The transient TSV is deleted
immediately, so peak extra disk is bounded by the largest single pcap, not the whole
capture.
"""
import hashlib
from pathlib import Path

import duckdb

from . import config, tshark

# All tshark columns read as VARCHAR, then normalised below.
_COLUMNS = "{" + ", ".join(f"'c{i}': 'VARCHAR'" for i in range(13)) + "}"
_TAB = "\t"  # interpolated as a real tab so DuckDB's delim is unambiguous


def _sqlstr(p: str) -> str:
    """Quote a path as a SQL string literal (paths are internal, but be safe)."""
    return "'" + p.replace("'", "''") + "'"


def _extract_sql(tsv: Path, shard: Path) -> str:
    return f"""
COPY (
  SELECT
    TRY_CAST(c0 AS DOUBLE)  AS ts,
    TRY_CAST(c1 AS BIGINT)  AS len,
    lower(nullif(c2, ''))   AS eth_dst,
    coalesce(nullif(c3, ''), nullif(c5, '')) AS src_ip,
    coalesce(nullif(c4, ''), nullif(c6, '')) AS dst_ip,
    coalesce(nullif(c7, ''), nullif(c8, '')) AS proto_num,
    TRY_CAST(coalesce(nullif(c9,  ''), nullif(c11, '')) AS INTEGER) AS src_port,
    TRY_CAST(coalesce(nullif(c10, ''), nullif(c12, '')) AS INTEGER) AS dst_port,
    (nullif(c9,  '') IS NOT NULL) AS is_tcp,
    (nullif(c11, '') IS NOT NULL) AS is_udp
  FROM read_csv({_sqlstr(str(tsv))}, delim='{_TAB}', header=false,
                quote='', escape='', columns={_COLUMNS})
) TO {_sqlstr(str(shard))} (FORMAT parquet, COMPRESSION zstd)
"""


def shard_path(pcap: Path) -> Path:
    """Stable shard name derived from the file path, so re-runs overwrite cleanly."""
    h = hashlib.sha1(str(pcap.resolve()).encode()).hexdigest()[:16]
    return config.SCRATCH_DIR / f"{h}.parquet"


def extract_file(pcap_str: str) -> dict:
    """Extract one pcap to a parquet shard. Returns a manifest-row dict (never raises)."""
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
