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
import random
import socket
import struct
import sys
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


FLOWS = CURATED + multiproto_flows() + fanout_flows() + bulk_flows()


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/captures/sample.pcap")
    out.parent.mkdir(parents=True, exist_ok=True)

    ts = 1_700_000_000
    records = []
    for count, src, dst, proto, sp, dp, payload in FLOWS:
        data = frame(src, dst, proto, sp, dp, payload)
        for _ in range(count):
            records.append((ts, data))
            ts += 1

    with open(out, "wb") as f:
        # classic pcap global header (big-endian), Ethernet link type
        f.write(struct.pack("!IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for t, data in records:
            f.write(struct.pack("!IIII", t, 0, len(data), len(data)))
            f.write(data)

    print(f"wrote {len(records)} packets to {out}")


if __name__ == "__main__":
    main()
