import { useCallback, useEffect, useMemo, useState } from "react";
import type { ElementDefinition } from "cytoscape";
import { api } from "./api";
import type {
  Category,
  ConnectionStack,
  ConnectionStacks,
  Graph,
  GraphMeta,
  LabelOpts,
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
  shortVersion,
  tokenAtTier,
  versionOwnerToken,
} from "./format";
import GraphCanvas from "./components/GraphCanvas";
import SearchBar from "./components/SearchBar";
import LayerFilter from "./components/LayerFilter";
import VlanFilter from "./components/VlanFilter";
import EndpointPanel from "./components/EndpointPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import StackPanel from "./components/StackPanel";
import CapNoticeModal from "./components/CapNoticeModal";

type LK = ReturnType<typeof buildLayerLookup>;

// Base graph (hosts / focus): nodes + NEUTRAL, unlabelled edges. An edge is just
// "a connection of any type" — protocol detail is revealed only on drill-down. Nodes
// are coloured by VLAN/subnet category. When `activeCats` is supplied (hosts view),
// nodes whose category is toggled off are dropped, along with any edge touching them;
// when it is undefined (focus view) everything is shown.
function graphToElements(
  g: Graph,
  activeCats?: Set<string>,
): ElementDefinition[] {
  const visible = g.nodes.filter(
    (n) => !activeCats || activeCats.has(n.category),
  );
  const visibleIps = new Set(visible.map((n) => n.ip));
  const nodes: ElementDefinition[] = visible.map((n) => ({
    data: {
      id: n.ip,
      ip: n.ip,
      name: n.given_name,
      whois: n.whois_name,
      label: n.ip, // GraphCanvas corrects this per the label toggles
      size: nodeSize(n.total_bytes),
      color: nodeColor(n),
    },
  }));
  const edges: ElementDefinition[] = g.edges
    .filter((e) => visibleIps.has(e.ip_a) && visibleIps.has(e.ip_b))
    .map((e) => ({
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

// Node/edge counts for a base graph under the category filter — the same visibility rule
// graphToElements applies (edges survive only when both endpoints do), used for the
// filter-window header in both the hosts and focus views.
function countVisible(g: Graph, activeCats: Set<string>): { nodes: number; edges: number } {
  const visible = g.nodes.filter((n) => activeCats.has(n.category));
  const visibleIps = new Set(visible.map((n) => n.ip));
  const edges = g.edges.filter(
    (e) => visibleIps.has(e.ip_a) && visibleIps.has(e.ip_b),
  );
  return { nodes: visible.length, edges: edges.length };
}

// Flow-fan view: the two endpoints + one edge PER STACK (flows sharing an identical
// full dissected stack are collapsed). Each stack edge is coloured by the protocol it
// carries at the active tier, and labelled with that protocol plus the flow count that
// collapsed into it — so port churn becomes a handful of arrows, not thousands.
function stacksToElements(
  conn: ConnectionStacks,
  tier: Tier,
  lk: LK,
): ElementDefinition[] {
  const totalBytes = conn.bytes_a2b + conn.bytes_b2a;
  const nodes: ElementDefinition[] = [
    {
      data: {
        id: conn.ip_a,
        ip: conn.ip_a,
        name: conn.name_a,
        whois: conn.whois_name_a,
        label: conn.ip_a,
        size: nodeSize(totalBytes),
        color: nodeColor({ colour: conn.colour_a, kind: conn.kind_a }),
      },
    },
    {
      data: {
        id: conn.ip_b,
        ip: conn.ip_b,
        name: conn.name_b,
        whois: conn.whois_name_b,
        label: conn.ip_b,
        size: nodeSize(totalBytes),
        color: nodeColor({ colour: conn.colour_b, kind: conn.kind_b }),
      },
    },
  ];
  const edges: ElementDefinition[] = conn.stacks.map((s, i) => {
    const st = edgeStyle(s.layers, tier, lk);
    // When the visible token OWNS the flow's protocol_version, fold it into the label so it
    // reads e.g. "tls·1.3 ×210" or "http·1.1 ×42" — but never on a token the version does
    // not belong to (e.g. a TLS version under the tcp chip), where it would be misleading.
    const ver =
      st.token && st.token === versionOwnerToken(s.protocol_version)
        ? shortVersion(s.protocol_version)
        : null;
    const proto = st.label ? `${st.label}${ver ? `·${ver}` : ""}` : "";
    return {
      data: {
        id: `s${i}`,
        source: conn.ip_a,
        target: conn.ip_b,
        weight: edgeWidth(s.bytes_a2b + s.bytes_b2a),
        color: st.color,
        // Mid-edge label: the protocol at the active tier (blank when the tier has none)
        // followed by the collapsed-flow count on the same line, e.g. "dns ×10,826".
        label: proto
          ? `${proto} ×${fmtNum(s.flow_count)}`
          : `×${fmtNum(s.flow_count)}`,
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
  | { kind: "flows"; conn: ConnectionStacks; from: GraphView };
type DrawerState =
  | null
  | { kind: "connection"; conn: ConnectionStacks }
  | { kind: "stack"; conn: ConnectionStacks; stack: ConnectionStack };

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [layers, setLayers] = useState<Layer[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [activeCats, setActiveCats] = useState<Set<string>>(new Set());
  const [labelOpts, setLabelOpts] = useState<LabelOpts>({ given: true, whois: false });
  const [activeTier, setActiveTier] = useState<Tier>("application");
  const [hostGraph, setHostGraph] = useState<Graph | null>(null);
  const [graphView, setGraphView] = useState<GraphView>({ kind: "hosts" });
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [capNotice, setCapNotice] = useState<GraphMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  const lk = useMemo(() => buildLayerLookup(layers), [layers]);

  const elements = useMemo(() => {
    if (graphView.kind === "flows") {
      return stacksToElements(graphView.conn, activeTier, lk);
    }
    if (graphView.kind === "focus") {
      // Focus view carries the same category filter as the hosts view.
      return graphToElements(graphView.graph, activeCats);
    }
    return hostGraph ? graphToElements(hostGraph, activeCats) : [];
  }, [graphView, hostGraph, activeTier, lk, activeCats]);

  // Tokens present at the active tier across the visible flows — the scoped legend.
  const tierLegend = useMemo(() => {
    if (graphView.kind !== "flows") return [];
    const seen = new Set<string>();
    const items: { token: string; colour: string; unresolved: boolean }[] = [];
    for (const s of graphView.conn.stacks) {
      const tok = tokenAtTier(s.layers, activeTier, lk);
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
      (a, b) => (lk.rank.get(a.token) ?? 0) - (lk.rank.get(b.token) ?? 0),
    );
  }, [activeTier, graphView, lk]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, g, ls, cats] = await Promise.all([
          api.stats(),
          api.graph(),
          api.layers(),
          api.categories(),
        ]);
        if (cancelled) return;
        setStats(s);
        setLayers(ls);
        setCategories(cats);
        setActiveCats(new Set(cats.map((c) => c.category_key)));
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
        api.neighbors(ip), // no explicit limit -> server's host-view cap governs
        api.node(ip),
      ]);
      setDrawer(null);
      setGraphView({ kind: "focus", node, graph: g });
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  // Edge tap dispatches on the current view: in the flow fan, an edge IS a stack;
  // otherwise it is a connection, which drills into the flow fan + summary drawer.
  const onEdgeTap = useCallback(
    async (data: any) => {
      if (graphView.kind === "flows") {
        const i = Number(String(data.id).slice(1)); // "s<index>"
        const stack = graphView.conn.stacks[i];
        if (stack) setDrawer({ kind: "stack", conn: graphView.conn, stack });
        return;
      }
      try {
        const conn = await api.connectionStacks(data.source, data.target);
        setActiveTier("application"); // default the flow view to the application tier
        setGraphView({ kind: "flows", conn, from: graphView });
        setDrawer({ kind: "connection", conn });
        setError(null);
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    },
    [graphView],
  );

  // Tapping empty canvas de-selects the current stack: fall back to the connection
  // summary (the stack drawer should not linger once its edge is unfocused).
  const onBackgroundTap = useCallback(() => {
    setDrawer((d) =>
      d?.kind === "stack" ? { kind: "connection", conn: d.conn } : d,
    );
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
      return { nodes: 2, edges: graphView.conn.stacks.length };
    }
    // Hosts & focus views count only what survives the category filter.
    if (graphView.kind === "focus") return countVisible(graphView.graph, activeCats);
    if (!hostGraph) return { nodes: 0, edges: 0 };
    return countVisible(hostGraph, activeCats);
  }, [graphView, hostGraph, activeCats]);

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
          labelOpts={labelOpts}
          setLabelOpts={setLabelOpts}
          onPick={focusNode}
        />
      </header>

      {error && <div className="error">{error}</div>}

      <main className="canvas-wrap">
        <GraphCanvas
          elements={elements}
          labelOpts={labelOpts}
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

        {/* The VLAN/category filter is shown in the hosts AND focus views — the focus
            view inherits the same toggles so filtering carries through on drill-in.
            (Shares the bottom-left slot with LayerFilter, shown only in the flow view.) */}
        {(graphView.kind === "hosts" || graphView.kind === "focus") &&
          categories.length > 0 && (
          <VlanFilter
            categories={categories}
            active={activeCats}
            setActive={setActiveCats}
            nodeCount={counts.nodes}
            edgeCount={counts.edges}
          />
        )}

        {drawer?.kind === "stack" ? (
          <StackPanel
            conn={drawer.conn}
            stack={drawer.stack}
            labelOpts={labelOpts}
            activeTier={activeTier}
            lk={lk}
            onBack={() => setDrawer({ kind: "connection", conn: drawer.conn })}
          />
        ) : drawer?.kind === "connection" ? (
          <ConnectionPanel
            conn={drawer.conn}
            labelOpts={labelOpts}
            activeTier={activeTier}
            lk={lk}
            onBack={exitFlows}
          />
        ) : graphView.kind === "focus" ? (
          <EndpointPanel data={graphView.node} labelOpts={labelOpts} onBack={exitFocus} />
        ) : null}
      </main>

      <CapNoticeModal notice={capNotice} onClose={() => setCapNotice(null)} />
    </div>
  );
}
