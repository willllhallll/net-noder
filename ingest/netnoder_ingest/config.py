"""Shared paths and tunables, resolved from the environment with sane defaults.

Run ingest from the project root so the default ``./data`` location works, or set
the ``NETNODER_*`` environment variables to override.

There is no port map and no local-subnet configuration: the peer/dissector model
takes protocols straight from tshark and classifies endpoints only as
unicast/multicast/broadcast. There is a single DuckDB store: given-names (from
``NAMES_CSV``) and the layer-colour registry live in it alongside the analytical
tables -- there is no separate API metadata DB.
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("NETNODER_DATA", "./data")).resolve()
# Default home for raw pcap/pcapng capture files (ingest still accepts any path).
CAPTURES_DIR = Path(
    os.environ.get("NETNODER_CAPTURES", str(DATA_DIR / "captures"))
).resolve()
SCRATCH_DIR = Path(
    os.environ.get("NETNODER_SCRATCH", str(DATA_DIR / "scratch"))
).resolve()
# The single DuckDB store: analytical tables + names + layer_colours. Built by
# ingest, served read-only by the API.
DB_PATH = Path(os.environ.get("NETNODER_DB", str(DATA_DIR / "netnoder.duckdb"))).resolve()
# CSV source of truth for user given-names (header: ip,given_name). Auto-loaded by
# ingest if present; also loadable on its own via `netnoder-names`.
NAMES_CSV = Path(os.environ.get("NETNODER_NAMES", str(DATA_DIR / "names.csv"))).resolve()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
