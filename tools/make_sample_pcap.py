#!/usr/bin/env python3
"""Write a deterministic Ethernet/IPv4 pcap for testing the ingest pipeline.

This fixture exercises the *peer/dissector* model — there are no client/server
roles, no ephemeral-vs-service split, and no reply-port cap. It is built to make
tshark's dissector (`frame.protocols`) the source of protocol truth:

  * **Dissector-over-IANA (the headline case):** real TLS and DNS payloads are sent
    on *non-standard* ports (TLS on 8443, DNS on 5333). tshark recognises them by
    content, so `frame.protocols` reads `...:tcp:tls` / `...:udp:dns` even though the
    port is not 443 / 53. A control flow on the same kind of port carries no
    recognisable payload and stays `...:tcp` — proving the port never names the
    protocol.
  * **Full layer stacks:** TLS rides over TCP/IP/Eth, DNS over UDP/IP/Eth, etc.
  * **Multi-protocol pairs (real-world mix):** the SAME two endpoints share many flows
    across several protocols at once -- TLS, cleartext HTTP, DNS, NTP, SSH, a UDP media
    stream, and ICMP -- so a connection's flow fan is multi-coloured and the layer
    filter is meaningful (application tier separates tls/http/dns/ntp/ssh; transport
    tier regroups them into tcp/udp/icmp).
  * **Many peer ports per protocol:** one pair exchanges TLS across tens of distinct
    peer ports, so the flows table keeps every port and the ports drawer has volume.
  * **All three kinds:** unicast, multicast (mDNS to 224.0.0.251), and broadcast
    (DHCP to 255.255.255.255), so endpoint `kind` + connection `cast_type` are covered.

Everything is seeded, so re-running produces byte-identical output.

Usage: python tools/make_sample_pcap.py [out.pcap]   (default: data/captures/sample.pcap)
"""
import argparse
import random
import socket
import struct
from pathlib import Path

ETH_SRC = "02:11:11:11:11:11"
ETH_DST = "02:22:22:22:22:22"


def mac(s: str) -> bytes:
    return bytes(int(x, 16) for x in s.split(":"))


def ipv4(src: str, dst: str, proto: int, payload: bytes) -> bytes:
    total = 20 + len(payload)
    hdr = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, total, 0x1234, 0, 64, proto, 0,
        socket.inet_aton(src), socket.inet_aton(dst),
    )
    return hdr + payload


def tcp(sport: int, dport: int, payload: bytes) -> bytes:
    # PSH+ACK set, so tshark treats the segment as carrying data to dissect.
    return struct.pack("!HHIIBBHHH", sport, dport, 0, 0, (5 << 4), 0x18, 65535, 0, 0) + payload


def udp(sport: int, dport: int, payload: bytes) -> bytes:
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def frame(src: str, dst: str, proto: int, sport: int, dport: int, payload: bytes) -> bytes:
    if proto == 6:
        l4 = tcp(sport, dport, payload)
    elif proto == 17:
        l4 = udp(sport, dport, payload)
    else:
        l4 = payload
    eth = mac(ETH_DST) + mac(ETH_SRC) + struct.pack("!H", 0x0800)
    return eth + ipv4(src, dst, proto, l4)


# ---- real protocol payloads (so tshark dissects by content, not by port) -------

def dns_query(name: str = "example.com") -> bytes:
    """A minimal well-formed DNS A-record query (recognised on any UDP port)."""
    qname = b"".join(
        bytes([len(part)]) + part.encode() for part in name.split(".")
    ) + b"\x00"
    header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)  # 1 question, RD set
    return header + qname + struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN


def tls_client_hello() -> bytes:
    """A minimal TLS 1.2 ClientHello record (recognised on any TCP port)."""
    body = (
        b"\x03\x03"        # client_version TLS 1.2
        + b"\x00" * 32     # random
        + b"\x00"          # session id length 0
        + b"\x00\x02\x00\x2f"  # cipher suites length 2 + TLS_RSA_WITH_AES_128_CBC_SHA
        + b"\x01\x00"      # compression: 1 method, null
        + b"\x00\x00"      # extensions length 0
    )
    handshake = b"\x01" + struct.pack("!I", len(body))[1:] + body  # type=client_hello
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake  # record


