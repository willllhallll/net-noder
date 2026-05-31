-- The single DuckDB store, served (read-only) to the web app. Built by ingest.
-- All packet-level data is discarded after aggregation; only the flow-grain
-- aggregate and its rollups live here. The analytical tables are rebuildable by
-- ingest; the user-curated metadata (names + layer_colours, defined lower down)
-- lives in this SAME store but is PRESERVED across --reset, so it is not lost when
-- captures are re-ingested -- only a deleted DuckDB file resets it.
--
-- The model is peer-to-peer: there are no client/server roles, no ephemeral-port
-- filtering, and no IANA port map. Protocol identity comes solely from the tshark
-- dissector (frame.protocols), recorded per layer in flow_layers.
--
-- Grain:
--   flows         - FACT: one row per canonical 5-tuple (key -> directional measures).
--   flow_layers   - the dissected protocol stack, one row per (flow, layer).
-- Derived rollups (rebuilt atomically each aggregation):
--   endpoints            - one row per IP (node).
--   connections          - one row per IP-pair (edge), neutral A<->B counters.
--   connection_layers    - distinct layer tokens per connection (graph tier filter).
--   connection_protocols - per-(pair, layer) presence rollup (edge-click drawer).

-- FACT: one row per canonical 5-tuple. ip_a <= ip_b; port_a belongs to ip_a.
-- No role, no ephemeral filtering, no port cap.
CREATE TABLE IF NOT EXISTS flows (
    flow_id       BIGINT PRIMARY KEY,   -- surrogate
    connection_id BIGINT,               -- FK -> connections
    l4_proto      VARCHAR,              -- 'tcp'|'udp'|'icmp'|'gre'|... (from ip.proto)
    port_a        INTEGER,              -- real port on ip_a side (NULL for portless L4)
    port_b        INTEGER,              -- real port on ip_b side (NULL)
    pkts_a2b      BIGINT,
    bytes_a2b     BIGINT,
    pkts_b2a      BIGINT,
    bytes_b2a     BIGINT,
    first_seen    DOUBLE,               -- epoch seconds
    last_seen     DOUBLE
    -- alternate key (unique by construction): (connection_id, l4_proto, port_a, port_b)
);

-- The dissected stack for each flow: the deepest frame.protocols seen for the
-- 5-tuple, split into one token per layer (generic 'ethertype' noise dropped, then
-- re-indexed contiguously). layer_index is the position in the stack (0=link).
CREATE TABLE IF NOT EXISTS flow_layers (
    flow_id     BIGINT,
    layer_index SMALLINT,
    layer       VARCHAR,                -- single token: 'eth','ip','tcp','tls','http',...
    PRIMARY KEY (flow_id, layer_index)
    -- alternate key: (flow_id, layer) -- a layer appears once per stack
);

-- NODE: one row per IP. kind is purely unicast / multicast / broadcast from
-- well-known IP ranges -- NO local/remote judgement is made anywhere.
CREATE TABLE IF NOT EXISTS endpoints (
    ip          VARCHAR PRIMARY KEY,
    total_pkts  BIGINT,
    total_bytes BIGINT,
    first_seen  DOUBLE,
    last_seen   DOUBLE,
    kind        VARCHAR,                -- 'unicast' | 'multicast' | 'broadcast'
    degree      INTEGER                 -- distinct peers
);

-- EDGE: one row per IP-pair. Both per-direction counters are kept and surfaced,
-- labelled only by endpoint (A->B / B->A) -- no roles, no arrows. cast_type is
-- DERIVED from the two endpoints' kinds.
CREATE TABLE IF NOT EXISTS connections (
    connection_id  BIGINT PRIMARY KEY,
    ip_a           VARCHAR,             -- canonical: ip_a <= ip_b
    ip_b           VARCHAR,
    pkts_a2b       BIGINT,
    bytes_a2b      BIGINT,
    pkts_b2a       BIGINT,
    bytes_b2a      BIGINT,
    cast_type      VARCHAR,             -- derived from endpoint kinds
    protocol_count INTEGER,             -- distinct protocol layers on this edge
    port_count     INTEGER,             -- distinct ports across this edge's flows
    first_seen     DOUBLE,
    last_seen      DOUBLE
);

