"""Graph queries over the aggregated tables. Each takes a DuckDB cursor.

All results are bounded (top-N / neighbours of one node) so the API never returns
the whole graph. Vocabulary: endpoints (one IP), connections (any traffic between a
pair), conversations (traffic on one assumed service/port within a connection).
"""
ALLOWED_METRICS = {"bytes", "pkts"}
_MAX_LIMIT = 2000
_EDGE_TOP_CONVS = 3  # conversations listed inline on each edge before "+N more"

# Defensive ceiling on host-view node count. The initial use case is a single /24
# (<=256 endpoints) so this never trips there; on a much larger capture we keep the
# highest-traffic endpoints and warn, since drill-down + search still reach the rest.
MAX_GRAPH_NODES = 10000

# connection row layout shared by the helpers below
_CONN_COLS = (
    "id, ip_a, ip_b, pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a, first_seen, last_seen"
)
# Node columns + the given-name join, applied at query time so names can be edited
# without re-aggregating. `n.given_name` is NULL when no name is known for the IP.
_NODE_SELECT = (
    "e.ip, e.total_pkts, e.total_bytes, e.first_seen, e.last_seen, "
    "e.is_local, e.kind, e.hostname, n.given_name"
)
_NODE_FROM = "endpoints e LEFT JOIN names n ON n.ip = e.ip"


def _order_expr(metric: str) -> str:
    return "(bytes_a2b + bytes_b2a)" if metric == "bytes" else "(pkts_a2b + pkts_b2a)"


def _clamp(limit: int, hi: int = _MAX_LIMIT) -> int:
    return max(1, min(int(limit), hi))


def _metric(metric: str) -> str:
    return metric if metric in ALLOWED_METRICS else "bytes"


def _node_dict(r) -> dict:
    return {
        "ip": r[0], "total_pkts": r[1], "total_bytes": r[2], "first_seen": r[3],
        "last_seen": r[4], "is_local": r[5], "kind": r[6], "hostname": r[7],
        "given_name": r[8],
    }


def _fetch_nodes(cur, ips) -> list[dict]:
    ips = list(ips)
    if not ips:
        return []
    ph = ",".join("?" * len(ips))
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip IN ({ph})", ips
    ).fetchall()
    return [_node_dict(r) for r in rows]


