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
import random

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

# tshark's encryption-boundary tokens -- the layer at which the dissector hands off to a
# sealed channel (and from which the negotiated version is read). The de-noise cut keys on
# this `encrypted` flag (NOT the literal string 'tls'), so it applies uniformly to every
# TLS-family boundary; marking it also lets the UI badge the chip with a 🔒. It never names
# any certificate/ASN.1 token, so the cert-cascade acceptance test is unaffected.
ENCRYPTED: frozenset[str] = frozenset({"tls", "ssl", "dtls"})

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


# --- Broadcast-domain colours -------------------------------------------------
# A dedicated palette for VLAN-subnet broadcast domains, DISJOINT from the protocol
# ANCHORS + PALETTE above so a domain colour can never be confused with a protocol colour.
# These were generated golden-angle in a distinct saturation/lightness band (muted mids,
# vs. the protocol palette's vivid hues) and verified collision-free against both
# protocol sets. Drawn in vlan_id allocation order; past its length the same band's
# golden-angle generator continues (see colour_for_domain_seq).
BROADCAST_DOMAIN_PALETTE: tuple[str, ...] = (
    "#c87541", "#2e9e7a", "#c341c8", "#819e2e", "#417ec8", "#9e2e40",
    "#41c853", "#5e2e9e", "#c8a241", "#2e9d9e", "#c8419f", "#5c9e2e",
    "#4151c8", "#9e422e", "#41c880", "#832e9e",
)

# The fixed (non-VLAN) broadcast domains and their curated colours (seq = -1).
# 'public'/'unassigned' get fresh hues; 'multicast' purple and 'broadcast' red are the
# single source for those domains' colours (served straight to the web, no JS fallback).
FIXED_CATEGORY_COLOURS: dict[str, str] = {
    "public":     "#43a047",  # green
    "unassigned": "#78909c",  # blue-grey
    "multicast":  "#a855f7",  # purple
    "broadcast":  "#ef4444",  # red
}
FIXED_CATEGORY_LABELS: dict[str, str] = {
    "public":     "Public",
    "unassigned": "Unassigned",
    "multicast":  "Multicast",
    "broadcast":  "Broadcast",
}
# Display order for the fixed categories (VLANs sort ahead of these by vlan_id).
FIXED_CATEGORY_ORDER: tuple[str, ...] = ("public", "unassigned", "multicast", "broadcast")


# --- Shake-up schemes ----------------------------------------------------------
# The stable ingest seeding (seed_layer_colours / seed_broadcast_domain_colours) is never touched
# by any of this; these power the on-demand `netnoder-colors` re-allocation only.
#
# Generated schemes step hue by the golden angle and alternate two lightnesses for
# adjacent-slot contrast. Each is (saturation, light_a, light_b, hue_lo, hue_span):
# vivid/pastel span the full wheel; warm/cool constrain hues to an arc. The random
# start within the arc comes from the seed, so a seed fully determines the result.
_SCHEME_BANDS: dict[str, tuple[float, float, float, float, float]] = {
    "vivid":  (0.78, 0.46, 0.58,   0.0, 360.0),  # punchy, full-wheel
    "pastel": (0.42, 0.74, 0.82,   0.0, 360.0),  # soft, light, full-wheel
    "warm":   (0.70, 0.48, 0.60, -25.0, 130.0),  # reds → oranges → yellows
    "cool":   (0.60, 0.46, 0.58, 150.0, 160.0),  # greens → cyans → blues
}

# `shuffle` reuses the curated palettes in a seed-shuffled order; the rest are generated.
SCHEMES: tuple[str, ...] = ("shuffle",) + tuple(_SCHEME_BANDS)


