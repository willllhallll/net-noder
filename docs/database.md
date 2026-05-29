# Database structure

net-noder stores everything in a single embedded **DuckDB** file
(`data/netnoder.duckdb`). There is no server process and no migrations — the schema
is plain `CREATE TABLE IF NOT EXISTS` statements in
[schema.sql](../ingest/netnoder_ingest/schema.sql), applied every time a connection
is opened.

The design goal is to take an arbitrarily large packet capture and reduce it to a set
of **small, fixed-shape rollup tables** that describe the network at three levels of
zoom. Raw packets are discarded after aggregation; only the rollups remain.

## The three-layer model

```
endpoints        a single device (one IP)
   │   one row per IP
   │
connections      ALL traffic between a pair of endpoints (an undirected edge)
   │   one row per {ip_a, ip_b} pair
   │
conversations    traffic toward ONE service endpoint within a connection
   │   one row per (connection, protocol, server_port, cast_type, server_is_a)
   │
conversation_ports   the reply ports that talked to that service (top-N by bytes)
```

Each layer is a finer slice of the one above it, so the UI can drill from "who is on
the network" → "who talks to whom" → "what service" → "which ports".

### Canonical pair orientation

Connections (and everything keyed off them) use a **canonical orientation**: `ip_a`
is always the lexicographically smaller IP and `ip_b` the larger. Direction is then
encoded in column *names* rather than row order:

- `*_a2b` = traffic from `ip_a` to `ip_b`
- `*_b2a` = traffic from `ip_b` to `ip_a`

This is why a packet and its reply always land in the same row — neither "who sent
first" nor src/dst order changes which group a packet falls into.

## Tables

### `endpoints` — the nodes

| column        | type    | meaning                                          |
| ------------- | ------- | ------------------------------------------------ |
| `ip`          | VARCHAR | primary key                                      |
| `total_pkts`  | BIGINT  | packets sent + received                          |
| `total_bytes` | BIGINT  | bytes sent + received (drives node size in the UI) |
| `first_seen` / `last_seen` | DOUBLE | epoch seconds                       |
| `is_local`    | BOOLEAN | RFC1918 / loopback / link-local / extra subnets  |
| `kind`        | VARCHAR | `unicast` \| `multicast` \| `broadcast`          |
| `hostname`    | VARCHAR | reserved for capture-derived names (currently unused) |

### `connections` — the edges

| column      | type    | meaning                                      |
| ----------- | ------- | -------------------------------------------- |
| `id`        | BIGINT  | primary key (ranked by total bytes at build) |
| `ip_a` / `ip_b` | VARCHAR | the pair, canonical (`ip_a <= ip_b`)     |
| `pkts_a2b`, `bytes_a2b`, `pkts_b2a`, `bytes_b2a` | BIGINT | per-direction counts |
| `first_seen` / `last_seen` | DOUBLE | epoch seconds                    |

One row per pair of endpoints, regardless of how many ports/protocols they used.

### `conversations` — per-service detail

Keyed by `(connection_id, l4_proto, server_port, cast_type, server_is_a)`. A service
port used by **both** ends produces two rows (one per direction) — each is a directed
`client → server` arrow in the UI.

| column             | type    | meaning                                          |
| ------------------ | ------- | ------------------------------------------------ |
| `connection_id`    | BIGINT  | → `connections.id`                               |
| `l4_proto`         | VARCHAR | `TCP`, `UDP`, `ICMP`, …                           |
| `server_port`      | INTEGER | the service port; `NULL` for non-TCP/UDP         |
| `cast_type`        | VARCHAR | `unicast` \| `multicast` \| `broadcast`          |
| `pkts_a2b` … `bytes_b2a` | BIGINT | per-direction counts (same `a`/`b` as the connection) |
| `reply_port_count` | BIGINT  | distinct reply ports seen (the *true* total)     |
| `server_is_a`      | BOOLEAN | `TRUE`: `ip_a` owns `server_port`; `FALSE`: `ip_b`; `NULL`: no service port |
| `first_seen` / `last_seen` | DOUBLE | epoch seconds                          |

`server_is_a` is what lets the UI draw the arrow the right way round, and it is part
of the key because the two directional rows of a service-to-service flow share the
same `server_port`.

### `conversation_ports` — bounded port breakdown

The finest grain: which **reply ports** talked to a conversation's service port. Only
the **top 50 by bytes** per conversation are kept (`reply_port_count` on the parent
row holds the true distinct count). Joins back to a conversation via the full key
including `server_is_a`.

Counts here are directional (`a2b`/`b2a`, same orientation as `connections`). That is
deliberate: it lets the API re-present a non-ephemeral reply port as a role-flipped
"mirror" conversation without ever touching raw packets again
(see [api.md](api.md#mirror-conversations)).

## User-curated tables (independent of captures)

These are **not** rebuilt by ingest and survive `--reset`. They are joined onto the
rollups at query time, so editing them never requires re-aggregating packets.

### `names`

| column       | type    | meaning             |
| ------------ | ------- | ------------------- |
| `ip`         | VARCHAR | primary key         |
| `given_name` | VARCHAR | friendly label      |

Loaded by `netnoder-names` from `data/names/*.csv`. Used for node labels (the UI
**Labels** toggle) and search.

### `port_services`

| column        | type    | meaning                          |
| ------------- | ------- | -------------------------------- |
| `port`        | INTEGER | part of primary key              |
| `transport`   | VARCHAR | `tcp` \| `udp` \| `sctp` \| … (part of primary key) |
| `description` | VARCHAR | human-readable service name      |

Loaded by `netnoder-portmap`. Joined onto `conversations.server_port` to show a
service description in the connection drawer (falls back to "No Service Info").

## Bookkeeping

### `manifest`

Tracks which captures have been extracted, for **resumable** ingest: `path` (PK),
`size`, `mtime`, `status` (`done`/`error`), row count, shard path, and timestamp. A
capture is re-extracted only if its size/mtime changed or its shard went missing.

## Indexes

Connection lookups are by IP and conversation lookups are by connection, so the
schema indexes `connections(ip_a)`, `connections(ip_b)`,
`conversations(connection_id)`, and `conversation_ports(connection_id)`. These keep
the neighbour and drill-down queries fast even on large captures.

## Concurrency note

DuckDB allows many read-only readers but only one read-write process. The API opens
the file **read-only**, so multiple API workers are fine — but do **not** run
`netnoder-ingest` against the database while the API is serving it.