def _edge_extras(cur, ids) -> dict:
    """Per-connection dominant cast + top-N conversations (by bytes) + total count."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))

    # Dominant cast and how many distinct conversations the pair has.
    summary = cur.execute(
        f"""
        SELECT connection_id,
          CASE WHEN bool_or(cast_type='broadcast') THEN 'broadcast'
               WHEN bool_or(cast_type='multicast') THEN 'multicast'
               ELSE 'unicast' END,
          count(*)
        FROM conversations WHERE connection_id IN ({ph})
        GROUP BY connection_id
        """,
        ids,
    ).fetchall()
    out = {cid: {"cast": cast, "total": total, "conversations": []}
           for cid, cast, total in summary}

    # Top-N conversations per connection, ranked by bytes.
    top = cur.execute(
        f"""
        SELECT connection_id, l4_proto, server_port, cast_type
        FROM (
          SELECT connection_id, l4_proto, server_port, cast_type,
                 row_number() OVER (PARTITION BY connection_id
                                    ORDER BY (bytes_a2b + bytes_b2a) DESC) AS rk
          FROM conversations WHERE connection_id IN ({ph})
        ) WHERE rk <= {_EDGE_TOP_CONVS}
        ORDER BY connection_id, rk
        """,
        ids,
    ).fetchall()
    for cid, proto, port, cast in top:
        if cid in out:
            out[cid]["conversations"].append(
                {"proto": proto, "port": port, "cast": cast}
            )
    return out


def _assemble(cur, conn_rows) -> dict:
    ids = [r[0] for r in conn_rows]
    extras = _edge_extras(cur, ids)
    ips: set = set()
    edges = []
    for r in conn_rows:
        cid, ip_a, ip_b = r[0], r[1], r[2]
        ips.add(ip_a)
        ips.add(ip_b)
        ex = extras.get(cid, {"cast": "unicast", "total": 0, "conversations": []})
        convs = ex["conversations"]
        edges.append({
            "id": cid, "source": ip_a, "target": ip_b,
            "pkts": r[3] + r[5], "bytes": r[4] + r[6],
            "cast": ex["cast"], "conversations": convs,
            "extra": max(0, ex["total"] - len(convs)),
        })
    return {"nodes": _fetch_nodes(cur, ips), "edges": edges}


def stats(cur) -> dict:
    row = cur.execute(
        """
        SELECT
          (SELECT count(*) FROM endpoints),
          (SELECT count(*) FROM connections),
          (SELECT count(*) FROM conversations),
          (SELECT coalesce(sum(pkts_a2b + pkts_b2a), 0) FROM connections),
          (SELECT coalesce(sum(bytes_a2b + bytes_b2a), 0) FROM connections),
          (SELECT min(first_seen) FROM endpoints),
          (SELECT max(last_seen) FROM endpoints)
        """
    ).fetchone()
    return {
        "endpoints": row[0], "connections": row[1], "conversations": row[2],
        "total_pkts": row[3], "total_bytes": row[4],
        "first_seen": row[5], "last_seen": row[6],
    }


def full_graph(cur, cap: int = MAX_GRAPH_NODES) -> dict:
    """The whole host graph (all endpoints + connections), bounded by a node cap.

    Under the cap every connection is returned. Over it, only the top `cap`
    endpoints by bytes are kept (and the connections among them); `meta.capped`
    flags this so the UI can explain that drill-down/search still reach the rest.
    """
    cap = _clamp(cap, hi=MAX_GRAPH_NODES)
    total = cur.execute("SELECT count(*) FROM endpoints").fetchone()[0]

    if total <= cap:
        rows = cur.execute(f"SELECT {_CONN_COLS} FROM connections").fetchall()
    else:
        top_ips = [
            r[0]
            for r in cur.execute(
                "SELECT ip FROM endpoints ORDER BY total_bytes DESC LIMIT ?", [cap]
            ).fetchall()
        ]
        ph = ",".join("?" * len(top_ips))
        rows = cur.execute(
            f"SELECT {_CONN_COLS} FROM connections "
            f"WHERE ip_a IN ({ph}) AND ip_b IN ({ph})",
            top_ips + top_ips,
        ).fetchall()

    g = _assemble(cur, rows)
    g["meta"] = {
        "capped": total > cap,
        "cap": cap,
        "shown_endpoints": len(g["nodes"]),
        "total_endpoints": total,
    }
    return g


def neighbors(cur, ip: str, metric: str = "bytes", limit: int = 50) -> dict:
    metric = _metric(metric)
    rows = cur.execute(
        f"SELECT {_CONN_COLS} FROM connections WHERE ip_a=? OR ip_b=? "
        f"ORDER BY {_order_expr(metric)} DESC LIMIT ?",
        [ip, ip, _clamp(limit)],
    ).fetchall()
    g = _assemble(cur, rows)
    if not any(n["ip"] == ip for n in g["nodes"]):
        g["nodes"].extend(_fetch_nodes(cur, [ip]))
    return g


def node_detail(cur, ip: str):
    r = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip=?", [ip]
    ).fetchone()
    if not r:
        return None
    d = _node_dict(r)
    d["degree"] = cur.execute(
        "SELECT count(*) FROM connections WHERE ip_a=? OR ip_b=?", [ip, ip]
    ).fetchone()[0]
    return d


def connection(cur, a: str, b: str):
    ip_a, ip_b = sorted([a, b])  # matches DuckDB LEAST/GREATEST canonical order
    c = cur.execute(
        f"SELECT {_CONN_COLS}, na.given_name, nb.given_name "
        "FROM connections c "
        "LEFT JOIN names na ON na.ip = c.ip_a "
        "LEFT JOIN names nb ON nb.ip = c.ip_b "
        "WHERE c.ip_a=? AND c.ip_b=?",
        [ip_a, ip_b],
    ).fetchone()
    if not c:
        return None
    # service: the port_services description for this conversation's server_port,
    # joined at query time (like names) so the registry can be refreshed without
    # re-aggregating. server_port is per-direction, so the service matches the arrow.
    convs = cur.execute(
        "SELECT cv.l4_proto, cv.server_port, cv.cast_type, "
        "cv.pkts_a2b, cv.bytes_a2b, cv.pkts_b2a, cv.bytes_b2a, "
        "coalesce(cv.reply_port_count, 0), cv.server_is_a, cv.first_seen, cv.last_seen, "
        "nullif(ps.description, '') "
        "FROM conversations cv "
        "LEFT JOIN port_services ps "
        "  ON ps.port = cv.server_port AND ps.transport = lower(cv.l4_proto) "
        "WHERE cv.connection_id=? "
        "ORDER BY (cv.bytes_a2b + cv.bytes_b2a) DESC",
        [c[0]],
    ).fetchall()
    return {
        "id": c[0], "ip_a": c[1], "ip_b": c[2],
        "name_a": c[9], "name_b": c[10],
        "pkts_a2b": c[3], "bytes_a2b": c[4], "pkts_b2a": c[5], "bytes_b2a": c[6],
        "first_seen": c[7], "last_seen": c[8],
        "conversations": [
            {"l4_proto": s[0], "server_port": s[1], "cast_type": s[2],
             "pkts_a2b": s[3], "bytes_a2b": s[4], "pkts_b2a": s[5], "bytes_b2a": s[6],
             "reply_port_count": s[7], "server_is_a": s[8],
             "first_seen": s[9], "last_seen": s[10], "service": s[11]}
            for s in convs
        ],
    }


def reply_ports(cur, a: str, b: str, proto: str, server_port: int,
                cast: str, server_is_a: bool, limit: int = 50):
    """Top reply ports for one conversation of a connection. server_is_a selects
    which side owns server_port, disambiguating two rows that share a server port."""
    ip_a, ip_b = sorted([a, b])
    conn = cur.execute(
        "SELECT id FROM connections WHERE ip_a=? AND ip_b=?", [ip_a, ip_b]
    ).fetchone()
    if not conn:
        return None
    cid = conn[0]
    total = cur.execute(
        "SELECT coalesce(reply_port_count, 0) FROM conversations "
        "WHERE connection_id=? AND l4_proto=? AND server_port=? AND cast_type=? "
        "AND server_is_a=?",
        [cid, proto, server_port, cast, server_is_a],
    ).fetchone()
    if total is None:
        return None
    rows = cur.execute(
        "SELECT reply_port, pkts, bytes FROM conversation_ports "
        "WHERE connection_id=? AND l4_proto=? AND server_port=? AND cast_type=? "
        "AND server_is_a=? "
        "ORDER BY bytes DESC LIMIT ?",
        [cid, proto, server_port, cast, server_is_a, _clamp(limit, 500)],
    ).fetchall()
    ports = [{"port": r[0], "pkts": r[1], "bytes": r[2]} for r in rows]
    return {
        "l4_proto": proto, "server_port": server_port, "cast_type": cast,
        "total": total[0], "truncated": total[0] > len(ports), "ports": ports,
    }


def search(cur, q: str, limit: int = 20) -> list[dict]:
    # Match by IP or by name. IPs are matched as a prefix ("192.168" -> that subnet);
    # hostnames and user-given names as a case-insensitive substring, so "laptop"
    # finds "Alice Laptop". Endpoints without a given name still match on their IP.
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    ip_like = esc + "%"
    name_like = f"%{esc}%"
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} "
        "WHERE e.ip LIKE ? ESCAPE '\\' "
        "OR e.hostname ILIKE ? ESCAPE '\\' "
        "OR n.given_name ILIKE ? ESCAPE '\\' "
        "ORDER BY e.total_bytes DESC LIMIT ?",
        [ip_like, name_like, name_like, _clamp(limit, 100)],
    ).fetchall()
    return [_node_dict(r) for r in rows]