def dhcp_discover() -> bytes:
    """A minimal BOOTP/DHCP DISCOVER payload (recognised on UDP 67/68)."""
    msg = struct.pack("!BBBBIHH", 1, 1, 6, 0, 0x3903F326, 0, 0x8000)  # op,htype,hlen,hops,xid,secs,flags
    msg += b"\x00" * 16            # ciaddr/yiaddr/siaddr/giaddr
    msg += b"\x02\x11\x11\x11\x11\x11" + b"\x00" * 10  # chaddr (16)
    msg += b"\x00" * 192           # sname + file
    msg += b"\x63\x82\x53\x63"     # magic cookie
    msg += b"\x35\x01\x01"         # option 53: DHCP DISCOVER
    msg += b"\xff"                 # end
    return msg


def http_get() -> bytes:
    """A cleartext HTTP/1.1 request (dissected as http by content, any TCP port)."""
    return b"GET / HTTP/1.1\r\nHost: example.com\r\nUser-Agent: net-noder\r\n\r\n"


def ssh_banner() -> bytes:
    """An SSH protocol-version banner (dissected as ssh by content, any TCP port)."""
    return b"SSH-2.0-OpenSSH_9.6\r\n"


def ntp_client() -> bytes:
    """A 48-byte NTP client packet (LI=0, VN=4, Mode=3) -> udp:ntp."""
    return bytes([0x23, 0, 0, 0]) + b"\x00" * 44


def icmp_echo() -> bytes:
    """A bare ICMP echo-request header (type 8) -> ip:icmp, no payload, no ports.

    Header-only on purpose: any trailing bytes would dissect as an `icmp:data`
    tail, which our pipeline treats as an unresolved stop-marker."""
    return struct.pack("!BBHHH", 8, 0, 0, 1, 1)  # type, code, csum, id, seq


# Each row: (count, src_ip, dst_ip, ip_proto, sport, dport, payload).
CURATED = [
    # --- DISSECTOR-OVER-IANA: TLS + DNS on NON-standard ports, recognised by content
    (6, "192.168.1.10", "203.0.113.7", 6, 51000, 8443, tls_client_hello()),  # tls on 8443
    (5, "203.0.113.7", "192.168.1.10", 6, 8443, 51000, tls_client_hello()),
    (4, "192.168.1.10", "203.0.113.9", 17, 51001, 5333, dns_query("api.example.net")),  # dns on 5333
    # Control: same shape, NO recognisable payload -> stays plain tcp (port lies).
    (5, "192.168.1.10", "203.0.113.8", 6, 51002, 8080, b"\x00" * 40),

    # --- standard-port equivalents, also content-dissected
    (8, "192.168.1.10", "93.184.216.34", 6, 52000, 443, tls_client_hello()),   # tls on 443
    (7, "93.184.216.34", "192.168.1.10", 6, 443, 52000, tls_client_hello()),
    (3, "192.168.1.10", "192.168.1.1", 17, 50000, 53, dns_query("example.com")),  # dns on 53

    # --- multicast (mDNS) + broadcast (DHCP): exercise kind / cast_type
    (4, "192.168.1.10", "224.0.0.251", 17, 5353, 5353, dns_query("_services._dns-sd._udp.local")),
    (1, "192.168.1.50", "255.255.255.255", 17, 68, 67, dhcp_discover()),
]


def fanout_flows():
    """One pair exchanging TLS across MANY distinct peer ports (no role split).

    72 distinct peer ports on one side, all carrying real TLS on a non-standard
    port (8443), so the flows table keeps every port and the ports drawer has a
    long, virtualized list to render. Peers are just ports — neither side is a
    'server'."""
    flows = []
    peer, host = "192.168.1.20", "151.101.1.69"
    payload = tls_client_hello()
    for k in range(72):
        sp = 49500 + k
        flows.append((1, peer, host, 6, sp, 8443, payload))
        flows.append((1, host, peer, 6, 8443, sp, payload))
    return flows


