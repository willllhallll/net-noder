# Ingest pipeline

`netnoder-ingest` turns raw capture files into the aggregated DuckDB store. It is a
classic **map → combine → reduce** pipeline, sized for the real workload shape
(100s of GB across 100s of *small* files): the working set is always one small file,
never the whole corpus, so per-file work can be accurate and the only global step is
an out-of-core DuckDB merge.

## File flow

```
data/captures/*.pcap ──┐
                       ├─(per file, parallel)─> data/scratch/<hash>.parquet  (flow grain)
                       ┘
                                  │ (one process)
                                  └─> data/netnoder.duckdb  (flows + flow_layers + views)
```

- **Captures** live under `data/captures/` (override with `NETNODER_CAPTURES`); any
  path can be passed to the CLI.
- **Scratch** parquet shards live under `data/scratch/` (`NETNODER_SCRATCH`). One
  shard per input file, named by a hash of the file path so re-runs overwrite cleanly.
- **Analytical store** is `data/netnoder.duckdb` (`NETNODER_DB`), fully rebuilt by
  ingest and served read-only by the API.

Packet-level data is never persisted — only the flow-grain aggregate and its rollups.

## Stages

### 1. map — tshark dissection ([tshark.py](../ingest/netnoder_ingest/tshark.py))

Each file is read with **two-pass** dissection (`tshark -r file -2 -n -T fields`),
extracting per-packet fields including **`frame.protocols`** — the dissector's
verdict on the real protocol stack (e.g. `eth:ethertype:ip:tcp:tls`). Two-pass
improves reassembly and late protocol identification; it is memory-bounded by a
single small file, so it is safe at corpus scale. Name resolution is off (`-n`).

`dump_protocols()` also captures `tshark -G protocols` — the authoritative
abbrev↔name reference list, persisted once per run for validation.

### 2. combine — per-file pre-aggregation ([extract.py](../ingest/netnoder_ingest/extract.py))

A worker process pipes tshark output into a transient TSV, then an in-memory DuckDB
collapses it to **flow grain** and writes one parquet shard:

- IPv4/IPv6 source/dest and TCP/UDP ports are unified; the L4 token comes from
  TCP/UDP presence or the IP protocol number ([transform.py](../ingest/netnoder_ingest/transform.py)).
- The 5-tuple is **canonicalised** to `ip_a <= ip_b`, with `port_a` always on the
  `ip_a` side. A packet whose source is `ip_a` counts toward the `a2b` direction.
- Rows are grouped by `(ip_a, ip_b, l4_proto, port_a, port_b)`, summing per-direction
  packet/byte counters and taking `arg_max(frame.protocols, depth)` — the **deepest
  stack** seen for that 5-tuple. `depth` rides along for the cross-file merge.

This collapses millions of packets into a handful of flow rows *before* anything
global happens — the biggest lever for keeping the merge cheap. The TSV is deleted
immediately, so peak extra disk is bounded by the largest single pcap.

### 3. reduce — merge + derived views ([aggregate.py](../ingest/netnoder_ingest/aggregate.py))

One DuckDB process reads every shard and rebuilds the store atomically (bounded RAM
via `memory_limit` + a spill `temp_directory`):

1. **Merge** shards to the canonical flow grain, taking `arg_max(stack, depth)`
   globally so the deepest stack wins across files.
2. Assign `connection_id` per IP-pair (ranked by total bytes) and a surrogate
   `flow_id` per 5-tuple → insert `flows`.
3. **Explode** each flow's deepest stack into `flow_layers`, dropping the
   `ethertype` noise token and re-indexing layers contiguously per flow.
4. Build `endpoints` (each flow contributes to both its IPs; `kind` = uni/multi/
   bcast by IP range; `degree` = distinct peers).
5. Build `connection_protocols` (presence-based per `(connection, layer, l4_proto)`,
   generic link/network tokens stripped) and `connection_layers` (full distinct
   stack, for the graph's tier filter).
6. Build `connections` (per-direction counters; distinct port + protocol counts;
   `cast_type` derived from the two endpoints' kinds).

### orchestration ([cli.py](../ingest/netnoder_ingest/cli.py))

`discover → manifest-skip → parallel extract → aggregate → persist protocols → seed
colours → load names`. The `manifest` table records each file's size/mtime/shard so
unchanged files are skipped on re-run; aggregation always rebuilds the rollups from
all current shards. After aggregation it persists the `tshark -G protocols` dump,
seeds/extends the `layer_colours` registry ([palette.py](../ingest/netnoder_ingest/palette.py),
first-seen-wins), and loads given-names from `names.csv` if present.

`--reset` wipes the analytical tables + shards but **preserves** `names` and
`layer_colours` (durable metadata — only a deleted DuckDB file resets them);
`--aggregate-only` re-aggregates existing shards.

## Loading names

Given-names are user metadata, kept in the single store's `names` table. The CSV
(`data/names.csv`, header `ip,given_name`) is the source of truth — load it with
`netnoder-names` ([names.py](../ingest/netnoder_ingest/names.py)), or let
`netnoder-ingest` auto-load it. The load replaces the table from the file, and the
table survives `--reset`.

## Adding ingest scripts

The package exposes `netnoder-ingest` and `netnoder-names`. There is **no** port-map
loader — protocols come solely from the dissector. New batch tooling should follow
the same discover→combine→reduce shape and write parquet shards at the flow grain.
