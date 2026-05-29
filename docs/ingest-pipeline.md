# Ingest pipeline

How raw capture files become the aggregated tables the app serves. Code lives in
[ingest/netnoder_ingest/](../ingest/netnoder_ingest/) and is driven by the
`netnoder-ingest` CLI.

## Where to put files

| What                         | Where                                | Notes                                                        |
| ---------------------------- | ------------------------------------ | ------------------------------------------------------------ |
| Capture files (`.pcap`, `.pcapng`, `.cap`) | `data/captures/` (any depth) | Scanned recursively. You can also point ingest at any other path. |
| IP → friendly name CSVs      | `data/names/` (one or more `*.csv`)  | Header `ip,given_name`. See [database.md](database.md#names). |
| Port → service registry CSV  | `data/portmap/portmap.csv`           | Header `port,transport,description,…`.                        |
| The DuckDB store (output)    | `data/netnoder.duckdb`               | Created on first run.                                         |
| Parquet scratch (transient)  | `data/scratch/`                      | One shard per capture; safe to delete.                        |

All of these locations are overridable with `NETNODER_*` environment variables
(see the [README](../README.md#configuration-env-vars)).

## Running it

```bash
netnoder-ingest data/captures/        # a directory (recursive) or a single file
```

Useful flags:

- `-j/--jobs N` — parallel extraction workers (default: all CPU cores).
- `-m/--memory 4GB` — DuckDB memory budget for the aggregation step.
- `--reset` — wipe the rollup tables, the manifest, and the parquet shards before
  starting. Does **not** touch `names` or `port_services` (those are user data).
- `--aggregate-only` — skip extraction and just rebuild the rollups from the shards
  already on disk (handy after changing aggregation logic).

## The four stages

```
.pcap ──tshark──> transient TSV ──DuckDB──> parquet shard ──DuckDB GROUP BY──> rollup tables
        (extract)                (normalise)               (aggregate, all shards at once)
```

### 1. Discover & resume — [cli.py](../ingest/netnoder_ingest/cli.py)

`discover()` finds every capture under the input path. Before extracting, each file
is checked against the `manifest` table: if it was already done and its size + mtime
still match (and its shard still exists), it is skipped. So re-running after adding a
few new captures only processes the new ones.

### 2. Extract — [tshark.py](../ingest/netnoder_ingest/tshark.py) + [extract.py](../ingest/netnoder_ingest/extract.py)

Each capture is handed to a worker process. The worker runs `tshark` once to dump a
fixed set of per-packet fields (timestamp, length, Ethernet dest, IPv4/IPv6 src/dst,
protocol, and TCP/UDP ports) as a tab-separated file. Name resolution is disabled
(`-n`) for speed, and only the first occurrence of each field is taken so a tunnelled
packet stays one row.

That TSV is immediately read by a throwaway in-memory DuckDB connection, normalised
into typed columns (collapsing IPv4/IPv6 into single `src_ip`/`dst_ip` columns,
casting ports to integers, recording `is_tcp`/`is_udp` flags), and written to a
compressed **parquet shard** in `data/scratch/`. The TSV is then deleted — so peak
extra disk is bounded by the single largest capture, not the whole set.

The shard name is a hash of the capture's absolute path, so re-extracting a file
overwrites its shard cleanly. Workers never raise: a failure is reported back as an
`error` manifest row and the run continues.

### 3. Aggregate — [aggregate.py](../ingest/netnoder_ingest/aggregate.py)

This is where raw packets become the graph. **In simple terms:** point DuckDB at
*all* the parquet shards at once and run a few big `GROUP BY` queries, then throw the
packets away — only the rollups are kept.

DuckDB does the grouping out-of-core (spilling to disk inside `data/`), so it stays
within the `--memory` budget even for hundreds of millions of packets.

The logic, step by step:

1. A `flows` view classifies every packet using the SQL fragments in
   [transform.py](../ingest/netnoder_ingest/transform.py): protocol name, cast type
   (unicast / multicast / broadcast), and — the key decision — **which side is the
   server** (see below). It also fixes a canonical pair orientation: `ip_a` is always
   the lexicographically smaller IP, `ip_b` the larger, so a packet and its reply land
   in the same group.
2. `connections` — group by the `(ip_a, ip_b)` pair, summing packets and bytes in each
   direction (`a2b`/`b2a`). One row per pair of endpoints.
3. `conversations` — group by `(connection, protocol, server_port, cast_type,
   server_is_a)`. One row per *service endpoint* within a connection.
4. `conversation_ports` — the finest breakdown, per *reply port*, kept only as the
   **top 50 by bytes** per conversation (`_REPLY_KEEP`). This bounds the table; the
   true distinct count is still recorded on the conversation as `reply_port_count`.
5. `endpoints` — union of src and dst sides, grouped by IP, for per-node totals.
   `_classify_endpoints()` then tags each IP as local/external and unicast/multicast/
   broadcast using Python's `ipaddress`.

All five inserts run inside a single transaction (`DELETE` + `INSERT`, so the schema
and indexes from [schema.sql](../ingest/netnoder_ingest/schema.sql) survive a rebuild).

#### How "the server" is chosen

The only signal used is the **IANA ephemeral port range** (49152–65535). A port in
that range is a throwaway client port; any other port is treated as a service port.
For each packet (`SERVICE_IS_SRC_SQL`):

- Non-TCP/UDP (no ports) → no service side; the conversation is undirected.
- Exactly one side on a service port → that side is the server. A client→server
  packet and its server→client reply both resolve to the same endpoint, so they stay
  in one conversation carrying both directions.
- Both sides on service ports → a **service-to-service** flow; the canonical (lower)
  port is chosen as the server, again so forward and reverse packets agree.
- Neither side on a service port → fall back to the lower port (ties broken by lower IP).

This is what gives every conversation a `client → server` arrow and lets the API
re-present a service-to-service flow as a role-flipped "mirror" without re-reading
packets (see [api.md](api.md#mirror-conversations)).

### 4. Refresh user metadata — [names.py](../ingest/netnoder_ingest/names.py) + [portmap.py](../ingest/netnoder_ingest/portmap.py)

After aggregation, ingest auto-loads `data/names/` and `data/portmap/portmap.csv` if
present. Both are **decoupled from packets**: they live in their own tables and are
joined at query time, so you can edit them and reload without re-ingesting.

```bash
netnoder-names              # reload IP → name CSV(s) (default data/names/)
netnoder-portmap            # reload the port → service registry
# both take a path argument and a --merge flag (upsert instead of replace)
```

## Adding extra scripts

New ingest-side commands follow the pattern of `names.py` / `portmap.py`:

1. Write a module under `ingest/netnoder_ingest/` exposing a `main(argv=None) -> int`.
   Use `from .cli import connect` to get a DuckDB connection with the schema applied.
2. Register it under `[project.scripts]` in
   [ingest/pyproject.toml](../ingest/pyproject.toml), e.g.
   `netnoder-foo = "netnoder_ingest.foo:main"`.
3. Re-install the package (`pip install -e ingest`) so the console script appears.

If the script populates a new table, add its `CREATE TABLE IF NOT EXISTS` to
[schema.sql](../ingest/netnoder_ingest/schema.sql) so it is created on connect.
