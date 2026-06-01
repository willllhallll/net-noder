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

# Order matters: extract.py references these positionally as c0..c15.
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
    # TLS handshake facts, read straight from the dissector so the negotiated version
    # is a decoded fact, never guessed. tshark fills these only on handshake frames;
    # extract.py keys the negotiated version off the ServerHello (handshake.type=2).
    "tls.handshake.type",                          # c13 -- 2 = ServerHello
    "tls.handshake.version",                       # c14 -- legacy/negotiated version code
    "tls.handshake.extensions.supported_version",  # c15 -- TLS 1.3 negotiated (0x0304)
    # Per-protocol version fields for the generic `protocol_version`: each present
    # protocol's own decoded version (NOT TLS-specific). extract.py decodes + picks the
    # most-specific non-null one per flow. All standards-registry decodes, no heuristics.
    "quic.version",            # c16 -- QUIC transport version (e.g. 0x00000001)
    "http.response.version",   # c17 -- "HTTP/1.1" etc. (response line)
    "http.request.version",    # c18 -- "HTTP/1.1" etc. (request line)
    "ntp.flags.vn",            # c19 -- NTP version number (3/4)
    "dtls.handshake.version",  # c20 -- DTLS version code (e.g. 0xfefd)
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


def extract_to_tsv(pcap: Path, tsv: Path) -> str | None:
    """Stream tshark output for ``pcap`` into ``tsv``. Raises on tshark failure.

    Returns ``None`` on a clean run, or a short warning string when the run was
    recoverable. A byte-split capture chunk almost always ends mid-packet, so tshark
    exits non-zero with "cut short in the middle of a packet" -- but it has already
    written every *complete* packet to stdout, so the only loss is the single
    truncated trailing packet (picked up at the head of the next chunk). We surface
    that as a warning and keep the data instead of discarding the whole shard.
    """
    cmd = build_command(pcap)
    with open(tsv, "wb") as out:
        proc = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE)
    if proc.returncode == 0:
        return None
    msg = proc.stderr.decode(errors="replace").strip()
    if "cut short in the middle of a packet" in msg:
        return "truncated trailing packet (split-capture boundary)"
    raise RuntimeError(f"tshark failed for {pcap}: {msg[:500]}")


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