def multiproto_flows():
    """Endpoint pairs that share MANY flows across SEVERAL full-stack protocols.

    The headline real-world case: the SAME two endpoints talk TLS, cleartext HTTP,
    DNS, NTP, SSH, an (encrypted/unkeyed) UDP media stream, and ICMP -- each as its
    own flow with its own stack. So one connection's flow fan is multi-coloured, the
    application-tier filter separates tls/http/dns/ntp/ssh, and the transport-tier
    filter regroups them into tcp/udp/icmp. Honest dissection: an HTTPS session has
    no decryptable payload here, so it shows as `tls`; the media stream stays `udp`.
    Seeded/curated -> byte-identical output.
    """
    tls = tls_client_hello()
    http = http_get()
    dns = dns_query("host.example.net")
    ntp = ntp_client()
    ssh = ssh_banner()
    media = b"\x00" * 120  # undissectable UDP "media" stream -> stays udp/data

    def pair_mix(a: str, b: str, base: int) -> list:
        """The standard real-world protocol mix between one pair (both directions)."""
        rows = []

        def both(proto, ap, bp, payload, na=3, nb=2):
            rows.append((na, a, b, proto, ap, bp, payload))
            rows.append((nb, b, a, proto, bp, ap, payload))

        both(6, base + 0, 443, tls)        # TLS handshake first
        both(6, base + 1, 8443, tls)       # ...then more TLS flows = the HTTPS session
        both(6, base + 2, 8443, tls)
        both(6, base + 3, 80, http)        # cleartext HTTP
        both(17, base + 4, 53, dns)        # a DNS lookup
        both(17, base + 5, 123, ntp)       # NTP time sync
        both(6, base + 6, 22, ssh)         # SSH
        both(17, base + 7, 16384, media, na=6, nb=6)  # UDP media stream (stays udp)
        rows.append((4, a, b, 1, 0, 0, icmp_echo()))   # ICMP ping (portless)
        rows.append((4, b, a, 1, 0, 0, icmp_echo()))
        return rows

    pairs = [
        ("192.168.1.10", "198.51.100.10", 50100),
        ("192.168.1.10", "198.51.100.20", 50200),   # 192.168.1.10 = a rich hub
        ("192.168.1.11", "93.184.216.34", 50300),
        ("10.0.0.5", "10.0.0.6", 40000),            # internal pair
        ("172.16.5.5", "172.16.5.10", 41000),
        ("192.168.1.12", "203.0.113.50", 51000),
    ]
    flows = []
    for a, b, base in pairs:
        flows += pair_mix(a, b, base)
    return flows


def bulk_flows():
    """~50 extra endpoints, each pair carrying 2-3 distinct protocols, for a denser
    graph full of multi-protocol connections.

    Seeded for deterministic output. Each target offers a menu of real-protocol
    variants; every chosen client<->target pair draws several, so connections across
    the graph are multi-coloured (not single-protocol)."""
    rng = random.Random(1700)
    tls = tls_client_hello()
    dns = dns_query("host.example.org")
    http = http_get()
    ssh = ssh_banner()
    ntp = ntp_client()
    blob = b"\x00" * 64
    # peer_ip -> menu of (ip_proto, port, payload) protocol variants.
    targets = [
        ("104.16.132.50", [(6, 8443, tls), (6, 443, tls), (6, 80, http), (17, 5333, dns)]),
        ("8.8.8.8", [(17, 53, dns), (17, 123, ntp)]),
        ("10.0.0.5", [(6, 9000, blob), (6, 22, ssh), (6, 443, tls)]),  # blob -> tcp:data
        ("10.0.0.6", [(6, 8443, tls), (6, 80, http), (17, 53, dns)]),
        ("192.168.1.1", [(17, 53, dns), (17, 123, ntp)]),
    ]
    clients = (
        [f"192.168.1.{100 + i}" for i in range(30)]
        + [f"10.0.1.{1 + i}" for i in range(10)]
        + [f"172.105.10.{20 + i}" for i in range(10)]
    )
    flows = []
    sp = 49200  # monotonic source port -> every flow is a distinct, deterministic 5-tuple
    for client in clients:
        for ip, menu in rng.sample(targets, k=rng.randint(2, 3)):
            for proto, port, payload in rng.sample(menu, k=min(len(menu), rng.randint(2, 3))):
                for _ in range(rng.randint(1, 3)):
                    sp += 1
                    n = rng.randint(2, 6)
                    flows.append((n, client, ip, proto, sp, port, payload))
                    flows.append((n, ip, client, proto, port, sp, payload))
    return flows