-- Distinct layer tokens present on each connection (the full stack, deduped).
-- Drives the graph's tier filter + per-edge colouring without re-scanning flows.
CREATE TABLE IF NOT EXISTS connection_layers (
    connection_id BIGINT,
    layer         VARCHAR,
    PRIMARY KEY (connection_id, layer)
);

-- THE edge-click view: the real protocols spoken across a pair, one row per
-- (connection, layer, l4_proto). Counts are PRESENCE-based (a TLS/HTTP flow's
-- bytes count under tcp AND tls AND http), so this is NOT a 100%-summing
-- partition. Generic link/network tokens are stripped here for a clean list.
CREATE TABLE IF NOT EXISTS connection_protocols (
    connection_id BIGINT,
    layer         VARCHAR,             -- 'tcp','tls','http','dns','quic','ssh','icmp',...
    l4_proto      VARCHAR,
    pkts_a2b      BIGINT,
    bytes_a2b     BIGINT,
    pkts_b2a      BIGINT,
    bytes_b2a     BIGINT,
    port_count    INTEGER,             -- distinct ports involved at this layer
    first_seen    DOUBLE,
    last_seen     DOUBLE
);

-- Authoritative protocol/abbrev reference, dumped once per ingest from
-- `tshark -G protocols`. Reference/validation only -- it never drives colour.
CREATE TABLE IF NOT EXISTS protocols (
    abbrev     VARCHAR PRIMARY KEY,    -- the frame.protocols token (filter name)
    name       VARCHAR,
    short_name VARCHAR
);

-- User-curated IP -> given name, loaded from names.csv by `netnoder-names` (or the
-- auto-load step in `netnoder-ingest`). Joined to nodes at query time. This is the
-- ONLY store of names now -- there is no separate API metadata DB.
CREATE TABLE IF NOT EXISTS names (
    ip         VARCHAR PRIMARY KEY,
    given_name VARCHAR
);

-- Persisted layer -> (tier, colour) registry, first-seen-wins. Curated anchors are
-- seeded with seq = -1; each newly observed token takes the next long-tail slot once
-- and keeps it. Seeded/extended by ingest and PRESERVED across re-aggregation and
-- --reset -- it is only lost if the DuckDB file itself is deleted, so a token's
-- colour never changes within the life of a store.
CREATE TABLE IF NOT EXISTS layer_colours (
    layer      VARCHAR PRIMARY KEY,
    tier       VARCHAR,                -- 'link'|'network'|'transport'|'application'
    colour     VARCHAR,                -- hex
    seq        INTEGER,                -- -1 for curated anchors; >=0 long-tail order
    unresolved BOOLEAN                 -- TRUE for tshark stop-markers ('data')
);

-- Tracks which pcap files have been extracted, for resumable ingest.
CREATE TABLE IF NOT EXISTS manifest (
    path        VARCHAR PRIMARY KEY,
    size        BIGINT,
    mtime       DOUBLE,
    status      VARCHAR,               -- 'done' | 'error'
    rows        BIGINT,
    shard       VARCHAR,
    ingested_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conn_a ON connections(ip_a);
CREATE INDEX IF NOT EXISTS idx_conn_b ON connections(ip_b);
CREATE INDEX IF NOT EXISTS idx_flows_conn ON flows(connection_id);
CREATE INDEX IF NOT EXISTS idx_flow_layers_flow ON flow_layers(flow_id);
CREATE INDEX IF NOT EXISTS idx_flow_layers_layer ON flow_layers(layer);
CREATE INDEX IF NOT EXISTS idx_cp_conn ON connection_protocols(connection_id);
CREATE INDEX IF NOT EXISTS idx_cl_conn ON connection_layers(connection_id);
