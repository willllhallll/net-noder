"""Shared paths and tunables, resolved from the environment with sane defaults.

Run ingest from the project root so the default ``./data`` location works, or set
the ``NETNODER_*`` environment variables to override.
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
DB_PATH = Path(os.environ.get("NETNODER_DB", str(DATA_DIR / "netnoder.duckdb"))).resolve()
# Directory of IP -> given-name CSVs (header: ip,given_name). May hold several files.
NAMES_DIR = Path(os.environ.get("NETNODER_NAMES", str(DATA_DIR / "names"))).resolve()

# Extra subnets (comma-separated CIDRs) to treat as "local" beyond the usual
# RFC1918 / loopback / link-local ranges, e.g. "10.0.0.0/8,192.0.2.0/24".
EXTRA_LOCAL_SUBNETS = [
    s.strip() for s in os.environ.get("NETNODER_LOCAL_SUBNETS", "").split(",") if s.strip()
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