# ---- multi-VLAN enterprise topology (mirrors data/vlans.csv) --------------------
#
# net-noder has NO 802.1Q tag in the pipeline: VLANs are pure IP-vs-CIDR membership
# (server/netnoder_api/classify.py). So "many VLANs" == many distinct /24 subnets, each
# also declared in data/vlans.csv. This block is the authoritative subnet list: keep it
# in lock-step with vlans.csv, and only name IPs in names.csv that are actually emitted.

# (vlan_id, base_ip /24, label) -- subnet mask is 255.255.255.0 for all.
VLANS = [
    (10,  "10.10.10.0",   "VLAN 10 — Workstations"),
    (20,  "10.20.20.0",   "VLAN 20 — Servers"),
    (30,  "10.30.30.0",   "VLAN 30 — VoIP"),
    (40,  "192.168.40.0", "VLAN 40 — Guest WiFi"),
    (50,  "192.168.50.0", "VLAN 50 — IoT"),
    (60,  "172.16.60.0",  "VLAN 60 — DMZ"),
    (70,  "172.16.70.0",  "VLAN 70 — Management"),
    (100, "10.100.0.0",   "VLAN 100 — Engineering"),
    (200, "10.200.0.0",   "VLAN 200 — Finance"),
    (300, "10.30.3.0",    "VLAN 300 — Lab"),
]

# Real, globally-routable IPs so ingest's RDAP/WHOIS resolves real org names.
PUBLIC = [
    "1.1.1.1", "1.0.0.1",          # Cloudflare
    "8.8.8.8", "8.8.4.4",          # Google
    "9.9.9.9",                      # Quad9
    "208.67.222.222",               # OpenDNS / Cisco
    "140.82.121.4",                 # GitHub
    "13.107.42.14",                 # Microsoft
    "17.253.144.10",                # Apple
    "142.250.80.46",                # Google
    "151.101.1.69",                 # Fastly
    "104.16.132.50",                # Cloudflare
]


