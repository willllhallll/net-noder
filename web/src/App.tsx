import { useCallback, useEffect, useMemo, useState } from "react";
import type { ElementDefinition } from "cytoscape";
import { api } from "./api";
import type {
  ConnectionFlows,
  Flow,
  Graph,
  GraphMeta,
  LabelMode,
  Layer,
  NodeT,
  Stats,
  Tier,
} from "./types";
import {
  buildLayerLookup,
  edgeStyle,
  edgeWidth,
  fmtBytes,
  fmtNum,
  isUnresolved,
  NEUTRAL_EDGE,
  nodeColor,
  nodeSize,
  tokenAtTier,
} from "./format";
import GraphCanvas from "./components/GraphCanvas";
import SearchBar from "./components/SearchBar";
import LayerFilter from "./components/LayerFilter";
import EndpointPanel from "./components/EndpointPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import FlowPanel from "./components/FlowPanel";
import CapNoticeModal from "./components/CapNoticeModal";

const NEIGHBOR_LIMIT = 40;

type LK = ReturnType<typeof buildLayerLookup>;

// Base graph (hosts / focus): nodes + NEUTRAL, unlabelled edges. An edge is just
// "a connection of any type" — protocol detail is revealed only on drill-down.
function graphToElements(g: Graph): ElementDefinition[] {
  const nodes: ElementDefinition[] = g.nodes.map((n) => ({
    data: {
      id: n.ip,
      ip: n.ip,
      name: n.given_name,
      label: n.ip, // GraphCanvas corrects this per labelMode
      size: nodeSize(n.total_bytes),
      color: nodeColor(n.kind),
    },
  }));
  const edges: ElementDefinition[] = g.edges.map((e) => ({
    data: {
      id: `e${e.connection_id}`,
      source: e.ip_a,
      target: e.ip_b,
      weight: edgeWidth(e.bytes),
      color: NEUTRAL_EDGE,
      // Label the connection with how many flows the pair shares (density at a glance).
      label: `${e.flow_count}`,
      opacity: 0.5,
      lineStyle: "solid",
    },
  }));
  return [...nodes, ...edges];
}

// Flow-fan view: the two endpoints + one edge PER FLOW (canonical 5-tuple). Each
// flow edge is coloured by the protocol it carries at the active tier (deepest
// recovered protocol when no tier is selected). No persistent labels — hundreds of
// edges rely on colour + the scoped legend; click an arrow for its full stack.
function flowsToElements(conn: ConnectionFlows, tier: Tier, lk: LK): ElementDefinition[] {
  const totalBytes = conn.bytes_a2b + conn.bytes_b2a;
  const nodes: ElementDefinition[] = [
    {
      data: {
        id: conn.ip_a, ip: conn.ip_a, name: conn.name_a, label: conn.ip_a,
        size: nodeSize(totalBytes), color: nodeColor(conn.kind_a),
      },
    },
    {
      data: {
        id: conn.ip_b, ip: conn.ip_b, name: conn.name_b, label: conn.ip_b,
        size: nodeSize(totalBytes), color: nodeColor(conn.kind_b),
      },
    },
  ];
  const edges: ElementDefinition[] = conn.flows.map((f) => {
    const st = edgeStyle(f.layers, tier, lk);
    return {
      data: {
        id: `f${f.flow_id}`,
        source: conn.ip_a,
        target: conn.ip_b,
        weight: edgeWidth(f.bytes_a2b + f.bytes_b2a),
        color: st.color,
        // Label each flow with the protocol it carries at the active tier, so the
        // colour code has explicit context (blank when the flow has none at the tier).
        label: st.label,
        opacity: st.opacity,
        lineStyle: st.dashed ? "dashed" : "solid",
      },
    };
  });
  return [...nodes, ...edges];
}

type GraphView =
  | { kind: "hosts" }
  | { kind: "focus"; node: NodeT; graph: Graph }
  | { kind: "flows"; conn: ConnectionFlows; from: GraphView };
