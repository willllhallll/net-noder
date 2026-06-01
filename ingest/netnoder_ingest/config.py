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
# CSV source of truth for VLAN definitions (header: vlan_id,base_ip,subnet_mask[,label]).
# Auto-loaded by ingest if present; also loadable on its own via `netnoder-vlans`.
VLANS_CSV = Path(os.environ.get("NETNODER_VLANS", str(DATA_DIR / "vlans.csv"))).resolve()

# RDAP/WHOIS name resolution: globally-routable IPs are looked up against ARIN's RDAP
# registry at ingest and cached (with a TTL) in the `ip_whois` table. Tunables:
#   WHOIS_ENABLED        - master switch for the auto-resolve step in `netnoder-ingest`.
#   WHOIS_TTL_DAYS       - re-resolve a successful row once it is older than this.
#   WHOIS_ERROR_TTL_DAYS - retry a failed/empty row sooner than a successful one.
#   WHOIS_MIN_DELAY      - minimum seconds between requests (politeness/rate-limit).
#   WHOIS_TIMEOUT        - per-request socket timeout in seconds.
#   WHOIS_MAX_RETRIES    - retries on 429/503/network errors (with backoff) per IP.
WHOIS_ENABLED = os.environ.get("NETNODER_WHOIS", "true").lower() in ("1", "true", "yes")
WHOIS_TTL_DAYS = int(os.environ.get("NETNODER_WHOIS_TTL_DAYS", "30"))
WHOIS_ERROR_TTL_DAYS = int(os.environ.get("NETNODER_WHOIS_ERROR_TTL_DAYS", "7"))
WHOIS_MIN_DELAY = float(os.environ.get("NETNODER_WHOIS_MIN_DELAY", "0.5"))
WHOIS_TIMEOUT = float(os.environ.get("NETNODER_WHOIS_TIMEOUT", "5.0"))
WHOIS_MAX_RETRIES = int(os.environ.get("NETNODER_WHOIS_MAX_RETRIES", "3"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
