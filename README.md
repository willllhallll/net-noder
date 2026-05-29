# net-noder

Ingest raw `tcpdump`/Wireshark capture files (`.pcap` / `.pcapng`) and explore the
network as an interactive graph: circles are IP **endpoints**, lines are
**connections** (any traffic between a pair). Click a connection to drill into its
**conversations** (per service/port, with a client → server arrow), then click a
conversation to see its ephemeral reply ports and uni/multi/broadcast breakdown.

Built for large captures (100GB+): packets are **aggregated on ingest** into an
embedded DuckDB store, and the web app only ever fetches **small, filtered
subgraphs** (top talkers, then expand from a node), so the browser never sees raw
packets and memory stays bounded.

## Architecture

```
.pcap files ──tshark──> parquet shards ──DuckDB GROUP BY──> endpoints / connections / conversations
                                                                      │
                                              FastAPI (bounded queries) ── React + Cytoscape.js UI
```

- **ingest/** — `netnoder-ingest` CLI: streams packets via `tshark`, writes compact
  parquet shards, then aggregates them in DuckDB. Parallel across files and
  resumable (a `manifest` table tracks completed files).
- **server/** — `netnoder-api` FastAPI service exposing graph queries.
- **web/** — Vite + React + Cytoscape.js front-end.
- **data/** — capture files under `data/captures/`, the DuckDB file, and parquet
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

2. **Name your IPs** (optional). Friendly names are user metadata, kept in a separate
   `names` table — edit and reload anytime without re-ingesting; survives `--reset`.

   ```bash
   # Put one or more CSVs (header: ip,given_name) in data/names/, then:
   netnoder-names            # defaults to data/names/  (--merge to upsert)
   # or point at a specific file/dir: netnoder-names path/to/names.csv
   ```

   `netnoder-ingest` also auto-loads `data/names/` if present. In the UI, the
   **Labels** toggle switches node labels between IP and given name (falls back to IP
   where no name is known); names are also searchable.

   You can likewise load a port → service reference (header:
   `port,transport,description`) so conversations show a service name; like names it
   is decoupled from packets and reloadable without re-ingesting:

   ```bash
   netnoder-portmap          # defaults to data/portmap/portmap.csv  (--merge to upsert)
   ```

3. **Serve** the API:

   ```bash
   netnoder-api                          # http://localhost:8000  (/docs for OpenAPI)
   ```

4. **Open the UI**:

   - Dev: `cd web && npm run dev` → http://localhost:5173 (proxies `/api` to :8000).
   - Single URL: `cd web && npm run build`, then `netnoder-api` serves the built app
     at http://localhost:8000.

## Data model

| table                | meaning                                                                          |
| -------------------- | -------------------------------------------------------------------------------- |
| `endpoints`          | one row per IP (node): totals, first/last seen, local?, kind                     |
| `connections`        | one row per IP pair (edge): per-direction packet/byte counts                     |
| `conversations`      | per connection: protocol, server port, cast type, per-direction counts, server side |
| `conversation_ports` | bounded top-N reply ports per conversation                                       |
| `names`              | user-curated IP → given name (independent of captures)                           |
| `port_services`      | user-curated port → service description (independent of captures)                |
| `manifest`           | ingest bookkeeping for resumable runs                                            |

Ingest picks the **server side** of each conversation
([`transform.py`](ingest/netnoder_ingest/transform.py)) using the IANA ephemeral
range (49152–65535): a port outside it is a service port and wins; if both or neither
side is a service port, the lower port wins. That gives every conversation a direction
(client → server) and identifies which endpoint owns the service port
(`conversations.server_is_a`). When *both* ends are services, the flow is stored once
and the API surfaces the reverse view as a role-flipped "mirror" arrow.

See [docs/database.md](docs/database.md) for the full schema.

Cast type is derived per packet from the destination (L2 group/broadcast bit, with
IP-range fallbacks for L3-only captures). "Local" uses RFC1918/loopback/link-local
plus any CIDRs in `NETNODER_LOCAL_SUBNETS`.

## Configuration (env vars)

- `NETNODER_DATA` (default `./data`), `NETNODER_CAPTURES` (default `data/captures/`), `NETNODER_DB`, `NETNODER_SCRATCH`, `NETNODER_NAMES` (default `data/names/`), `NETNODER_PORTMAP` (default `data/portmap/portmap.csv`)
- `NETNODER_LOCAL_SUBNETS` — extra "local" CIDRs, comma-separated
- `NETNODER_HOST` / `NETNODER_PORT` for the API

## Documentation

Design docs for each subsystem live in [docs/](docs/):

- [Ingest pipeline](docs/ingest-pipeline.md) — where files go, the four ingest stages,
  how aggregation works, and how to add ingest scripts.
- [Database structure](docs/database.md) — the three-layer model and every table.
- [API structure](docs/api.md) — the models and how raw rows are shaped into graph JSON.
- [Web app UI flow](docs/ui-flow.md) — how to use the explorer, view by view.
