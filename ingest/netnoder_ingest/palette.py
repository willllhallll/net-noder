"""Protocol-layer tiers + colour allocation, persisted at ingest.

The universe of protocol tokens is finite but large; we colour only the *observed*
set, deterministically rather than by hashing:

  * **Tiers** place each token on an OSI-ish axis (link / network / transport /
    application). Common tokens use a curated map; unknown tokens fall back to their
    modal stack position.
  * **Anchors** give intuition-bearing protocols a fixed, hand-picked colour.
  * The **long tail** draws from a perceptually-distinct categorical palette in
    allocation order, falling back to a golden-angle generator past the palette.

This registry is written into the analytical DuckDB (`layer_colours`) on every
ingest, **first-seen-wins**: anchors are seeded once (seq = -1) and each new observed
token takes the next unused slot and keeps it. It is preserved across re-aggregation
and --reset, so a token's colour never changes within the life of a store.
"""
import duckdb

TIER_ORDER = ("link", "network", "transport", "application")

# Curated anchor colours (collision-free by hand). Keys are frame.protocols abbrevs.
ANCHORS: dict[str, str] = {
    "eth":  "#9e9e9e",  # grey   - link
    "ip":   "#43a047",  # green  - network
    "ipv4": "#43a047",  # green  - network (alias some builds emit)
    "ipv6": "#e53935",  # red    - network
    "tcp":  "#1e88e5",  # blue   - transport
    "udp":  "#ffb300",  # amber  - transport
    "tls":  "#6d4c41",  # brown  - application
    "http": "#fb8c00",  # orange - application
    "dns":  "#00897b",  # teal   - application
    "quic": "#8e24aa",  # violet - application
    "ssh":  "#d81b60",  # magenta- application
    # 'data' is tshark's "payload present but unidentified" sentinel (dissection
    # stopped here). It is still real, worthwhile traffic, so it gets its OWN distinct
    # colour (a deep lime, never grey) -- as an anchor it consumes no long-tail slot.
    "data": "#c0ca33",
}

# Tokens that are dissection STOP markers, not real protocols.
UNRESOLVED: frozenset[str] = frozenset({"data"})

# Curated tier map for common tokens (includes the anchors). Anything not listed is
# tiered by its modal layer_index via tier_for().
TIER_MAP: dict[str, str] = {
    # link
    "eth": "link", "sll": "link", "sll2": "link", "llc": "link", "vlan": "link",
    "ppp": "link", "wlan": "link", "loop": "link", "null": "link", "nflog": "link",
    # network
    "ip": "network", "ipv4": "network", "ipv6": "network", "arp": "network",
    # transport (incl. the L4 control protocols)
    "tcp": "transport", "udp": "transport", "sctp": "transport", "dccp": "transport",
    "icmp": "transport", "icmpv6": "transport", "igmp": "transport",
    "gre": "transport", "esp": "transport", "ah": "transport", "ospf": "transport",
    # application (the large, open-ended tier)
    "tls": "application", "ssl": "application", "http": "application",
    "http2": "application", "http3": "application", "dns": "application",
    "mdns": "application", "llmnr": "application", "quic": "application",
    "ssh": "application", "dhcp": "application", "dhcpv6": "application",
    "bootp": "application", "ntp": "application", "snmp": "application",
    "smb": "application", "smb2": "application", "ldap": "application",
    "kerberos": "application", "rdp": "application", "sip": "application",
    "rtp": "application", "ftp": "application", "ftp-data": "application",
    "smtp": "application", "imap": "application", "pop": "application",
    "mysql": "application", "pgsql": "application", "tds": "application",
    "mongo": "application", "redis": "application", "amqp": "application",
    "mqtt": "application", "data": "application", "bittorrent": "application",
}

# Long-tail palette: perceptually-distinct categorical colours, none clashing with
# the anchor hues. Drawn in allocation order.
PALETTE: tuple[str, ...] = (
    "#ff6d00", "#2962ff", "#00bfa5", "#c51162", "#aeea00", "#6200ea",
    "#ffab00", "#0091ea", "#dd2c00", "#64dd17", "#aa00ff", "#00b8d4",
    "#ff4081", "#3d5afe", "#1de9b6", "#f50057", "#76ff03", "#7c4dff",
    "#ffc400", "#00e5ff", "#ff3d00", "#b2ff59", "#e040fb", "#18ffff",
    "#ff80ab", "#536dfe", "#69f0ae", "#ff5252", "#eeff41", "#b388ff",
    "#ffd740", "#84ffff", "#ff8a80", "#8c9eff", "#a7ffeb", "#ff9e80",
    "#cfff95", "#ea80fc", "#80d8ff", "#ffe57f",
)


def tier_for(layer: str, modal_index: int | None) -> str:
    """Tier for a token: curated map first, else its modal stack position."""
    t = TIER_MAP.get(layer)
    if t is not None:
        return t
    if modal_index is None:
        return "application"
    if modal_index <= 0:
        return "link"
    if modal_index == 1:
        return "network"
    if modal_index == 2:
        return "transport"
    return "application"


def _hsl_to_hex(h: float, s: float, l: float) -> str:
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    r, g, b = {
        0: (c, x, 0), 1: (x, c, 0), 2: (0, c, x),
        3: (0, x, c), 4: (x, 0, c), 5: (c, 0, x),
    }[int(h // 60) % 6]
    return "#{:02x}{:02x}{:02x}".format(
        round((r + m) * 255), round((g + m) * 255), round((b + m) * 255)
    )


def colour_for_seq(seq: int) -> str:
    """Colour for long-tail allocation slot `seq` (0-based)."""
    if 0 <= seq < len(PALETTE):
        return PALETTE[seq]
    over = seq - len(PALETTE)
    hue = (over * 137.508) % 360
    light = 0.45 if over % 2 == 0 else 0.6
    return _hsl_to_hex(hue, 0.7, light)


def seed_layer_colours(con: duckdb.DuckDBPyConnection) -> int:
    """Seed/extend `layer_colours` for the observed layers, first-seen-wins.

    Anchors are inserted once (seq = -1). Every observed token not already present
    takes the next unused long-tail slot, allocated in descending-frequency order so
    the busiest new protocols get the lowest (most separated) slots. Existing rows are
    never touched, so colours are stable across ingests. Returns rows newly added.
    """
    # Anchors first (idempotent).
    for layer, colour in ANCHORS.items():
        con.execute(
            "INSERT OR IGNORE INTO layer_colours VALUES (?, ?, ?, -1, ?)",
            [layer, TIER_MAP.get(layer, "application"), colour, layer in UNRESOLVED],
        )

    # Observed tokens with their modal stack position, busiest first.
    observed = con.execute(
        """
        SELECT layer, mode(layer_index) AS modal_index, count(*) AS n
        FROM flow_layers GROUP BY layer ORDER BY n DESC, layer
        """
    ).fetchall()

    added = 0
    for layer, modal_index, _ in observed:
        if con.execute(
            "SELECT 1 FROM layer_colours WHERE layer = ?", [layer]
        ).fetchone():
            continue
        nxt = con.execute(
            "SELECT coalesce(max(seq), -1) + 1 FROM layer_colours WHERE seq >= 0"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO layer_colours VALUES (?, ?, ?, ?, ?)",
            [layer, tier_for(layer, modal_index), colour_for_seq(nxt), nxt,
             layer in UNRESOLVED],
        )
        added += 1
    return added
