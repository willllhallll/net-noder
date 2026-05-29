"""Aggregate all parquet shards into the endpoints/connections/conversations tables.

DuckDB does the heavy GROUP BY out-of-core (spilling to ``temp_directory``), so this
stays within the RAM budget even for hundreds of millions of packets. Tables are
rebuilt via DELETE + INSERT to preserve the schema and indexes from schema.sql.
"""
import ipaddress

import duckdb

from . import config
from .transform import CAST_SQL, PROTO_SQL, SERVICE_IS_SRC_SQL

# Max reply ports retained per conversation row (top-N by bytes). Bounds the size of
# conversation_ports; the per-conversation total is still recorded on conversations
# (reply_port_count).
_REPLY_KEEP = 50


def aggregate(con: duckdb.DuckDBPyConnection, memory: str = "4GB", threads: int = 8) -> None:
    shard_glob = str(config.SCRATCH_DIR / "*.parquet")
    n_shards = con.execute("SELECT count(*) FROM glob(?)", [shard_glob]).fetchone()[0]
    if n_shards == 0:
        print("No parquet shards found; nothing to aggregate.")
        return

    con.execute(f"SET memory_limit='{memory}'")
    con.execute(f"SET threads={max(1, threads)}")
    con.execute(f"SET temp_directory='{config.DATA_DIR}'")

    # Outer query derives, from the inner query's per-packet service_is_src:
    #   server_port     - the service endpoint's port.
    #   reply_port      - the other (replying) side's port. Generalises the old
    #                     ephemeral client port: many ephemeral ports in a normal
    #                     client->server flow, or a single service port when both
    #                     sides talk on service ports.
    #   server_is_a_row - whether ip_a owns the service port on this packet: ip_a
    #                     when the service side is the source (a_is_src), else when
    #                     it's the destination (NOT a_is_src).
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW flows AS
        SELECT *,
            CASE WHEN service_is_src IS NULL THEN NULL
                 WHEN service_is_src THEN src_port ELSE dst_port END AS server_port,
            CASE WHEN service_is_src IS NULL THEN NULL
                 WHEN service_is_src THEN dst_port ELSE src_port END AS reply_port,
            CASE WHEN service_is_src IS NULL THEN NULL
                 WHEN service_is_src THEN a_is_src ELSE NOT a_is_src END AS server_is_a_row
        FROM (
            SELECT
                src_ip, dst_ip, ts, len, src_port, dst_port,
                {PROTO_SQL}          AS proto,
                {CAST_SQL}           AS cast_type,
                {SERVICE_IS_SRC_SQL} AS service_is_src,
                LEAST(src_ip, dst_ip)    AS ip_a,
                GREATEST(src_ip, dst_ip) AS ip_b,
                (src_ip <= dst_ip)       AS a_is_src
            FROM read_parquet('{shard_glob}')
            WHERE src_ip IS NOT NULL AND dst_ip IS NOT NULL
        )
    """)

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM conversation_ports")
        con.execute("DELETE FROM conversations")
        con.execute("DELETE FROM connections")
        con.execute("DELETE FROM endpoints")

        con.execute("""
            INSERT INTO connections
            SELECT
                row_number() OVER (ORDER BY (bytes_a2b + bytes_b2a) DESC) AS id,
                ip_a, ip_b, pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a, first_seen, last_seen
            FROM (
                SELECT ip_a, ip_b,
                    SUM(CASE WHEN a_is_src THEN 1   ELSE 0 END) AS pkts_a2b,
                    SUM(CASE WHEN a_is_src THEN len ELSE 0 END) AS bytes_a2b,
                    SUM(CASE WHEN a_is_src THEN 0   ELSE 1 END) AS pkts_b2a,
                    SUM(CASE WHEN a_is_src THEN 0 ELSE len END) AS bytes_b2a,
                    MIN(ts) AS first_seen, MAX(ts) AS last_seen
                FROM flows GROUP BY ip_a, ip_b
            )
        """)

        # Per-conversation directional rollup, keyed by the service endpoint
        # (server_port + server_is_a) so a service port shared by both ends yields
        # two directional rows. server_is_a is deterministic per packet, so it's a
        # plain group key here.
        con.execute("""
            INSERT INTO conversations
            SELECT c.id, f.proto, f.server_port, f.cast_type,
                   SUM(CASE WHEN f.a_is_src THEN 1   ELSE 0 END) AS pkts_a2b,
                   SUM(CASE WHEN f.a_is_src THEN f.len ELSE 0 END) AS bytes_a2b,
                   SUM(CASE WHEN f.a_is_src THEN 0   ELSE 1 END) AS pkts_b2a,
                   SUM(CASE WHEN f.a_is_src THEN 0 ELSE f.len END) AS bytes_b2a,
                   COUNT(DISTINCT f.reply_port) AS reply_port_count,
                   f.server_is_a_row AS server_is_a,
                   MIN(f.ts) AS first_seen, MAX(f.ts) AS last_seen
            FROM flows f
            JOIN connections c USING (ip_a, ip_b)
            GROUP BY c.id, f.proto, f.server_port, f.cast_type, f.server_is_a_row
        """)

        # Finest-grain breakdown (per reply port), materialised once for the top-N
        # ports rollup so it doesn't rescan the parquet.
        con.execute("DROP TABLE IF EXISTS conv_full")
        con.execute("""
            CREATE TEMP TABLE conv_full AS
            SELECT ip_a, ip_b, proto, server_port, cast_type, server_is_a_row, reply_port,
                   COUNT(*) AS pkts, SUM(len) AS bytes
            FROM flows
            GROUP BY ip_a, ip_b, proto, server_port, cast_type, server_is_a_row, reply_port
        """)

        # Top-N reply ports per conversation (by bytes).
        con.execute(f"""
            INSERT INTO conversation_ports
            SELECT c.id, f.proto, f.server_port, f.cast_type, f.server_is_a_row,
                   f.reply_port, f.pkts, f.bytes
            FROM (
                SELECT *, row_number() OVER (
                           PARTITION BY ip_a, ip_b, proto, server_port, cast_type,
                                        server_is_a_row
                           ORDER BY bytes DESC) AS rk
                FROM conv_full
                WHERE reply_port IS NOT NULL
            ) f
            JOIN connections c USING (ip_a, ip_b)
            WHERE f.rk <= {_REPLY_KEEP}
        """)

        con.execute("""
            INSERT INTO endpoints
            SELECT ip,
                   SUM(pkts) AS total_pkts, SUM(bytes) AS total_bytes,
                   MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen,
                   FALSE, 'unicast', NULL
            FROM (
                SELECT src_ip AS ip, COUNT(*) pkts, SUM(len) bytes,
                       MIN(ts) first_seen, MAX(ts) last_seen
                FROM flows GROUP BY src_ip
                UNION ALL
                SELECT dst_ip AS ip, COUNT(*) pkts, SUM(len) bytes,
                       MIN(ts) first_seen, MAX(ts) last_seen
                FROM flows GROUP BY dst_ip
            ) GROUP BY ip
        """)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    _classify_endpoints(con)


def _classify_endpoints(con: duckdb.DuckDBPyConnection) -> None:
    """Set is_local + kind on endpoints using Python's ipaddress (small table)."""
    extra_nets = []
    for cidr in config.EXTRA_LOCAL_SUBNETS:
        try:
            extra_nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    ips = [r[0] for r in con.execute("SELECT ip FROM endpoints").fetchall()]
    updates = [(*_classify_ip(ip, extra_nets), ip) for ip in ips]
    if updates:
        con.executemany("UPDATE endpoints SET kind=?, is_local=? WHERE ip=?", updates)


def _classify_ip(ip: str, extra_nets) -> tuple[str, bool]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unicast", False
    if addr.version == 4 and ip == "255.255.255.255":
        return "broadcast", False
    if addr.is_multicast:
        return "multicast", False
    is_local = (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or any(addr in n for n in extra_nets)
    )
    return "unicast", bool(is_local)
