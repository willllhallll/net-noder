"""Per-file extraction: tshark -> transient TSV -> flow-grain parquet shard.

This is the *combine* stage of the map/combine/reduce pipeline. Each worker runs in
its own process with its own in-memory DuckDB connection, reads the per-packet TSV,
and pre-aggregates it down to **flow grain** before writing one compact parquet
shard. Collapsing millions of packets into a handful of flow rows here -- per small
file, in bounded RAM -- is the single biggest lever keeping the global merge cheap.

Canonical 5-tuple: ``ip_a <= ip_b`` and ``port_a`` always belongs to ``ip_a``. A
packet is in the ``a2b`` direction when its source is ``ip_a``. There is NO role,
NO ephemeral filtering, and NO port cap -- every real port-pair becomes its own row.

Protocols are carried as a PRESENCE set, not a single deepest stack: each shard row
holds the distinct ``frame.protocols`` tokens seen across ALL the flow's packets, each
with its first-appearance position (``list({token,pos})``). This cures the old
deepest-frame shadowing (``arg_max(stack, depth)``) and is stable when a certificate
gains/loses an ASN.1 element. The flow's ``protocol_version`` is read deterministically
from whatever version each *present* protocol exposes (TLS/DTLS handshake, QUIC, HTTP,
NTP), decoded via the standards registry; the most-specific non-null label wins. The
transient TSV is deleted immediately, so peak extra disk is bounded by the largest single
pcap, never the whole corpus.
"""
import hashlib
from pathlib import Path

import duckdb

from . import config, tshark
from .transform import (
    NOISE_TOKEN,
    PROTO_SQL,
    dtls_version_label_sql,
    quic_version_label_sql,
    tls_version_label_sql,
)

# All 21 tshark columns (c0..c20) read as VARCHAR, then normalised below.
_COLUMNS = "{" + ", ".join(f"'c{i}': 'VARCHAR'" for i in range(21)) + "}"
_TAB = "\t"  # interpolated as a real tab so DuckDB's delim is unambiguous


def _sqlstr(p: str) -> str:
    """Quote a path as a SQL string literal (paths are internal, but be safe)."""
    return "'" + p.replace("'", "''") + "'"


def _extract_sql(tsv: Path, shard: Path) -> str:
    # pkt:  one row per packet, normalised (IPv4/IPv6 unified, ports unified). Also
    #       carries the TLS handshake fields used to read the negotiated version.
    # canon: canonicalise to ip_a<=ip_b, attribute the packet to a2b/b2a, compute the
    #       L4 token, and derive the ServerHello negotiated version per packet.
    # flows: flow-grain measures + the negotiated TLS version (deterministic).
    # pkt_tokens -> flow_tok -> tok_list: the distinct PRESENCE token set per flow, each
    #       with its first-appearance position (drops empties + the 'ethertype' noise).
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
      lower(nullif(c12, '')) AS stack,
      nullif(c13, '') AS hs_type,        -- tls.handshake.type (2 = ServerHello)
      nullif(c14, '') AS hs_ver,         -- tls.handshake.version (legacy/negotiated)
      nullif(c15, '') AS sup_ver,        -- tls.handshake.extensions.supported_version (TLS1.3)
      nullif(c16, '') AS quic_ver,       -- quic.version
      nullif(c17, '') AS http_resp_ver,  -- http.response.version
      nullif(c18, '') AS http_req_ver,   -- http.request.version
      nullif(c19, '') AS ntp_vn,         -- ntp.flags.vn
      nullif(c20, '') AS dtls_ver        -- dtls.handshake.version
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
      -- Per-protocol raw version values, kept un-decoded here (decoded + ranked per flow
      -- below). TLS comes ONLY from the ServerHello (handshake.type=2): prefer the TLS1.3
      -- supported_versions extension, else the legacy handshake version. The others are
      -- read as-is wherever the dissector emitted them.
      CASE WHEN hs_type = '2' THEN coalesce(sup_ver, hs_ver) END AS tls_raw,
      dtls_ver AS dtls_raw,
      quic_ver AS quic_raw,
      coalesce(http_resp_ver, http_req_ver) AS http_raw,
      ntp_vn AS ntp_raw
    FROM pkt
    WHERE src_ip IS NOT NULL AND dst_ip IS NOT NULL
  ),
  flows AS (
    SELECT
      ip_a, ip_b, l4_proto, port_a, port_b,
      sum(CASE WHEN forward      THEN 1   ELSE 0 END) AS pkts_a2b,
      sum(CASE WHEN forward      THEN len ELSE 0 END) AS bytes_a2b,
      sum(CASE WHEN NOT forward  THEN 1   ELSE 0 END) AS pkts_b2a,
      sum(CASE WHEN NOT forward  THEN len ELSE 0 END) AS bytes_b2a,
      min(ts) AS first_seen,
      max(ts) AS last_seen,
      -- protocol_version: the most-specific protocol's decoded version label, picked by a
      -- fixed session-version precedence (security > transport > application). Each branch
      -- maxes its raw value per flow, decodes via the standards registry, then coalesces.
      coalesce(
        {tls_version_label_sql('max(tls_raw)')},
        {dtls_version_label_sql('max(dtls_raw)')},
        {quic_version_label_sql('max(quic_raw)')},
        max(http_raw),
        CASE WHEN max(ntp_raw) IS NULL THEN NULL ELSE 'NTPv' || max(ntp_raw) END
      ) AS protocol_version
    FROM canon
    GROUP BY ip_a, ip_b, l4_proto, port_a, port_b
  ),
  pkt_tokens AS (
    SELECT
      ip_a, ip_b, l4_proto, port_a, port_b,
      unnest(string_split(stack, ':'))                AS token,
      unnest(range(0, len(string_split(stack, ':')))) AS pos
    FROM canon
    WHERE stack IS NOT NULL AND stack <> ''
  ),
  flow_tok AS (
    SELECT
      ip_a, ip_b, l4_proto, port_a, port_b,
      token, min(pos) AS pos
    FROM pkt_tokens
    WHERE token <> '' AND token <> '{NOISE_TOKEN}'
    GROUP BY ip_a, ip_b, l4_proto, port_a, port_b, token
  ),
  tok_list AS (
    SELECT
      ip_a, ip_b, l4_proto, port_a, port_b,
      list({{'token': token, 'pos': pos}} ORDER BY pos, token) AS tokens
    FROM flow_tok
    GROUP BY ip_a, ip_b, l4_proto, port_a, port_b
  )
  SELECT
    f.ip_a, f.ip_b, f.l4_proto, f.port_a, f.port_b,
    f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a,
    f.first_seen, f.last_seen, f.protocol_version,
    tl.tokens AS tokens
  FROM flows f
  LEFT JOIN tok_list tl USING (ip_a, ip_b, l4_proto, port_a, port_b)
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
        warn = tshark.extract_to_tsv(pcap, tsv)
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
            "size": st.st_size, "mtime": st.st_mtime, "status": "done", "error": warn,
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
