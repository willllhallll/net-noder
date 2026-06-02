# net-noder

Ingest raw `tcpdump`/Wireshark capture files (`.pcap` / `.pcapng`) and explore the
network as an interactive graph: circles are IP **endpoints**, lines are
**connections** (any traffic between a pair). Click a connection to see the real
**protocols** spoken across it, then click a protocol to see every **port** in use.

This is a **peer/dissector** tool, by design:

- **Real protocols, not port guesses.** What two endpoints actually speak comes
  straight from tshark's dissector (`frame.protocols`) — so TLS on port 8443 reads
  as `tls`, DNS on 5333 reads as `dns`, and an unrecognised stream on 443 stays
  `tcp`. The port never names the protocol.
- **Peers, not clients/servers.** Every endpoint and every port is an equal peer.
  There are no roles, no ephemeral-port filtering, and no IANA port map. Direction
  is surfaced only as neutral **A→B / B→A** byte/packet counters; you judge it.
- **No local/remote.** Endpoints are classified only as **unicast / multicast /
  broadcast** by well-known IP ranges. The tool makes no remote-vs-local claim.
- **Every port kept.** Nothing is filtered or capped in storage; the UI compacts
  large port sets for display only.

Built for large captures (100GB+): packets are **aggregated on ingest** into an
embedded DuckDB store at flow grain, and the web app only ever fetches **small,
filtered subgraphs**, so the browser never sees raw packets and memory stays bounded.

## Architecture

```
.pcap files ──tshark -2──> parquet shards ──DuckDB GROUP BY──> flows + flow_layers
                                                                      │
                                            derived: endpoints / connections /
                                            connection_layers / connection_protocols
                                                                      │
                                            FastAPI (bounded queries) ── React + Cytoscape.js UI
```

- **ingest/** — `netnoder-ingest` CLI: two-pass `tshark` dissection → compact parquet
  shards at flow grain → DuckDB merge + derived rollups. Parallel across files and
  resumable (a `manifest` table tracks completed files).
- **server/** — `netnoder-api` FastAPI service. Opens the single DuckDB store
  **read-only** (analytical tables + given-names + the layer-colour registry).
- **web/** — Vite + React + Cytoscape.js front-end.
- **data/** — capture files under `data/captures/`, the DuckDB files, and parquet
  scratch (all git-ignored).
- **docs/** — design docs for each subsystem (see [Documentation](#documentation)).

## Setup

The dev container provisions everything automatically (`.devcontainer/post-create.sh`):
tshark, a Python `.venv` with both packages installed editable, and the web deps.
The venv's `bin` is on `PATH`, so the commands below work directly. To set up
manually outside the container, run that script.

## Usage

1. **Ingest** a capture (file or directory, scanned recursively):

   ```bash
   netnoder-ingest data/captures/        # or a single file
   # options: -j/--jobs N, -m/--memory 4GB, --reset, --aggregate-only
   ```

2. **Serve** the API:

   ```bash
   netnoder-api                          # http://localhost:8000  (/docs for OpenAPI)
   ```

3. **Open the UI**:

   - Dev: `cd web && npm run dev` → http://localhost:5173 (proxies `/api` to :8000).
   - Single URL: `cd web && npm run build`, then `netnoder-api` serves the built app
     at http://localhost:8000.

   Or just run `bash scripts/dev.sh` (the `/dev` skill) to bring up both at once.

4. **Name your endpoints** (optional). Put labels in `data/names.csv` (header
   `ip,given_name`) and load them into the store:

   ```bash
   netnoder-names                 # defaults to data/names.csv
   # netnoder-ingest also auto-loads data/names.csv if present
   ```

   Names live in the single DuckDB store (the `names` table), joined to nodes at
   query time. The CSV is the source of truth, and ingest **preserves** `names`
   across `--reset`, so re-ingesting captures never loses them. They're shown in the
   UI (label toggle / search) read-only.

## Run encapsulated with Docker

To run the tool cleanly outside the dev container — with the same runtimes but none of
the developer dependencies (no Claude Code, Prettier, editor extensions, editable
installs, or Vite dev server) — use the bundled multi-stage `Dockerfile` and
`docker-compose.yml`. The image builds the web app with Node 22, then runs everything on
Python 3.12, with FastAPI serving the built UI **and** `/api` from a single port (8000).
`./data` is bind-mounted, so the DuckDB store and your captures live on the host and
survive rebuilds.

```bash
# 1. Put pcap/pcapng files in ./data/captures/, then dissect them into the store
docker compose run --rm ingest data/captures/   # same flags as netnoder-ingest

# 2. Serve the UI + API on one URL
docker compose up                                # open http://localhost:8000
```

`docker compose down` stops it; the data volume persists. (The `ingest` service is under
a `tools` profile, so `up` only starts the app.)

## Data model

| table                  | meaning                                                                   |
| ---------------------- | ------------------------------------------------------------------------- |
| `flows`                | FACT: one row per canonical 5-tuple; key → per-direction measures         |
| `flow_layers`          | the dissected protocol stack, one row per (flow, layer)                   |
| `endpoints`            | one row per IP (node): totals, first/last seen, kind, degree              |
| `connections`          | one row per IP pair (edge): neutral A↔B counters, cast_type, counts       |
| `connection_layers`    | distinct layer tokens per connection (graph tier filter)                  |
| `connection_protocols` | per-(pair, layer) presence rollup (the edge-click drawer)                 |
| `protocols`            | `tshark -G protocols` reference dump (validation; never drives colour)    |
| `names`                | user-curated IP → given name, loaded from `names.csv` (preserved on reset) |
| `vlans`                | user-curated VLAN subnet definitions, loaded from `vlans.csv` (preserved on reset) |
| `layer_colours`        | persisted tier + colour per layer, first-seen-wins (preserved on reset)   |
| `broadcast_domain_colours` | persisted colour per broadcast domain (VLANs + Public/Unassigned/Multicast/Broadcast), first-seen-wins (preserved on reset) |
| `manifest`             | ingest bookkeeping for resumable runs                                     |

The base of record is two relations — `flows` (key → measures) and `flow_layers`
(the deepest dissected stack, one token per layer) — chosen so the schema is in
**ETNF/5NF** with no redundant tuples. Everything else is a materialised view over
them, rebuilt atomically each aggregation. Given-names (`names`), VLAN definitions
(`vlans`) and the colour registries (`layer_colours`, `broadcast_domain_colours`) live in
the **same** store but are **preserved across `--reset`** — only lost if the DuckDB file
itself is deleted. See
[docs/database.md](docs/database.md).

## Configuration (env vars)

- `NETNODER_DATA` (default `./data`), `NETNODER_CAPTURES` (default `data/captures/`),
  `NETNODER_DB` (the single store), `NETNODER_NAMES` (names CSV, default
  `data/names.csv`), `NETNODER_SCRATCH`
- `NETNODER_HOST` / `NETNODER_PORT` for the API
- `NETNODER_WEB_DIST` — directory of the built web app to serve at `/` (defaults to the
  in-repo `web/dist`; set by the Docker image to its copied bundle)

## Documentation

Design docs for each subsystem live in [docs/](docs/):

- [Ingest pipeline](docs/ingest-pipeline.md) — file flow, the map/combine/reduce
  stages, and the flow-grain aggregation.
- [Database structure](docs/database.md) — the `flows`/`flow_layers` base, the
  derived views, and the ETNF rationale.
- [API structure](docs/api.md) — the four views, the layer/colour registry, and the
  single read-only store.
- [Web app UI flow](docs/ui-flow.md) — the graph → focus → connection → ports flow,
  the tier stepper, and the colour palette.