def vlan_topology_flows(rng: random.Random) -> list:
    """A multi-VLAN enterprise: per-VLAN hosts with intra/inter-VLAN, public, broadcast
    and multicast traffic. Subnets mirror the authoritative VLANS list (and vlans.csv).

    Reuses the real-payload helpers so tshark dissects by content, not port. Seeded via
    the passed RNG for byte-identical output.
    """
    tls, http, ssh, ntp = tls_client_hello(), http_get(), ssh_banner(), ntp_client()
    media = b"\x00" * 120          # undissectable UDP "media" -> stays udp/data
    icmp, dhcp = icmp_echo(), dhcp_discover()
    multicast_groups = ["224.0.0.251", "239.255.255.250", "224.0.0.1"]  # mDNS, SSDP, all-hosts

    def net(base: str) -> str:     # "10.20.20.0" -> "10.20.20"
        return base.rsplit(".", 1)[0]

    # Build a host inventory per VLAN (~15-25 hosts, .10 upward).
    hosts = {
        vid: [f"{net(base)}.{10 + i}" for i in range(rng.randint(15, 25))]
        for vid, base, _ in VLANS
    }
    servers, mgmt = hosts[20], hosts[70]

    flows: list = []

    def both(a, b, proto, ap, bp, payload, na=3, nb=2):
        flows.append((na, a, b, proto, ap, bp, payload))
        if nb:
            flows.append((nb, b, a, proto, bp, ap, payload))

    sp = 49152  # ephemeral source-port walker -> distinct, deterministic 5-tuples
    def nextport() -> int:
        nonlocal sp
        sp = 49152 if sp >= 65000 else sp + 1
        return sp

    for vid, base, _label in VLANS:
        p = net(base)
        gw, bcast = f"{p}.1", f"{p}.255"
        vhosts = hosts[vid]
        dns = dns_query(f"host.vlan{vid}.example.net")

        # Intra-VLAN: every host -> gateway (TLS/DNS/NTP/ICMP) and a few peers (mixed).
        for h in vhosts:
            both(h, gw, 6,  nextport(), 443, tls)
            both(h, gw, 17, nextport(), 53,  dns)
            both(h, gw, 17, nextport(), 123, ntp)
            flows.append((4, h, gw, 1, 0, 0, icmp))
            for peer in rng.sample(vhosts, k=min(3, len(vhosts))):
                if peer == h:
                    continue
                proto, port, payload = rng.choice(
                    [(6, 445, tls), (6, 22, ssh), (6, 80, http), (17, 16384, media)]
                )
                both(h, peer, proto, nextport(), port, payload)

        # Broadcast: DHCP DISCOVER (limited) + a directed broadcast (subnet all-ones).
        flows.append((2, vhosts[0], "255.255.255.255", 17, 68, 67, dhcp))
        flows.append((2, vhosts[1 % len(vhosts)], bcast, 17, 138, 138, media))

        # Multicast: mDNS / SSDP from a few hosts (one-directional, like the real thing).
        for h in vhosts[:3]:
            grp = rng.choice(multicast_groups)
            both(h, grp, 17, 5353, 5353,
                 dns_query("_services._dns-sd._udp.local"), na=2, nb=0)

        # Inter-VLAN: client VLANs reach the Servers VLAN (TLS/HTTP/DNS).
        if vid in (10, 40, 50, 100, 200):
            for h in rng.sample(vhosts, k=min(8, len(vhosts))):
                srv = rng.choice(servers)
                both(h, srv, 6,  nextport(), 443, tls)
                both(h, srv, 6,  nextport(), 80,  http)
                both(h, srv, 17, nextport(), 53,  dns)

        # VLAN -> public Internet (drives live WHOIS at ingest).
        for h in rng.sample(vhosts, k=min(6, len(vhosts))):
            pub = rng.choice(PUBLIC)
            both(h, pub, 6,  nextport(), 443, tls)
            both(h, pub, 17, nextport(), 53,  dns)

    # Management VLAN administers every gateway (SSH + HTTPS) -> central hub nodes.
    for _vid, base, _label in VLANS:
        gw, admin = f"{net(base)}.1", rng.choice(mgmt)
        both(admin, gw, 6, nextport(), 22,  ssh)
        both(admin, gw, 6, nextport(), 443, tls)

    return flows


def build_flows(seed: int) -> list:
    """The full flow set: the curated dissector/cast-type core + the multi-VLAN topology."""
    rng = random.Random(seed)
    return (
        CURATED
        + multiproto_flows()
        + fanout_flows()
        + bulk_flows()
        + vlan_topology_flows(rng)
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a deterministic, multi-VLAN Ethernet/IPv4 sample pcap.",
    )
    ap.add_argument("out", nargs="?", default="data/captures/sample.pcap",
                    help="output pcap path (default: data/captures/sample.pcap)")
    ap.add_argument("--target-mb", type=float, default=250.0,
                    help="approximate output size in MB; flows are scaled to hit it "
                         "(default: 250)")
    ap.add_argument("--seed", type=int, default=1700,
                    help="RNG seed for the VLAN topology (default: 1700)")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    flows = build_flows(args.seed)

    # Scale per-flow packet counts so the file lands near --target-mb. The graph stays a
    # few hundred endpoints (rich but bounded); flows just get heavier. 16 = the pcap
    # per-packet record header that precedes every frame on disk.
    REC_HDR = 16
    base_bytes = sum(
        count * (REC_HDR + len(frame(src, dst, proto, sp, dp, payload)))
        for count, src, dst, proto, sp, dp, payload in flows
    )
    target_bytes = int(args.target_mb * 1_000_000)
    mult = max(1, round(target_bytes / base_bytes)) if base_bytes else 1

    ts = 1_700_000_000
    written = 0
    total = 24  # classic pcap global header
    with open(out, "wb") as f:
        f.write(struct.pack("!IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for count, src, dst, proto, sp, dp, payload in flows:
            data = frame(src, dst, proto, sp, dp, payload)
            for _ in range(count * mult):
                f.write(struct.pack("!IIII", ts, 0, len(data), len(data)))
                f.write(data)
                ts += 1
                written += 1
                total += REC_HDR + len(data)

    print(f"wrote {written} packets ({total / 1e6:.1f} MB, x{mult} scale) to {out}")


if __name__ == "__main__":
    main()
