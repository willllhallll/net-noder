import { useCallback, useEffect, useMemo, useState } from "react";
import type { ElementDefinition } from "cytoscape";
import { api } from "./api";
import type {
  ConnectionDetail,
  Conversation,
  EdgeConversation,
  EphemeralPorts,
  Graph,
  GraphMeta,
  LabelMode,
  NodeDetail,
  Stats,
} from "./types";
import {
  castColor,
  convDirection,
  convLabel,
  edgeWidth,
  fmtBytes,
  fmtNum,
  nodeColor,
  nodeSize,
} from "./format";
import GraphCanvas from "./components/GraphCanvas";
import Controls from "./components/Controls";
import EndpointPanel from "./components/EndpointPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import CapNoticeModal from "./components/CapNoticeModal";

const NEIGHBOR_LIMIT = 40;

function convChip(s: EdgeConversation): string {
  return s.port == null ? s.proto : `${s.proto}/${s.port}`;
}

// Stacked, multi-line label listing the top conversations on a connection edge.
function edgeLabel(convs: EdgeConversation[], extra: number): string {
  const lines = convs.map(convChip);
  if (extra > 0) lines.push(`+${extra} more`);
  return lines.join("\n");
}

// Host view: nodes = endpoints, edges = connections (undirected, arrow "none").
function graphToElements(g: Graph): ElementDefinition[] {
  const nodes: ElementDefinition[] = g.nodes.map((n) => ({
    data: {
      id: n.ip,
      ip: n.ip,
      name: n.given_name,
      label: n.ip, // corrected per labelMode by GraphCanvas
      size: nodeSize(n.total_bytes),
      color: nodeColor(n.kind, n.is_local),
    },
  }));
  const edges: ElementDefinition[] = g.edges.map((e) => ({
    data: {
      id: `e${e.id}`,
      source: e.source,
      target: e.target,
      weight: edgeWidth(e.bytes),
      color: castColor(e.cast),
      label: edgeLabel(e.conversations, e.extra),
      arrow: "none",
    },
  }));
  return [...nodes, ...edges];
}

// Drill-down view: the two endpoints + one directed edge per conversation. Node
// data is reused from the host graph (both endpoints are present, since the
// connection edge existed there) so colour/size/label stay consistent.
function connectionToElements(
  conn: ConnectionDetail,
  hostElements: ElementDefinition[]
): ElementDefinition[] {
  const nodeById = new Map(
    hostElements
      .filter((e) => !(e.data as any).source)
      .map((e) => [String(e.data!.id), e])
  );
  const mkNode = (ip: string, name: string | null): ElementDefinition =>
    nodeById.get(ip) ?? {
      data: {
        id: ip,
        ip,
        name,
        label: ip,
        size: nodeSize(0),
        color: nodeColor("unicast", false),
      },
    };

  const nodes = [mkNode(conn.ip_a, conn.name_a), mkNode(conn.ip_b, conn.name_b)];
  const edges: ElementDefinition[] = conn.conversations.map((c, i) => {
    const dir = convDirection(conn, c);
    const directed = c.server_is_a != null;
    return {
      data: {
        id: `c${i}`,
        // client -> server when directed; otherwise a -> b with no arrowhead.
        source: directed ? (dir.client as string) : conn.ip_a,
        target: directed ? (dir.server as string) : conn.ip_b,
        weight: edgeWidth(c.bytes_a2b + c.bytes_b2a),
        color: castColor(c.cast_type),
        label: convLabel(c),
        arrow: directed ? "triangle" : "none",
        convIdx: i,
      },
    };
  });
  return [...nodes, ...edges];
}

// The endpoint the graph is currently filtered to: its detail + the subgraph of
// its connections (reused when stepping back from a connection drill-down).
type Focus = { node: NodeDetail; elements: ElementDefinition[] };
type View =
  | { kind: "hosts" }
  | { kind: "focus"; focus: Focus }
  | { kind: "connection"; conn: ConnectionDetail; origin: Focus | null };
