# API structure

A small read-only **FastAPI** service over the aggregated DuckDB store. Code is in
[server/netnoder_api/](../server/netnoder_api/). Run it with `netnoder-api`; the
interactive OpenAPI docs are at <http://localhost:8000/docs>.

```
DuckDB (read-only) ── queries.py (SQL → dicts) ── models.py (Pydantic) ── FastAPI JSON
```

## What the API is for

The browser must never load the whole graph. Every endpoint here returns a
**bounded** result — overall stats, a capped host graph, the neighbours of one node,
or the detail of one connection — so response size stays small no matter how large
the capture is. The heavy lifting already happened at ingest; the API just shapes
rollup rows into JSON.

## Layout

| File         | Responsibility                                                           |
| ------------ | ------------------------------------------------------------------------ |
| [db.py](../server/netnoder_api/db.py)        | Opens the DuckDB file **read-only** (lazily, one shared connection).     |
| [queries.py](../server/netnoder_api/queries.py) | All SQL. Each function takes a cursor and returns plain dicts.        |
| [models.py](../server/netnoder_api/models.py)   | Pydantic response models — also generate the OpenAPI schema.         |
| [main.py](../server/netnoder_api/main.py)     | FastAPI routes; thin wrappers that call a query and return its result.   |

`main.py` also mounts the built web app (`web/dist/`) at `/` when present, so in
production the whole tool is one URL. `/api/*` routes are registered first so they
take precedence. CORS is wide open because this is a local, single-user tool.

## Endpoints

| Method & path                     | Returns          | Purpose                                              |
| --------------------------------- | ---------------- | ---------------------------------------------------- |
| `GET /api/health`                 | —                | Liveness + DB path/existence.                        |
| `GET /api/stats`                  | `Stats`          | Totals for the header strip.                         |
| `GET /api/graph?cap=N`            | `Graph`          | The whole host graph, capped at `N` endpoints.       |
| `GET /api/node/{ip}`              | `NodeDetail`     | One endpoint + its degree.                           |
| `GET /api/node/{ip}/neighbors`    | `Graph`          | Subgraph of one node's top connections.              |
| `GET /api/connection?a=&b=`       | `ConnectionDetail` | One connection and all its conversations.          |
| `GET /api/conversation/ports`     | `ReplyPorts`     | Top reply ports for one conversation.                |
| `GET /api/search?q=`              | `list[Node]`     | Find endpoints by IP prefix or name substring.       |

If the database file is missing, the first query raises **503** with a message
pointing at the DB path ("Run ingest first").

## The models

Defined in [models.py](../server/netnoder_api/models.py). They mirror the
[three-layer data model](database.md#the-three-layer-model) but reshaped for the UI:

- **`Stats`** — endpoint/connection/conversation counts, total packets/bytes, and the
  capture's overall time span.
- **`Node`** — one endpoint as a graph node, with `given_name` joined in from `names`.
  **`NodeDetail`** adds `degree` (number of connections).
- **`Edge`** — a connection as an *undirected* graph edge. Carries `pkts`/`bytes`
  totals, the dominant `cast`, and an inline preview of its top conversations
  (`conversations: [EdgeConversation]`) plus an `extra` count of the rest. This
  preview is what lets the host graph label an edge ("tcp/443, udp/53, +4 more")
  without a second request.
- **`Graph`** — `{ nodes, edges }`, with optional **`GraphMeta`** describing host-view
  truncation (see the cap below).
- **`Conversation`** — traffic toward one service endpoint: protocol, `server_port`,
  `cast_type`, per-direction counts, `reply_port_count`, `server_is_a` (which side is
  the server), and a joined `service` description. **`ConnectionDetail`** wraps the
  connection's two endpoints (with names) and its list of conversations.
- **`ReplyPorts`** / **`ReplyPort`** — the bounded list of ports talking to a
  conversation's service port, with a `truncated` flag and the true `total`.

## How the raw rows become JSON

The point of `queries.py` is to translate stored rollup rows into the **shapes the
graph UI wants**. The main transformations:

### Canonical columns → graph elements

Stored rows use `ip_a`/`ip_b` + `*_a2b`/`*_b2a` (see
[canonical orientation](database.md#canonical-pair-orientation)). `_assemble()` turns
each connection row into an `Edge` with summed `pkts`/`bytes` and `source`/`target`
endpoints, and gathers the distinct node IPs to fetch in one batch. The
client→server *direction* is not decided here — the host-view edge is undirected; the
UI resolves direction per conversation from `server_is_a`.

### Names & service descriptions joined at query time

`given_name` (from `names`) and the service `description` (from `port_services`) are
`LEFT JOIN`ed in every relevant query rather than baked into the rollups. That is what
lets you edit those CSVs and reload them without re-aggregating packets.

### Inline edge previews

`_edge_extras()` computes, per connection, the dominant cast and the **top 3
conversations by bytes** (with a count of any beyond that). This rides along on the
graph response so edges can show useful labels immediately.

### The host-view cap

`full_graph()` returns every connection when the capture is small. Above
`MAX_GRAPH_NODES` (10,000) it keeps only the highest-traffic endpoints and the
connections among them, and sets `meta.capped` so the UI can explain that the rest is
still reachable via drill-down and search. Nothing is deleted — it just isn't drawn
all at once.

### Mirror conversations

A normal flow has one server side and many *ephemeral* reply ports. But when **both**
ends use service ports (e.g. two daemons talking), ingest stores it as a single
canonical conversation keyed on the lower port. `_mirror_conversations()` detects
this — any reply port **outside** the ephemeral range (49152–65535) is itself a
service — and synthesises a second, role-flipped conversation keyed on the peer's
port. Both arrows carry the same bytes from opposite viewpoints. Crucially this is
derived from the directional `conversation_ports` rows, so **no packet data is
re-read or duplicated**.

`reply_ports()` handles the same duality: for a stored conversation it returns its
recorded reply ports; for a mirror (which has no stored row) it looks up the canonical
server ports that talked to this peer port, with the orientation flipped back.

> The ephemeral range constant lives in **both** `queries.py` and
> `ingest/.../transform.py` and the two must stay in sync — a reply port is judged a
> "real service" the same way at ingest and at query time.

### Safe search

`search()` matches an IP as a **prefix** (`192.168` → that subnet) and a hostname or
given name as a case-insensitive **substring**, with `LIKE` wildcards escaped so user
input can't inject pattern metacharacters. Results are capped and ranked by bytes.

## Configuration

The API reads `NETNODER_DB` for the database path and `NETNODER_HOST` / `NETNODER_PORT`
for where to bind (defaults `127.0.0.1:8000`). See the
[README](../README.md#configuration-env-vars).