type DrawerState =
  | null
  | { kind: "connection"; conn: ConnectionFlows }
  | { kind: "flow"; conn: ConnectionFlows; flow: Flow };

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [layers, setLayers] = useState<Layer[]>([]);
  const [labelMode, setLabelMode] = useState<LabelMode>("ip");
  const [activeTier, setActiveTier] = useState<Tier>("application");
  const [hostGraph, setHostGraph] = useState<Graph | null>(null);
  const [graphView, setGraphView] = useState<GraphView>({ kind: "hosts" });
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [capNotice, setCapNotice] = useState<GraphMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lk = useMemo(() => buildLayerLookup(layers), [layers]);

  const elements = useMemo(() => {
    if (graphView.kind === "flows") {
      return flowsToElements(graphView.conn, activeTier, lk);
    }
    const g = graphView.kind === "focus" ? graphView.graph : hostGraph;
    return g ? graphToElements(g) : [];
  }, [graphView, hostGraph, activeTier, lk]);

  // Tokens present at the active tier across the visible flows — the scoped legend.
  const tierLegend = useMemo(() => {
    if (graphView.kind !== "flows") return [];
    const seen = new Set<string>();
    const items: { token: string; colour: string; unresolved: boolean }[] = [];
    for (const f of graphView.conn.flows) {
      const tok = tokenAtTier(f.layers, activeTier, lk);
      if (tok && !seen.has(tok)) {
        seen.add(tok);
        items.push({
          token: tok,
          colour: lk.map.get(tok)?.colour ?? NEUTRAL_EDGE,
          unresolved: isUnresolved(tok, lk),
        });
      }
    }
    return items.sort(
      (a, b) => (lk.rank.get(a.token) ?? 0) - (lk.rank.get(b.token) ?? 0)
    );
  }, [activeTier, graphView, lk]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, g, ls] = await Promise.all([
          api.stats(),
          api.graph(),
          api.layers(),
        ]);
        if (cancelled) return;
        setStats(s);
        setLayers(ls);
        setHostGraph(g);
        setCapNotice(g.meta?.capped ? g.meta : null);
        setGraphView({ kind: "hosts" });
        setDrawer(null);
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e.message ?? e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const focusNode = useCallback(async (ip: string) => {
    try {
      const [g, node] = await Promise.all([
        api.neighbors(ip, NEIGHBOR_LIMIT),
        api.node(ip),
      ]);
      setDrawer(null);
      setGraphView({ kind: "focus", node, graph: g });
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  // Edge tap dispatches on the current view: in the flow fan, an edge IS a flow;
  // otherwise it is a connection, which drills into the flow fan + summary drawer.
  const onEdgeTap = useCallback(
    async (data: any) => {
      if (graphView.kind === "flows") {
        const flow = graphView.conn.flows.find((f) => `f${f.flow_id}` === data.id);
        if (flow) setDrawer({ kind: "flow", conn: graphView.conn, flow });
        return;
      }
      try {
        const conn = await api.connectionFlows(data.source, data.target);
        setActiveTier("application"); // default the flow view to the application tier
        setGraphView({ kind: "flows", conn, from: graphView });
        setDrawer({ kind: "connection", conn });
        setError(null);
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    },
    [graphView]
  );

  // Tapping empty canvas de-selects the current flow: fall back to the connection
  // summary (the flow drawer should not linger once its edge is unfocused).
  const onBackgroundTap = useCallback(() => {
    setDrawer((d) => (d?.kind === "flow" ? { kind: "connection", conn: d.conn } : d));
  }, []);

  const exitFocus = useCallback(() => {
    setDrawer(null);
    setGraphView({ kind: "hosts" });
  }, []);

  const exitFlows = useCallback(() => {
    setDrawer(null);
    setGraphView((gv) => (gv.kind === "flows" ? gv.from : { kind: "hosts" }));
  }, []);

  const counts = useMemo(() => {
    if (graphView.kind === "flows") {
      return { nodes: 2, edges: graphView.conn.flow_count };
    }
    const g = graphView.kind === "focus" ? graphView.graph : hostGraph;
    return { nodes: g?.nodes.length ?? 0, edges: g?.edges.length ?? 0 };
  }, [graphView, hostGraph]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">net-noder</div>
        {stats && (
          <div className="stat-strip">
            <span>{fmtNum(stats.endpoints)} endpoints</span>
            <span>{fmtNum(stats.connections)} connections</span>
            <span>{fmtNum(stats.flows)} flows</span>
            <span>{fmtNum(stats.layers)} protocols</span>
            <span>{fmtBytes(stats.total_bytes)}</span>
          </div>
        )}
        <SearchBar
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
          onBackgroundTap={onBackgroundTap}
        />

        {graphView.kind === "focus" && (
          <div className="view-banner">endpoint view · {graphView.node.ip}</div>
        )}
        {graphView.kind === "flows" && (
          <div className="view-banner">
            flow view · {graphView.conn.ip_a} ↔ {graphView.conn.ip_b} ·{" "}
            {fmtNum(graphView.conn.flow_count)} flows
          </div>
        )}

        {/* The tier filter lives in the flow view only — it colours the flow arrows. */}
        {graphView.kind === "flows" && (
          <LayerFilter
            activeTier={activeTier}
            setActiveTier={setActiveTier}
            tierLegend={tierLegend}
            nodeCount={counts.nodes}
            edgeCount={counts.edges}
          />
        )}

        {drawer?.kind === "flow" ? (
          <FlowPanel
            conn={drawer.conn}
            flow={drawer.flow}
            labelMode={labelMode}
            lk={lk}
            onBack={() => setDrawer({ kind: "connection", conn: drawer.conn })}
          />
        ) : drawer?.kind === "connection" ? (
          <ConnectionPanel
            conn={drawer.conn}
            labelMode={labelMode}
            onBack={exitFlows}
          />
        ) : graphView.kind === "focus" ? (
          <EndpointPanel data={graphView.node} onBack={exitFocus} />
        ) : null}
      </main>

      <CapNoticeModal notice={capNotice} onClose={() => setCapNotice(null)} />
    </div>
  );
}
