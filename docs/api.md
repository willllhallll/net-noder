# API structure

`netnoder-api` is a FastAPI service ([main.py](../server/netnoder_api/main.py))
serving exactly four UI views — graph, endpoint, connection protocols, protocol
ports — plus stats, the layer registry, and search. No roles, no arrows, no IANA
services, no local/remote anywhere.

## One read-only store ([db.py](../server/netnoder_api/db.py))

The API opens the single DuckDB store (`NETNODER_DB`) **read-only** and writes
nothing. That store holds the analytical tables, the `names` table (loaded from
`names.csv` by `netnoder-names`), and the `layer_colours` registry (seeded at
ingest). Given-names are attached to nodes with an ordinary `LEFT JOIN names`, and
`/api/layers` reads tier+colour straight from `layer_colours` — no allocation
happens at request time. Names are therefore edited via the CSV + a re-load, not
through the API.

## Endpoints

```
GET  /api/health
GET  /api/stats          -> { endpoints, connections, flows, layers, total_pkts, total_bytes, first/last_seen }
GET  /api/layers         -> [{ layer, tier, colour, count }]   (observed set; tier + colour
                            from the PERSISTED registry — first-seen-wins, never reassigned)
GET  /api/graph?cap=     -> { nodes[], edges[], meta }
       Node: ip, kind, given_name, total_pkts, total_bytes, degree, first/last_seen
       Edge: connection_id, ip_a, ip_b, pkts, bytes, pkts_a2b/b2a, bytes_a2b/b2a,
             layers: string[]  (full token set, for client filter+colour), first/last
       meta: { capped, cap, shown_endpoints, total_endpoints }   (top-N by bytes)
GET  /api/node/{ip}                  -> EndpointDetail (node fields + degree)
GET  /api/node/{ip}/neighbors?limit= -> Graph (focus subgraph, same shape)
GET  /api/connection?a=&b=           -> ConnectionProtocols { connection_id, ip_a, ip_b,
                                          name_a/b, kind_a/b, pkts/bytes a2b+b2a,
                                          protocols: [{ layer, l4_proto,
                                            pkts/bytes a2b+b2a,  -- presence-based
                                            port_count, first/last }] }
GET  /api/connection/ports?a=&b=&layer=&limit=&offset=
                                     -> { connection_id, ip_a, ip_b, layer, total,
                                          ports: [{ port_a, port_b,
                                            pkts/bytes a2b+b2a,  -- per-direction
                                            first/last }] }
                                        ALL port-pairs for the layer; paginated, NO storage cap
GET  /api/search?q=                  -> Node[]  (IP prefix or given-name substring)
```

(There are no name-mutation endpoints: names are managed via `names.csv` +
`netnoder-names`, and the store is read-only to the API.)

Models are in [models.py](../server/netnoder_api/models.py); queries in
[queries.py](../server/netnoder_api/queries.py). The canonical pair order matches
ingest's lexical `ip_a <= ip_b`, so `a`/`b` are reproduced with `sorted([a, b])`.

## The protocol-ports query

Canonicalise `a,b` → `connection_id`, then (per-direction counters preserved):

```sql
SELECT f.port_a, f.port_b,
       f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a, f.first_seen, f.last_seen
FROM flows f JOIN flow_layers fl ON fl.flow_id = f.flow_id
WHERE f.connection_id = ? AND fl.layer = ?
ORDER BY (f.bytes_a2b + f.bytes_b2a) DESC
LIMIT ? OFFSET ?;
```

Every port-pair carrying the layer is returned (paginated for transport; the UI
loads all and virtualizes the list) — there is no storage-side cap.

## Layers, tiers, and colour ([palette.py](../ingest/netnoder_ingest/palette.py))

`/api/layers` returns the **observed** layer set with a **tier**, a **colour**, and
an **unresolved** flag — all read straight from the `layer_colours` table; the API
allocates nothing. The registry is seeded at ingest:

- **Tier** (`link` / `network` / `transport` / `application`) drives the UI's tier
  stepper. It comes from a curated map for common tokens, else from the token's
  *modal* stack position (`layer_index`) across all flows.
- **Colour** is *assigned deterministically*, not hashed. Curated **anchors** give
  intuition-bearing protocols a fixed colour (`tcp` blue, `tls` brown, `dns` teal…);
  the long tail draws from a perceptually-distinct categorical palette in allocation
  order, falling back to a golden-angle generator past the palette.
- **`unresolved`** is true for tshark stop-markers (`data`) — payload present but
  unidentified — so the UI can render them as "dissection stopped here" rather than
  as a real protocol.
- **Persistence — a token's colour never changes.** `seed_layer_colours()` writes
  each allocation first-seen-wins: anchors pre-seeded (`seq = -1`); each newly
  observed token takes the next unused slot once. Ingest excludes `layer_colours`
  from `--reset` and never rewrites an existing row, so colours are stable for the
  life of the store (only a deleted DuckDB file resets them).

This palette is consistent everywhere — graph edges, the active-tier legend,
connection-drawer protocol rows, and the ports-drawer accent.
