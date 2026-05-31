"""The *reduce* stage: merge all flow-grain shards and rebuild the derived store.

One DuckDB process reads every parquet shard, merges them to the canonical flow
grain (`flows` + `flow_layers`), then rebuilds the derived rollups atomically:
`endpoints`, `connections`, `connection_layers`, `connection_protocols`. Everything
is recomputed from scratch each run, so there is never an in-place update anomaly.

Bounded RAM: `memory_limit` + a `temp_directory` let the big out-of-core `GROUP BY`
spill to disk, so the 100s-of-GB corpus merges without blowing memory.

The peer/dissector model is enforced here:
  * connection_id is assigned per IP-pair, ordered by total bytes (no roles).
  * protocols come only from the deepest dissected stack (arg_max over depth).
  * `connection_protocols` is presence-based and strips generic link/net tokens.
  * `cast_type` is derived from the two endpoints' kinds, never stored per flow.
"""
import duckdb

from . import config
from .transform import generic_layers_sql, kind_sql, NOISE_TOKEN

# Derived/base tables rebuilt on every aggregation (manifest + protocols are owned
# by the CLI and intentionally excluded here).
_REBUILT = (
    "connection_protocols", "connection_layers", "connections",
    "endpoints", "flow_layers", "flows",
)

_GENERIC = generic_layers_sql()


