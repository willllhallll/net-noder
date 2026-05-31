# Database structure

**One DuckDB store** (`data/netnoder.duckdb`, `NETNODER_DB`), built by ingest and
served **read-only** by the API. Schema in
[schema.sql](../ingest/netnoder_ingest/schema.sql). It holds three kinds of table:

- **Rebuildable analytical data** — `flows`, `flow_layers`, and the derived views;
  wiped + rebuilt on every aggregation / `--reset`.
- **Durable metadata** — `names` (loaded from `names.csv`) and `layer_colours` (the
  persisted tier+colour registry). These are **preserved across `--reset`** and
  re-aggregation; they are only lost if the DuckDB file itself is deleted.
- **Reference / bookkeeping** — `protocols`, `manifest`.

There is no separate API metadata store: the API writes nothing.

The model is **peer-to-peer**: no client/server roles, no ephemeral filtering, no
IANA port map, no local/remote. Protocol identity comes solely from the tshark
dissector. Direction is recorded only as neutral per-direction counters.

## The base of record: `flows` + `flow_layers`

Because we keep the **full layer set** *and* **every port**, the protocol stack is
multi-valued per flow. Splitting the fact from its layer membership keeps the schema
normalised (see ETNF below).

```
flows                              -- FACT: one row per canonical 5-tuple; key -> measures
  flow_id        BIGINT  PK        -- surrogate
  connection_id  BIGINT            -- FK -> connections
  l4_proto       VARCHAR           -- 'tcp'|'udp'|'icmp'|'gre'|... (from ip.proto)
  port_a/port_b  INTEGER           -- real ports (NULL for portless L4); port_a is on ip_a
  pkts_a2b, bytes_a2b              -- per-direction counters (neutral A->B)
  pkts_b2a, bytes_b2a              -- per-direction counters (neutral B->A)
  first_seen, last_seen  DOUBLE
  -- ALT KEY (UNIQUE): (connection_id, l4_proto, port_a, port_b)

flow_layers                        -- the dissected stack, one row per (flow, layer)
  flow_id        BIGINT            -- FK -> flows
  layer_index    SMALLINT          -- position in the stack (0=link ... n=app)
  layer          VARCHAR           -- single token: 'eth','ip','tcp','tls','http',...
  PRIMARY KEY (flow_id, layer_index)
  -- ALT KEY: (flow_id, layer)
```

The **deepest** `frame.protocols` stack seen for a 5-tuple (the superset
`tcp ⊂ tcp:tls ⊂ tcp:tls:http`) is split into `flow_layers`. The `ethertype` noise
token is dropped and the remaining tokens are re-indexed contiguously from 0.

## Derived views (rebuilt atomically each aggregation)

```
endpoints (nodes)                connections (edges, peer pairs)
  ip PRIMARY KEY                   connection_id PRIMARY KEY
  total_pkts, total_bytes          ip_a, ip_b            -- ip_a <= ip_b, NO roles
  first_seen, last_seen            pkts_a2b, bytes_a2b    -- neutral per-direction
  kind (uni/multi/bcast)           pkts_b2a, bytes_b2a
  degree                           cast_type             -- derived from endpoint kinds
                                   protocol_count, port_count, first/last_seen

connection_layers                connection_protocols  -- THE edge-click view
  connection_id                    connection_id
  layer                            layer       -- 'tcp','tls','http','dns','quic',...
  -- full distinct stack           l4_proto
  -- (incl eth/ip) for the         pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a  -- presence-based
  -- graph tier filter             port_count, first_seen, last_seen
                                   -- = flows ⋈ flow_layers GROUP BY (conn, layer, l4)
                                   --   [generic link/network tokens stripped]
```

`connection_protocols` counts are **presence-based, not a partition** — a TLS/HTTP
flow's bytes count under `tcp`, `tls`, and `http`. The view reads as "which protocol
layers are present, and how much traffic involved each" (the UI shows them as
overlapping layers, not a 100%-summing breakdown).

`cast_type` is *not* stored per flow — it is functionally determined by the pair, so
it lives on `connections`, derived from the two endpoints' `kind`.

## Reference + bookkeeping

```
protocols (abbrev PK, name, short_name)   -- tshark -G protocols dump; validation only
manifest  (path, size, mtime, status,...) -- resumable ingest bookkeeping
```

## Durable metadata (same store, preserved across --reset)

```
names         (ip PK, given_name)                       -- loaded from names.csv by
                                                        -- `netnoder-names`; joined at query time
layer_colours (layer PK, tier, colour, seq, unresolved) -- first-seen-wins colour registry;
                                                        -- anchors seeded with seq = -1;
                                                        -- unresolved=TRUE for stop-markers ('data')
```

`netnoder-ingest` excludes both from `--reset` and never re-allocates an existing
`layer_colours` row, so a given name and a token's colour are stable for the life of
the store. Colours are seeded/extended at ingest by
[palette.py](../ingest/netnoder_ingest/palette.py).

## Why this is ETNF (and 5NF for the base relations)

ETNF (Date/Darwen/Fagin) = BCNF *and* every join dependency has a superkey
component — the exact condition for "no redundant tuples".

| Relation | Candidate key(s) | Non-trivial FDs | BCNF | JDs beyond keys | ETNF |
|---|---|---|---|---|---|
| `flows` | `{flow_id}`; alt `{connection_id,l4,port_a,port_b}` | key → measures | ✓ | none | ✓ (5NF) |
| `flow_layers` | `{flow_id,layer_index}`; alt `{flow_id,layer}` | keys determine each other | ✓ (all prime) | none | ✓ (5NF) |
| `names` | `{ip}` | ip → given_name | ✓ | none | ✓ |

The naïve "one row per flow×layer with the measures inline" would make the measures
depend on a *proper subset* of the key (a partial dependency) — failing BCNF with a
textbook update anomaly. Splitting into `flows` + `flow_layers` removes it.

Crucially, the repetition of a token like `tls` across 50,000 ephemeral-port flows is
**not** an ETNF violation: because we *reject* port→protocol, `port_b = 443` does not
functionally determine `layer = tls`. Each flow's layer set is an independent fact, so
no join dependency forces those tuples. (The old IANA model — `443 ⇒ https` — *would*
have introduced exactly that non-superkey FD.) Physical repetition of the token is
absorbed by DuckDB's columnar dictionary/RLE encoding, so it costs ~nothing on disk.
