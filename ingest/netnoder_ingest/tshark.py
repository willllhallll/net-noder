"""Thin wrapper around the tshark CLI.

Two jobs:
  * ``extract_to_tsv`` dumps the per-packet fields we need as TSV, including
    ``frame.protocols`` -- the dissector's verdict on what is *actually* spoken,
    which is the whole point of the peer/dissector model (the port is never trusted
    to name the protocol).
  * ``dump_protocols`` captures the authoritative protocol/abbrev reference list
    (``tshark -G protocols``) so we can validate + tier the observed tokens.

We disable name resolution (-n) for speed, take the first occurrence of each field
(-E occurrence=f), and run **two-pass** dissection (-2) so reassembly and late
protocol identification land in ``frame.protocols``. Two-pass is memory-bounded by a
single (small) capture file, never the whole corpus, so it is safe at scale.
"""
import shutil
import subprocess
from pathlib import Path

# Order matters: extract.py references these positionally as c0..c12.
FIELDS = [
    "frame.time_epoch",  # c0
    "frame.len",         # c1
    "ip.src",            # c2
    "ip.dst",            # c3
    "ipv6.src",          # c4
    "ipv6.dst",          # c5
    "ip.proto",          # c6
    "ipv6.nxt",          # c7
    "tcp.srcport",       # c8
    "tcp.dstport",       # c9
    "udp.srcport",       # c10
    "udp.dstport",       # c11
    "frame.protocols",   # c12 -- the dissected stack, e.g. eth:ethertype:ip:tcp:tls
]


def have_tshark() -> bool:
    return shutil.which("tshark") is not None


def build_command(pcap: Path) -> list[str]:
    cmd = [
        "tshark", "-r", str(pcap),
        "-n",                       # no name resolution
        "-2",                       # two-pass: better reassembly + late protocol ID
        "-T", "fields",
        "-E", "separator=/t",       # tab-separated (none of our fields contain tabs)
        "-E", "occurrence=f",       # first occurrence only
    ]
    for f in FIELDS:
        cmd += ["-e", f]
    return cmd


def extract_to_tsv(pcap: Path, tsv: Path) -> None:
    """Stream tshark output for ``pcap`` into ``tsv``. Raises on tshark failure."""
    cmd = build_command(pcap)
    with open(tsv, "wb") as out:
        proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"tshark failed for {pcap}: {msg}")


def dump_protocols() -> list[tuple[str, str, str]]:
    """Return ``(abbrev, name, short_name)`` for every protocol in the tshark build.

    ``tshark -G protocols`` emits tab-separated rows: descriptive name, short name,
    filter abbrev (= the token that appears in ``frame.protocols``), then flags. We
    key on the abbrev. This is the authoritative reference/validation list; it never
    drives colour by itself.
    """
    proc = subprocess.run(
        ["tshark", "-G", "protocols"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace")[:500]
        raise RuntimeError(f"tshark -G protocols failed: {msg}")
    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, short_name, abbrev = parts[0], parts[1], parts[2]
        if abbrev:
            rows.append((abbrev.strip().lower(), name.strip(), short_name.strip()))
    return rows
