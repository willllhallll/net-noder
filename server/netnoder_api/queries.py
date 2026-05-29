"""Graph queries over the aggregated tables. Each takes a DuckDB cursor.

All results are bounded (top-N / neighbours of one node) so the API never returns
the whole graph.
"""
ALLOWED_METRICS = {"bytes", "pkts"}
_MAX_LIMIT = 2000
_EDGE_TOP_SERVICES = 3  # services listed inline on each edge before "+N more"

# conv row layout shared by the helpers below
_CONV_COLS = (
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
    """Per-conversation dominant cast + top-N services (by bytes) + total count."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))

    # Dominant cast and how many distinct services the pair has.
    summary = cur.execute(
        f"""
        SELECT conversation_id,
          CASE WHEN bool_or(cast_type='broadcast') THEN 'broadcast'
               WHEN bool_or(cast_type='multicast') THEN 'multicast'
               ELSE 'unicast' END,
          count(*)
        FROM services WHERE conversation_id IN ({ph})
        GROUP BY conversation_id
        """,
        ids,
    ).fetchall()
    out = {cid: {"cast": cast, "total": total, "services": []}
           for cid, cast, total in summary}

    # Top-N services per conversation, ranked by bytes.
    top = cur.execute(
        f"""
        SELECT conversation_id, l4_proto, server_port, cast_type
        FROM (
          SELECT conversation_id, l4_proto, server_port, cast_type,
                 row_number() OVER (PARTITION BY conversation_id
                                    ORDER BY bytes DESC) AS rk
          FROM services WHERE conversation_id IN ({ph})
        ) WHERE rk <= {_EDGE_TOP_SERVICES}
        ORDER BY conversation_id, rk
        """,
        ids,
    ).fetchall()
    for cid, proto, port, cast in top:
        if cid in out:
            out[cid]["services"].append(
                {"proto": proto, "port": port, "cast": cast}
            )
    return out


def _assemble(cur, conv_rows) -> dict:
    ids = [r[0] for r in conv_rows]
    extras = _edge_extras(cur, ids)
    ips: set = set()
    edges = []
    for r in conv_rows:
        cid, ip_a, ip_b = r[0], r[1], r[2]
        ips.add(ip_a)
        ips.add(ip_b)
        ex = extras.get(cid, {"cast": "unicast", "total": 0, "services": []})
        services = ex["services"]
        edges.append({
            "id": cid, "source": ip_a, "target": ip_b,
            "pkts": r[3] + r[5], "bytes": r[4] + r[6],
            "cast": ex["cast"], "services": services,
            "extra": max(0, ex["total"] - len(services)),
        })
    return {"nodes": _fetch_nodes(cur, ips), "edges": edges}


def stats(cur) -> dict:
    row = cur.execute(
        """
        SELECT
          (SELECT count(*) FROM endpoints),
          (SELECT count(*) FROM conversations),
          (SELECT count(*) FROM services),
          (SELECT coalesce(sum(pkts_a2b + pkts_b2a), 0) FROM conversations),
          (SELECT coalesce(sum(bytes_a2b + bytes_b2a), 0) FROM conversations),
          (SELECT min(first_seen) FROM endpoints),
          (SELECT max(last_seen) FROM endpoints)
        """
    ).fetchone()
    return {
        "endpoints": row[0], "conversations": row[1], "services": row[2],
        "total_pkts": row[3], "total_bytes": row[4],
        "first_seen": row[5], "last_seen": row[6],
    }


def top_graph(cur, metric: str = "bytes", limit: int = 100) -> dict:
    metric = _metric(metric)
    rows = cur.execute(
        f"SELECT {_CONV_COLS} FROM conversations "
        f"ORDER BY {_order_expr(metric)} DESC LIMIT ?",
        [_clamp(limit)],
    ).fetchall()
    return _assemble(cur, rows)


def neighbors(cur, ip: str, metric: str = "bytes", limit: int = 50) -> dict:
    metric = _metric(metric)
    rows = cur.execute(
        f"SELECT {_CONV_COLS} FROM conversations WHERE ip_a=? OR ip_b=? "
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
        "SELECT count(*) FROM conversations WHERE ip_a=? OR ip_b=?", [ip, ip]
    ).fetchone()[0]
    return d


def conversation(cur, a: str, b: str):
    ip_a, ip_b = sorted([a, b])  # matches DuckDB LEAST/GREATEST canonical order
    c = cur.execute(
        f"SELECT {_CONV_COLS}, na.given_name, nb.given_name "
        "FROM conversations c "
        "LEFT JOIN names na ON na.ip = c.ip_a "
        "LEFT JOIN names nb ON nb.ip = c.ip_b "
        "WHERE c.ip_a=? AND c.ip_b=?",
        [ip_a, ip_b],
    ).fetchone()
    if not c:
        return None
    svc = cur.execute(
        "SELECT l4_proto, server_port, cast_type, pkts, bytes, "
        "coalesce(client_port_count, 0) "
        "FROM services WHERE conversation_id=? ORDER BY bytes DESC",
        [c[0]],
    ).fetchall()
    return {
        "id": c[0], "ip_a": c[1], "ip_b": c[2],
        "name_a": c[9], "name_b": c[10],
        "pkts_a2b": c[3], "bytes_a2b": c[4], "pkts_b2a": c[5], "bytes_b2a": c[6],
        "first_seen": c[7], "last_seen": c[8],
        "services": [
            {"l4_proto": s[0], "server_port": s[1], "cast_type": s[2],
             "pkts": s[3], "bytes": s[4], "client_port_count": s[5]}
            for s in svc
        ],
    }


def ephemeral_ports(cur, a: str, b: str, proto: str, server_port: int,
                    cast: str, limit: int = 50):
    """Top ephemeral/client ports for one service line of a conversation."""
    ip_a, ip_b = sorted([a, b])
    conv = cur.execute(
        "SELECT id FROM conversations WHERE ip_a=? AND ip_b=?", [ip_a, ip_b]
    ).fetchone()
    if not conv:
        return None
    cid = conv[0]
    total = cur.execute(
        "SELECT coalesce(client_port_count, 0) FROM services "
        "WHERE conversation_id=? AND l4_proto=? AND server_port=? AND cast_type=?",
        [cid, proto, server_port, cast],
    ).fetchone()
    if total is None:
        return None
    rows = cur.execute(
        "SELECT client_port, pkts, bytes FROM service_ports "
        "WHERE conversation_id=? AND l4_proto=? AND server_port=? AND cast_type=? "
        "ORDER BY bytes DESC LIMIT ?",
        [cid, proto, server_port, cast, _clamp(limit, 500)],
    ).fetchall()
    ports = [{"port": r[0], "pkts": r[1], "bytes": r[2]} for r in rows]
    return {
        "l4_proto": proto, "server_port": server_port, "cast_type": cast,
        "total": total[0], "truncated": total[0] > len(ports), "ports": ports,
    }


def search(cur, q: str, limit: int = 20) -> list[dict]:
    like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} "
        "WHERE e.ip LIKE ? ESCAPE '\\' OR e.hostname LIKE ? ESCAPE '\\' "
        "OR n.given_name LIKE ? ESCAPE '\\' "
        "ORDER BY e.total_bytes DESC LIMIT ?",
        [like, like, like, _clamp(limit, 100)],
    ).fetchall()
    return [_node_dict(r) for r in rows]