def generate_palette(n: int, scheme: str, seed: int, *, base, overflow) -> list[str]:
    """Return `n` hex colours for a re-allocation `scheme` (member of SCHEMES).

    `shuffle` draws from a seed-shuffled copy of `base` (a curated palette), using
    `overflow(i)` for any slot past its length. The generated schemes ignore
    `base`/`overflow` and lay colours out golden-angle within their `_SCHEME_BANDS`.
    """
    if n <= 0:
        return []
    rnd = random.Random(seed)
    if scheme == "shuffle":
        pool = list(base)
        rnd.shuffle(pool)
        return [pool[i] if i < len(pool) else overflow(i) for i in range(n)]
    sat, la, lb, hue_lo, span = _SCHEME_BANDS[scheme]
    start = rnd.random() * span
    return [
        _hsl_to_hex((hue_lo + (start + i * 137.508) % span) % 360,
                    sat, la if i % 2 == 0 else lb)
        for i in range(n)
    ]


def regenerate_layer_colours(
    con: duckdb.DuckDBPyConnection, *, scheme: str, seed: int, keep_anchors: bool
) -> int:
    """Wipe `layer_colours` and re-allocate it with a fresh `scheme` (a deliberate
    'shake up' of the otherwise-stable registry). Token order mirrors the seeder —
    anchors first, then observed tokens busiest-first — so a normal re-ingest
    afterwards is a no-op (the rows already exist). Returns rows written.

    With `keep_anchors` the curated ANCHORS keep their colour (seq = -1) and only the
    long tail is recoloured; otherwise every token, anchors included, is recoloured.
    """
    observed = con.execute(
        """SELECT layer, mode(layer_index) AS modal_index
           FROM flow_layers GROUP BY layer ORDER BY count(*) DESC, layer"""
    ).fetchall()
    modal = {layer: mi for layer, mi in observed}
    anchors = list(ANCHORS)
    tail = [layer for layer, _ in observed if layer not in ANCHORS]
    order = anchors + tail

    if keep_anchors:
        gen = generate_palette(len(tail), scheme, seed, base=PALETTE, overflow=colour_for_seq)
        colours = dict(ANCHORS)
        seqs = {layer: -1 for layer in anchors}
        for i, layer in enumerate(tail):
            colours[layer], seqs[layer] = gen[i], i
    else:
        gen = generate_palette(len(order), scheme, seed, base=PALETTE, overflow=colour_for_seq)
        colours = {layer: gen[i] for i, layer in enumerate(order)}
        seqs = {layer: i for i, layer in enumerate(order)}

    con.execute("DELETE FROM layer_colours")
    for layer in order:
        con.execute(
            "INSERT INTO layer_colours VALUES (?, ?, ?, ?, ?, ?)",
            [layer, tier_for(layer, modal.get(layer)), colours[layer], seqs[layer],
             layer in UNRESOLVED, layer in ENCRYPTED],
        )
    return len(order)


def regenerate_broadcast_domain_colours(
    con: duckdb.DuckDBPyConnection, *, scheme: str, seed: int, keep_anchors: bool
) -> int:
    """Wipe `broadcast_domain_colours` and re-allocate it with a fresh `scheme`; analogous
    to regenerate_layer_colours. Fixed domains sort ahead of defined VLANs (vlan_id order)
    and labels are preserved. The seed is offset so generated VLAN hues land away from the
    protocol hues (best-effort; exact disjointness only holds for the curated `shuffle`
    palettes). Returns rows written."""
    vlan_rows = con.execute("SELECT vlan_id, label FROM vlans ORDER BY vlan_id").fetchall()
    fixed = list(FIXED_CATEGORY_ORDER)
    vlan_keys = [f"vlan_{vid}" for vid, _ in vlan_rows]
    order = fixed + vlan_keys
    labels = {f"vlan_{vid}": (lbl or f"VLAN {vid}") for vid, lbl in vlan_rows}
    labels.update(FIXED_CATEGORY_LABELS)

    vseed = (seed ^ 0x5A5A5A5A) & 0xFFFFFFFF
    if keep_anchors:
        gen = generate_palette(len(vlan_keys), scheme, vseed,
                               base=BROADCAST_DOMAIN_PALETTE, overflow=colour_for_domain_seq)
        colours = dict(FIXED_CATEGORY_COLOURS)
        seqs = {key: -1 for key in fixed}
        for i, key in enumerate(vlan_keys):
            colours[key], seqs[key] = gen[i], i
    else:
        gen = generate_palette(len(order), scheme, vseed,
                               base=BROADCAST_DOMAIN_PALETTE, overflow=colour_for_domain_seq)
        colours = {key: gen[i] for i, key in enumerate(order)}
        seqs = {key: i for i, key in enumerate(order)}

    con.execute("DELETE FROM broadcast_domain_colours")
    for key in order:
        con.execute(
            "INSERT INTO broadcast_domain_colours VALUES (?, ?, ?, ?)",
            [key, labels[key], colours[key], seqs[key]],
        )
    return len(order)


