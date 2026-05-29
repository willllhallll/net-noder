-- Aggregated tables served to the web app. Built by aggregate.py.
-- All packet-level data is discarded after aggregation; only rollups live here.
--
-- The three-layer model:
--   endpoints     - a device with one IP address.
--   connections   - any traffic between two endpoints (the IP-pair).
--   conversations - traffic on a specific service/port within a connection, which
--                   may fan out to many ephemeral reply ports.

CREATE TABLE IF NOT EXISTS endpoints (
    ip          VARCHAR PRIMARY KEY,
    total_pkts  BIGINT,
    total_bytes BIGINT,
    first_seen  DOUBLE,   -- epoch seconds
    last_seen   DOUBLE,
    is_local    BOOLEAN,
    kind        VARCHAR,  -- 'unicast' | 'multicast' | 'broadcast'
    hostname    VARCHAR
);

CREATE TABLE IF NOT EXISTS connections (
    id         BIGINT PRIMARY KEY,
    ip_a       VARCHAR,  -- canonical: ip_a <= ip_b (lexicographic)
    ip_b       VARCHAR,
    pkts_a2b   BIGINT,
    bytes_a2b  BIGINT,
    pkts_b2a   BIGINT,
    bytes_b2a  BIGINT,
    first_seen DOUBLE,
    last_seen  DOUBLE
);

-- One row per (connection, l4_proto, server_port, cast_type): traffic on a single
-- assumed service. Directional counts + server_is_a give the client->server arrow.
CREATE TABLE IF NOT EXISTS conversations (
    connection_id     BIGINT,
    l4_proto          VARCHAR,
    server_port       INTEGER,  -- NULL for non-TCP/UDP
    cast_type         VARCHAR,  -- 'unicast' | 'multicast' | 'broadcast'
    pkts_a2b          BIGINT,
    bytes_a2b         BIGINT,
    pkts_b2a          BIGINT,
    bytes_b2a         BIGINT,
    client_port_count BIGINT,   -- distinct ephemeral ports seen for this conversation
    server_is_a       BOOLEAN,  -- TRUE: ip_a owns server_port; FALSE: ip_b; NULL: no service port
    first_seen        DOUBLE,   -- epoch seconds
    last_seen         DOUBLE
);

-- Bounded per-conversation breakdown of ephemeral/client ports (top-N by bytes).
-- Joins back to a `conversations` row via (connection_id, l4_proto, server_port, cast_type).
CREATE TABLE IF NOT EXISTS conversation_ports (
    connection_id BIGINT,
    l4_proto      VARCHAR,
    server_port   INTEGER,
    cast_type     VARCHAR,
    client_port   INTEGER,
    pkts          BIGINT,
    bytes         BIGINT
);

-- User-curated IP -> friendly name mapping. Independent of the aggregation tables
-- (never wiped by re-ingest), so names can be edited without re-processing captures.
CREATE TABLE IF NOT EXISTS names (
    ip         VARCHAR PRIMARY KEY,
    given_name VARCHAR
);

-- Tracks which pcap files have been extracted, for resumable ingest.
CREATE TABLE IF NOT EXISTS manifest (
    path        VARCHAR PRIMARY KEY,
    size        BIGINT,
    mtime       DOUBLE,
    status      VARCHAR,  -- 'done' | 'error'
    rows        BIGINT,
    shard       VARCHAR,
    ingested_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conn_a ON connections(ip_a);
CREATE INDEX IF NOT EXISTS idx_conn_b ON connections(ip_b);
CREATE INDEX IF NOT EXISTS idx_conv_conn ON conversations(connection_id);
CREATE INDEX IF NOT EXISTS idx_convports_conn ON conversation_ports(connection_id);