def aggregate(con: duckdb.DuckDBPyConnection, memory: str = "4GB", threads: int = 4) -> None:
    shards = sorted(config.SCRATCH_DIR.glob("*.parquet"))
    if not shards:
        print("aggregate: no shards found; nothing to do")
        return

    tmpdir = config.SCRATCH_DIR / "duckdb_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA memory_limit='{memory}'")
    con.execute(f"PRAGMA threads={max(1, threads)}")
    con.execute(f"PRAGMA temp_directory='{tmpdir}'")

    glob = "'" + str(config.SCRATCH_DIR / "*.parquet").replace("'", "''") + "'"

    # Wipe the rebuilt tables (children first so no dangling references mid-run).
    for t in _REBUILT:
        con.execute(f"DELETE FROM {t}")

    # 1. Merge shards to the canonical flow grain, picking the deepest stack globally.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE merged AS
        SELECT ip_a, ip_b, l4_proto, port_a, port_b,
               sum(pkts_a2b)  AS pkts_a2b,  sum(bytes_a2b) AS bytes_a2b,
               sum(pkts_b2a)  AS pkts_b2a,  sum(bytes_b2a) AS bytes_b2a,
               min(first_seen) AS first_seen, max(last_seen) AS last_seen,
               arg_max(stack, depth) AS stack
        FROM read_parquet({glob})
        GROUP BY ip_a, ip_b, l4_proto, port_a, port_b
    """)

    # 2. Assign connection_id per IP-pair, ranked by total bytes (heaviest = 1).
    con.execute("""
        CREATE OR REPLACE TEMP TABLE conn_geo AS
        SELECT ip_a, ip_b,
               row_number() OVER (
                   ORDER BY sum(bytes_a2b + bytes_b2a) DESC, ip_a, ip_b
               ) AS connection_id
        FROM merged
        GROUP BY ip_a, ip_b
    """)

    # 3. Surrogate flow_id per merged 5-tuple; keep ip pair + stack for the explode.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE flows_tmp AS
        SELECT row_number() OVER (
                   ORDER BY g.connection_id, m.l4_proto, m.port_a, m.port_b
               ) AS flow_id,
               g.connection_id, g.ip_a, g.ip_b,
               m.l4_proto, m.port_a, m.port_b,
               m.pkts_a2b, m.bytes_a2b, m.pkts_b2a, m.bytes_b2a,
               m.first_seen, m.last_seen, m.stack
        FROM merged m JOIN conn_geo g USING (ip_a, ip_b)
    """)

    # 4. flows fact (stack + geo dropped -- they live in flow_layers / connections).
    con.execute("""
        INSERT INTO flows
        SELECT flow_id, connection_id, l4_proto, port_a, port_b,
               pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a, first_seen, last_seen
        FROM flows_tmp
    """)

    # 5. Explode the stack into one row per layer. Drop the 'ethertype' noise token,
    #    then re-index contiguously per flow preserving the original stack order.
    #    (Two same-length UNNESTs in one projection zip positionally in DuckDB.)
    con.execute(f"""
        INSERT INTO flow_layers
        WITH exploded AS (
            SELECT flow_id,
                   unnest(string_split(stack, ':')) AS layer,
                   unnest(range(0, len(string_split(stack, ':')))) AS orig_pos
            FROM flows_tmp
            WHERE stack IS NOT NULL AND stack <> ''
        )
        SELECT flow_id,
               CAST(row_number() OVER (PARTITION BY flow_id ORDER BY orig_pos) - 1
                    AS SMALLINT) AS layer_index,
               layer
        FROM exploded
        WHERE layer <> '' AND layer <> '{NOISE_TOKEN}'
    """)

    # 6. endpoints: each flow contributes to BOTH of its IPs (all its packets touch
    #    each endpoint). degree = distinct peers. kind = uni/multi/bcast by IP only.
    con.execute(f"""
        INSERT INTO endpoints
        WITH contrib AS (
            SELECT ip_a AS ip, ip_b AS peer,
                   pkts_a2b + pkts_b2a AS pk, bytes_a2b + bytes_b2a AS byts,
                   first_seen, last_seen FROM merged
            UNION ALL
            SELECT ip_b AS ip, ip_a AS peer,
                   pkts_a2b + pkts_b2a AS pk, bytes_a2b + bytes_b2a AS byts,
                   first_seen, last_seen FROM merged
        )
        SELECT ip,
               sum(pk) AS total_pkts,
               sum(byts) AS total_bytes,
               min(first_seen) AS first_seen,
               max(last_seen)  AS last_seen,
               {kind_sql('ip')} AS kind,
               count(DISTINCT peer) AS degree
        FROM contrib
        GROUP BY ip
    """)

    # 7. connection_protocols (edge-click view): presence-based per (conn, layer, l4),
    #    generic link/net tokens stripped. Measures and distinct-port counts are
    #    computed in separate passes -- unnesting ports inline would double the sums.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cp_measures AS
        SELECT f.connection_id, fl.layer, f.l4_proto,
               sum(f.pkts_a2b) AS pkts_a2b, sum(f.bytes_a2b) AS bytes_a2b,
               sum(f.pkts_b2a) AS pkts_b2a, sum(f.bytes_b2a) AS bytes_b2a,
               min(f.first_seen) AS first_seen, max(f.last_seen) AS last_seen
        FROM flows f JOIN flow_layers fl ON fl.flow_id = f.flow_id
        WHERE fl.layer NOT IN {_GENERIC}
        GROUP BY f.connection_id, fl.layer, f.l4_proto
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cp_ports AS
        SELECT f.connection_id, fl.layer, f.l4_proto,
               count(DISTINCT p.port) AS port_count
        FROM flows f JOIN flow_layers fl ON fl.flow_id = f.flow_id,
             UNNEST([f.port_a, f.port_b]) AS p(port)
        WHERE fl.layer NOT IN {_GENERIC} AND p.port IS NOT NULL
        GROUP BY f.connection_id, fl.layer, f.l4_proto
    """)
    con.execute("""
        INSERT INTO connection_protocols
        SELECT m.connection_id, m.layer, m.l4_proto,
               m.pkts_a2b, m.bytes_a2b, m.pkts_b2a, m.bytes_b2a,
               coalesce(p.port_count, 0), m.first_seen, m.last_seen
        FROM cp_measures m
        LEFT JOIN cp_ports p USING (connection_id, layer, l4_proto)
    """)

    # 8. connection_layers: the FULL distinct stack per connection (generics kept),
    #    for the graph's 4-tier stepper.
    con.execute("""
        INSERT INTO connection_layers
        SELECT DISTINCT f.connection_id, fl.layer
        FROM flows f JOIN flow_layers fl ON fl.flow_id = f.flow_id
    """)

    # 9. connections: per-direction counters summed from flows; distinct-port count;
    #    distinct protocol-layer count; cast_type derived from the endpoints' kinds.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE conn_meas AS
        SELECT connection_id,
               sum(pkts_a2b) AS pkts_a2b, sum(bytes_a2b) AS bytes_a2b,
               sum(pkts_b2a) AS pkts_b2a, sum(bytes_b2a) AS bytes_b2a,
               min(first_seen) AS first_seen, max(last_seen) AS last_seen
        FROM flows GROUP BY connection_id
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE conn_ports AS
        SELECT f.connection_id, count(DISTINCT p.port) AS port_count
        FROM flows f, UNNEST([f.port_a, f.port_b]) AS p(port)
        WHERE p.port IS NOT NULL
        GROUP BY f.connection_id
    """)
    con.execute("""
        INSERT INTO connections
        SELECT g.connection_id, g.ip_a, g.ip_b,
               cm.pkts_a2b, cm.bytes_a2b, cm.pkts_b2a, cm.bytes_b2a,
               CASE
                   WHEN ea.kind = 'broadcast' OR eb.kind = 'broadcast' THEN 'broadcast'
                   WHEN ea.kind = 'multicast' OR eb.kind = 'multicast' THEN 'multicast'
                   ELSE 'unicast'
               END AS cast_type,
               coalesce(pc.protocol_count, 0) AS protocol_count,
               coalesce(cp.port_count, 0)     AS port_count,
               cm.first_seen, cm.last_seen
        FROM conn_geo g
        JOIN conn_meas cm ON cm.connection_id = g.connection_id
        JOIN endpoints ea ON ea.ip = g.ip_a
        JOIN endpoints eb ON eb.ip = g.ip_b
        LEFT JOIN conn_ports cp ON cp.connection_id = g.connection_id
        LEFT JOIN (
            SELECT connection_id, count(DISTINCT layer) AS protocol_count
            FROM connection_protocols GROUP BY connection_id
        ) pc ON pc.connection_id = g.connection_id
    """)

    for t in ("merged", "conn_geo", "flows_tmp", "cp_measures", "cp_ports",
              "conn_meas", "conn_ports"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