def colour_for_domain_seq(seq: int) -> str:
    """Colour for broadcast-domain allocation slot `seq` (0-based), unbounded.

    Within the curated `BROADCAST_DOMAIN_PALETTE` it returns the hand-verified hue; past its
    end it continues the same muted-mid band golden-angle so colours stay distinct and the
    scheme never runs out (very large VLAN counts are rare but supported)."""
    if 0 <= seq < len(BROADCAST_DOMAIN_PALETTE):
        return BROADCAST_DOMAIN_PALETTE[seq]
    hue = (seq * 137.508 + 23) % 360
    light = 0.52 if seq % 2 == 0 else 0.40
    return _hsl_to_hex(hue, 0.55, light)


def seed_broadcast_domain_colours(con: duckdb.DuckDBPyConnection) -> int:
    """Seed/extend `broadcast_domain_colours` for the fixed domains + each defined VLAN.

    First-seen-wins: the four fixed domains are inserted once (seq = -1) and each
    `vlans.vlan_id` not already present takes the next unused VLAN slot and keeps it.
    Existing rows are never touched, so a domain's colour is stable across ingests.
    Returns rows newly added."""
    added = 0
    # Fixed domains first (idempotent).
    for key in FIXED_CATEGORY_ORDER:
        before = con.execute(
            "SELECT 1 FROM broadcast_domain_colours WHERE category_key = ?", [key]
        ).fetchone()
        con.execute(
            "INSERT OR IGNORE INTO broadcast_domain_colours VALUES (?, ?, ?, -1)",
            [key, FIXED_CATEGORY_LABELS[key], FIXED_CATEGORY_COLOURS[key]],
        )
        if not before:
            added += 1

    # Each defined VLAN, in vlan_id order, takes the next free slot.
    vlan_rows = con.execute(
        "SELECT vlan_id, label FROM vlans ORDER BY vlan_id"
    ).fetchall()
    for vlan_id, label in vlan_rows:
        key = f"vlan_{vlan_id}"
        if con.execute(
            "SELECT 1 FROM broadcast_domain_colours WHERE category_key = ?", [key]
        ).fetchone():
            continue
        slot = con.execute(
            "SELECT coalesce(max(seq), -1) + 1 FROM broadcast_domain_colours WHERE seq >= 0"
        ).fetchone()[0]
        name = label if label else f"VLAN {vlan_id}"
        con.execute(
            "INSERT INTO broadcast_domain_colours VALUES (?, ?, ?, ?)",
            [key, name, colour_for_domain_seq(slot), slot],
        )
        added += 1
    return added


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
            "INSERT OR IGNORE INTO layer_colours VALUES (?, ?, ?, -1, ?, ?)",
            [layer, TIER_MAP.get(layer, "application"), colour,
             layer in UNRESOLVED, layer in ENCRYPTED],
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
            "INSERT INTO layer_colours VALUES (?, ?, ?, ?, ?, ?)",
            [layer, tier_for(layer, modal_index), colour_for_seq(nxt), nxt,
             layer in UNRESOLVED, layer in ENCRYPTED],
        )
        added += 1
    return added
