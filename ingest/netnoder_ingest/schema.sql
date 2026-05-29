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

-- One row per service endpoint within a connection, keyed by
-- (connection, l4_proto, server_port, cast_type, server_is_a). A service port used
-- by both ends yields two rows (one per direction); each is a directional arrow.
-- The reply ports may be many ephemeral ports or a single peer service port.
CREATE TABLE IF NOT EXISTS conversations (
    connection_id     BIGINT,
    l4_proto          VARCHAR,
    server_port       INTEGER,  -- NULL for non-TCP/UDP
    cast_type         VARCHAR,  -- 'unicast' | 'multicast' | 'broadcast'
    pkts_a2b          BIGINT,
    bytes_a2b         BIGINT,
    pkts_b2a          BIGINT,
    bytes_b2a         BIGINT,
    reply_port_count  BIGINT,   -- distinct reply ports seen for this conversation
    server_is_a       BOOLEAN,  -- TRUE: ip_a owns server_port; FALSE: ip_b; NULL: no service port
    first_seen        DOUBLE,   -- epoch seconds
    last_seen         DOUBLE
);

-- Bounded per-conversation breakdown of reply ports (top-N by bytes). Joins back to
-- a `conversations` row via (connection_id, l4_proto, server_port, cast_type,
-- server_is_a) -- server_is_a is needed because two rows may share server_port.
CREATE TABLE IF NOT EXISTS conversation_ports (
    connection_id BIGINT,
    l4_proto      VARCHAR,
    server_port   INTEGER,
    cast_type     VARCHAR,
    server_is_a   BOOLEAN,
    reply_port    INTEGER,
    pkts          BIGINT,
    bytes         BIGINT
);

-- User-curated IP -> friendly name mapping. Independent of the aggregation tables
-- (never wiped by re-ingest), so names can be edited without re-processing captures.
CREATE TABLE IF NOT EXISTS names (
    ip         VARCHAR PRIMARY KEY,
    given_name VARCHAR
);

-- IANA-style port -> service-description reference, loaded by `netnoder-portmap`.
-- Independent of packet ingest (survives re-ingest/--reset); joined onto a
-- conversation's server_port at query time. Keyed by (port, transport).
CREATE TABLE IF NOT EXISTS port_services (
    port        INTEGER,
    transport   VARCHAR,   -- 'tcp' | 'udp' | 'sctp' | ...
    description VARCHAR,
    PRIMARY KEY (port, transport)
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
