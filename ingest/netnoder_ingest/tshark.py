"""Thin wrapper around the tshark CLI to dump the fields we care about as TSV.

We extract only the columns needed to build connections + conversation/cast detail,
disable name resolution (-n) for speed, and take the first occurrence of each field
(-E occurrence=f) so tunnelled/multi-layer packets stay one row per packet.
"""
import shutil
import subprocess
from pathlib import Path

# Order matters: extract.py references these positionally as c0..c12.
FIELDS = [
    "frame.time_epoch",  # c0
    "frame.len",         # c1
    "eth.dst",           # c2
    "ip.src",            # c3
    "ip.dst",            # c4
    "ipv6.src",          # c5
    "ipv6.dst",          # c6
    "ip.proto",          # c7
    "ipv6.nxt",          # c8
    "tcp.srcport",       # c9
    "tcp.dstport",       # c10
    "udp.srcport",       # c11
    "udp.dstport",       # c12
]


def have_tshark() -> bool:
    return shutil.which("tshark") is not None


def build_command(pcap: Path) -> list[str]:
    cmd = [
        "tshark", "-r", str(pcap),
        "-n",                       # no name resolution
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