type ConvPorts = EphemeralPorts | "loading" | null;
type SelectedConv = { conv: Conversation; ports: ConvPorts } | null;

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [labelMode, setLabelMode] = useState<LabelMode>("ip");
  const [hostElements, setHostElements] = useState<ElementDefinition[]>([]);
  const [view, setView] = useState<View>({ kind: "hosts" });
  const [selectedConv, setSelectedConv] = useState<SelectedConv>(null);
  const [capNotice, setCapNotice] = useState<GraphMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const elements = useMemo(() => {
    if (view.kind === "connection")
      return connectionToElements(view.conn, view.origin?.elements ?? hostElements);
    if (view.kind === "focus") return view.focus.elements;
    return hostElements;
  }, [view, hostElements]);

  // Initial host-view load: the full graph (bytes-ranked, capped) + stats.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, g] = await Promise.all([api.stats(), api.graph()]);
        if (cancelled) return;
        setStats(s);
        setHostElements(graphToElements(g));
        setCapNotice(g.meta?.capped ? g.meta : null);
        setView({ kind: "hosts" });
        setSelectedConv(null);
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e.message ?? e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Filter the graph to one endpoint's connections and open its detail drawer.
  // Shared by node taps and the search picker, and called fresh each time so a
  // new endpoint fully replaces any existing focus (no drawer stacking).
  const focusNode = useCallback(async (ip: string) => {
    try {
      const [g, node] = await Promise.all([
        api.neighbors(ip, NEIGHBOR_LIMIT),
        api.node(ip),
      ]);
      setSelectedConv(null);
      setView({ kind: "focus", focus: { node, elements: graphToElements(g) } });
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  // Open the side drawer for one conversation, lazily loading its ephemeral ports.
  const openConversation = useCallback(
    async (conn: ConnectionDetail, conv: Conversation) => {
      if (conv.server_port == null || conv.client_port_count === 0) {
        setSelectedConv({ conv, ports: null });
        return;
      }
      setSelectedConv({ conv, ports: "loading" });
      try {
        const ports = await api.ephemeralPorts(
          conn.ip_a,
          conn.ip_b,
          conv.l4_proto,
          conv.server_port,
          conv.cast_type
        );
        setSelectedConv((cur) =>
          cur && cur.conv === conv ? { conv, ports } : cur
        );
      } catch {
        setSelectedConv((cur) =>
          cur && cur.conv === conv ? { conv, ports: null } : cur
        );
      }
    },
    []
  );

  // Edge taps mean different things per view: a conversation (drill-down view)
  // opens its detail in the drawer; a connection (host/focus view) drills down,
  // remembering the focused endpoint so we can step back to it.
  const onEdgeTap = useCallback(
    async (data: any) => {
      if (view.kind === "connection") {
        const conv = view.conn.conversations[data.convIdx];
        if (conv) openConversation(view.conn, conv);
        return;
      }
      const origin = view.kind === "focus" ? view.focus : null;
      try {
        const conn = await api.connection(data.source, data.target);
        setSelectedConv(null);
        setView({ kind: "connection", conn, origin });
        setError(null);
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    },
    [view, openConversation]
  );

  // Connection drawer ← button: step back to where the drill-down was entered.
  const backToOrigin = useCallback(() => {
    setSelectedConv(null);
    setView((v) =>
      v.kind === "connection" && v.origin
        ? { kind: "focus", focus: v.origin }
        : { kind: "hosts" }
    );
  }, []);

  // Focus drawer ✕ button: leave the filtered view for the full host graph.
  const exitFocus = useCallback(() => {
    setSelectedConv(null);
    setView({ kind: "hosts" });
  }, []);

  const counts = useMemo(() => {
    const nodes = elements.filter((e) => !(e.data as any).source).length;
    const edges = elements.length - nodes;
    return { nodes, edges };
  }, [elements]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">net-noder</div>
        {stats && (
          <div className="stat-strip">
            <span>{fmtNum(stats.endpoints)} endpoints</span>
            <span>{fmtNum(stats.connections)} connections</span>
            <span>{fmtNum(stats.conversations)} conversations</span>
            <span>{fmtBytes(stats.total_bytes)}</span>
          </div>
        )}
        <Controls
          labelMode={labelMode}
          setLabelMode={setLabelMode}
          onPick={focusNode}
        />
      </header>

      {error && <div className="error">{error}</div>}

      <main className="canvas-wrap">
        <GraphCanvas
          elements={elements}
          labelMode={labelMode}
          onNodeTap={focusNode}
          onEdgeTap={onEdgeTap}
        />

        {view.kind !== "hosts" && (
          <div className="view-banner">
            {view.kind === "connection" ? "connection view" : "endpoint view"}
          </div>
        )}

        <div className="legend">
          <div className="legend-title">
            showing {counts.nodes} nodes · {counts.edges}{" "}
            {view.kind === "connection" ? "conversations" : "connections"}
          </div>
          <LegendRow color={nodeColor("unicast", true)} label="local host" />
          <LegendRow color={nodeColor("unicast", false)} label="external host" />
          <LegendRow color={nodeColor("multicast", false)} label="multicast" />
          <LegendRow color={nodeColor("broadcast", false)} label="broadcast" />
          <div className="legend-hint">
            {view.kind === "connection"
              ? "click conversation = ports · arrow points client → server"
              : "click node = focus · click connection = conversations"}
          </div>
        </div>

        {view.kind === "focus" && (
          <EndpointPanel data={view.focus.node} onBack={exitFocus} />
        )}

        {view.kind === "connection" && (
          <ConnectionPanel
            conn={view.conn}
            conv={selectedConv?.conv ?? null}
            ports={selectedConv?.ports ?? null}
            labelMode={labelMode}
            onBack={backToOrigin}
          />
        )}
      </main>

      <CapNoticeModal notice={capNotice} onClose={() => setCapNotice(null)} />
    </div>
  );
}

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="legend-row">
      <span className="dot" style={{ background: color }} />
      {label}
    </div>
  );
}
