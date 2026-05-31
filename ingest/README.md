# netnoder-ingest

Turns raw `.pcap`/`.pcapng` capture files into the aggregated, flow-grain DuckDB
store the API serves. Peer/dissector model: protocols come **only** from tshark's
`frame.protocols`; there are no client/server roles, no ephemeral-port filtering, and
no IANA port map.

## Install / run

Installed editable in the dev container; otherwise `pip install -e ingest/`.

```bash
netnoder-ingest data/captures/        # a directory (scanned recursively) or one file
# -j/--jobs N        parallel extraction workers (default: all cores)
# -m/--memory 4GB    DuckDB memory limit for the merge
# --reset            wipe tables + shards before ingest
# --aggregate-only   skip extraction; re-aggregate existing shards
```

Requires `tshark` on `PATH` (Wireshark ≥ 4.x).

## Pipeline (map → combine → reduce)

1. **map** ([tshark.py](netnoder_ingest/tshark.py)) — two-pass dissection
   (`tshark -2`) extracting per-packet fields incl. `frame.protocols`.
2. **combine** ([extract.py](netnoder_ingest/extract.py)) — per file, an in-memory
   DuckDB pre-aggregates packets to **flow grain** and writes one parquet shard,
   keeping the deepest stack per 5-tuple (`arg_max(stack, depth)`).
3. **reduce** ([aggregate.py](netnoder_ingest/aggregate.py)) — one DuckDB process
   merges all shards into `flows` + `flow_layers` and rebuilds the derived views
   (`endpoints`, `connections`, `connection_layers`, `connection_protocols`),
   out-of-core (memory_limit + temp_directory spill).

`cli.py` orchestrates discover → manifest-skip → parallel extract → aggregate →
persist the `tshark -G protocols` reference. The `manifest` table makes re-runs
resumable. See [../docs/ingest-pipeline.md](../docs/ingest-pipeline.md) and
[../docs/database.md](../docs/database.md) for details.

## Names + colours

There is one DuckDB store. Given-names live in its `names` table — load them from a
CSV (`data/names.csv`, header `ip,given_name`):

```bash
netnoder-names                 # defaults to data/names.csv
```

`netnoder-ingest` auto-loads the CSV if present and seeds the `layer_colours`
registry. Both `names` and `layer_colours` are **preserved across `--reset`** (only
a deleted DuckDB file resets them).

## Config

`NETNODER_DATA` (`./data`), `NETNODER_CAPTURES`, `NETNODER_SCRATCH`, `NETNODER_DB`
(the single store), `NETNODER_NAMES` (names CSV, default `data/names.csv`).
