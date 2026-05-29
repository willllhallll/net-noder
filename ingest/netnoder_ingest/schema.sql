-- Aggregated tables served to the web app. Built by aggregate.py.
-- All packet-level data is discarded after aggregation; only rollups live here.

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

CREATE TABLE IF NOT EXISTS conversations (
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

CREATE TABLE IF NOT EXISTS services (
    conversation_id   BIGINT,
    l4_proto          VARCHAR,
    server_port       INTEGER,  -- NULL for non-TCP/UDP
    cast_type         VARCHAR,  -- 'unicast' | 'multicast' | 'broadcast'
    pkts              BIGINT,
    bytes             BIGINT,
    client_port_count BIGINT    -- distinct ephemeral ports seen for this service
);
-- Migration for DBs created before client_port_count existed.
ALTER TABLE services ADD COLUMN IF NOT EXISTS client_port_count BIGINT;

-- Bounded per-service breakdown of ephemeral/client ports (top-N by bytes).
-- Joins back to a `services` row via (conversation_id, l4_proto, server_port, cast_type).
CREATE TABLE IF NOT EXISTS service_ports (
    conversation_id BIGINT,
    l4_proto        VARCHAR,
    server_port     INTEGER,
    cast_type       VARCHAR,
    client_port     INTEGER,
    pkts            BIGINT,
    bytes           BIGINT
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

CREATE INDEX IF NOT EXISTS idx_conv_a ON conversations(ip_a);
CREATE INDEX IF NOT EXISTS idx_conv_b ON conversations(ip_b);
CREATE INDEX IF NOT EXISTS idx_svc_conv ON services(conversation_id);
CREATE INDEX IF NOT EXISTS idx_svcports_conv ON service_ports(conversation_id);
