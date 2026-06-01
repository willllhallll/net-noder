"""Resolve globally-routable IPs to their RDAP "name" and cache them in `ip_whois`.

For every public (globally-routable unicast) IP seen in the captures we look up the
top-level ``name`` field from ARIN's RDAP registry --
``https://rdap.arin.net/registry/ip/<ip>`` -- which returns the owning org/netblock handle
(e.g. ``3.166.65.96`` -> ``AMAZON-CF``). ARIN transparently redirects to the owning RIR
(RIPE/APNIC/LACNIC/AFRINIC) for non-ARIN space, and the ``name`` field is common across
RIRs, so a single endpoint covers the whole address space (IPv4 and IPv6).

Results are cached in the `ip_whois` table with a per-row ``resolved_at`` timestamp, so a
later ingest only re-resolves rows older than the TTL (``WHOIS_TTL_DAYS`` for hits, the
shorter ``WHOIS_ERROR_TTL_DAYS`` for misses/errors). Lookups are throttled
(``WHOIS_MIN_DELAY``) and retried with backoff on 429/503/network errors so a busy RDAP
server or a single bad IP never aborts an ingest.

This is the machine-generated sibling of `names.py`: distinct table, joined to nodes at
query time, and -- like names/vlans -- both auto-run by `netnoder-ingest` and runnable on
its own via `netnoder-whois` (with ``--force`` to ignore the TTL).
"""
import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from ipaddress import ip_address
from pathlib import Path

import duckdb

from . import config

_RDAP_URL = "https://rdap.arin.net/registry/ip/{ip}"
_USER_AGENT = "net-noder/0.2.0 (RDAP name resolution; +https://github.com/willhall/net-noder)"
# HTTP statuses worth retrying (transient): rate-limit + common server-side hiccups.
_RETRYABLE = frozenset((429, 500, 502, 503, 504))


def _retry_after(err: urllib.error.HTTPError) -> float | None:
    """Parse a Retry-After header as integer seconds, if present and numeric."""
    val = err.headers.get("Retry-After") if err.headers else None
    if val is None:
        return None
    try:
        return max(0.0, float(val))
    except (TypeError, ValueError):
        return None  # HTTP-date form -- fall back to exponential backoff


class RdapResolver:
    """Throttled, retrying RDAP client. One instance per run holds the rate-limit clock."""

    def __init__(
        self,
        min_delay: float = config.WHOIS_MIN_DELAY,
        timeout: float = config.WHOIS_TIMEOUT,
        max_retries: int = config.WHOIS_MAX_RETRIES,
    ):
        self.min_delay = min_delay
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0

    def _throttle(self) -> None:
        """Sleep so consecutive requests are at least `min_delay` apart."""
        wait = self._last_request + self.min_delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get_name(self, ip: str) -> tuple[str | None, str]:
        """Single RDAP request -> (name|None, status). Raises only for retryable cases."""
        req = urllib.request.Request(
            _RDAP_URL.format(ip=ip),
            headers={"Accept": "application/rdap+json", "User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip(), "ok"
        return None, "notfound"

    def resolve(self, ip: str) -> tuple[str | None, str]:
        """Resolve one IP to (name|None, status) -- 'ok' | 'notfound' | 'error'.

        Never raises: transient failures are retried with backoff up to `max_retries`,
        then reported as 'error' so the caller can cache and move on."""
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                return self._get_name(ip)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None, "notfound"  # no RDAP record -- negative cache, no retry
                if e.code not in _RETRYABLE or attempt == self.max_retries:
                    return None, "error"
                backoff = _retry_after(e)
            except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, ValueError):
                if attempt == self.max_retries:
                    return None, "error"
                backoff = None
            time.sleep(backoff if backoff is not None else 2.0 ** attempt)
        return None, "error"


def _candidate_ips(con: duckdb.DuckDBPyConnection, force_refresh: bool) -> list[str]:
    """Unicast endpoint IPs needing resolution, filtered to globally-routable ones.

    Without --force, an IP is a candidate only if it is absent from `ip_whois` or its
    `resolved_at` has aged past the relevant TTL (a longer one for hits, a shorter one
    for misses/errors so failures retry sooner). `is_global` then drops VLAN/private/
    CGNAT/reserved space that ARIN cannot answer for.
    """
    if force_refresh:
        rows = con.execute(
            "SELECT ip FROM endpoints WHERE kind = 'unicast'"
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT e.ip
            FROM endpoints e
            LEFT JOIN ip_whois w ON w.ip = e.ip
            WHERE e.kind = 'unicast'
              AND (
                w.ip IS NULL
                OR w.resolved_at IS NULL
                OR (w.status = 'ok'  AND w.resolved_at < now() - INTERVAL (?) DAY)
                OR (w.status <> 'ok' AND w.resolved_at < now() - INTERVAL (?) DAY)
              )
            """,
            [config.WHOIS_TTL_DAYS, config.WHOIS_ERROR_TTL_DAYS],
        ).fetchall()

    out = []
    for (ip,) in rows:
        try:
            if ip_address(ip).is_global:
                out.append(ip)
        except ValueError:
            continue  # unparseable address -- not resolvable
    return out


def _upsert(con: duckdb.DuckDBPyConnection, ip: str, name: str | None, status: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO ip_whois (ip, name, status, resolved_at) "
        "VALUES (?, ?, ?, now())",
        [ip, name, status],
    )


def resolve_whois(con: duckdb.DuckDBPyConnection, force_refresh: bool = False) -> int:
    """Resolve all due public IPs to RDAP names and upsert them into `ip_whois`.

    Returns the number of IPs that resolved to a name. Prints `[i/n]` progress in the
    same style as the rest of the ingest CLI -- throttling makes this slow, so the
    per-IP line keeps it visible."""
    ips = _candidate_ips(con, force_refresh)
    if not ips:
        print("RDAP names: nothing to resolve (all within TTL)")
        return 0

    print(
        f"resolving {len(ips)} public IP(s) to RDAP names "
        f"(TTL {config.WHOIS_TTL_DAYS}d, {config.WHOIS_MIN_DELAY}s/req) ..."
    )
    resolver = RdapResolver()
    n_ok = n_none = n_err = 0
    for i, ip in enumerate(ips, 1):
        name, status = resolver.resolve(ip)
        _upsert(con, ip, name, status)
        if status == "ok":
            n_ok += 1
            tag, shown = "ok ", name
        elif status == "notfound":
            n_none += 1
            tag, shown = "---", "(no name)"
        else:
            n_err += 1
            tag, shown = "ERR", "(error)"
        print(f"[{i}/{len(ips)}] {tag} {ip} -> {shown}")

    print(f"RDAP names: {n_ok} resolved, {n_none} without a name, {n_err} errors "
          f"({len(ips)} total)")
    return n_ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="netnoder-whois",
        description="Resolve public IPs to RDAP names and cache them in the store.",
    )
    ap.add_argument(
        "-f", "--force", action="store_true",
        help="re-resolve every global unicast IP, ignoring the TTL cache",
    )
    args = ap.parse_args(argv)

    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    con.execute((Path(__file__).parent / "schema.sql").read_text())
    try:
        resolve_whois(con, force_refresh=args.force)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
