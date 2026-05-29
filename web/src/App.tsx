import { useCallback, useEffect, useMemo, useState } from "react";
import type { ElementDefinition } from "cytoscape";
import { api } from "./api";
import type { EdgeService, Graph, LabelMode, Metric, Stats } from "./types";
import {
  castColor,
  edgeWidth,
  fmtBytes,
  fmtNum,
  nodeColor,
  nodeSize,
} from "./format";
import GraphCanvas from "./components/GraphCanvas";
import Controls from "./components/Controls";
import DetailModal, { ModalContent } from "./components/DetailModal";

const NEIGHBOR_LIMIT = 40;

function svcLabel(s: EdgeService): string {
  return s.port == null ? s.proto : `${s.proto}/${s.port}`;
}

// Stacked, multi-line label listing the top services on an edge, with overflow.
function edgeLabel(services: EdgeService[], extra: number): string {
  const lines = services.map(svcLabel);
  if (extra > 0) lines.push(`+${extra} more`);
  return lines.join("\n");
}

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
      label: edgeLabel(e.services, e.extra),
    },
  }));
  return [...nodes, ...edges];
}

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [metric, setMetric] = useState<Metric>("bytes");
  const [limit, setLimit] = useState(100);
  const [labelMode, setLabelMode] = useState<LabelMode>("ip");
  const [elements, setElements] = useState<ElementDefinition[]>([]);
  const [modal, setModal] = useState<ModalContent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mergeGraph = useCallback((g: Graph) => {
    setElements((prev) => {
      const map = new Map(prev.map((e) => [String(e.data!.id), e]));
      for (const el of graphToElements(g)) map.set(String(el.data!.id), el);
      return Array.from(map.values());
    });
  }, []);

  // Initial + top-view loads (driven by metric/limit).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, g] = await Promise.all([api.stats(), api.top(metric, limit)]);
        if (cancelled) return;
        setStats(s);
        setElements(graphToElements(g));
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e.message ?? e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [metric, limit]);

  const onNodeTap = useCallback(async (ip: string) => {
    try {
      setModal({ type: "node", data: await api.node(ip) });
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  const onNodeExpand = useCallback(
    async (ip: string) => {
      try {
        mergeGraph(await api.neighbors(ip, metric, NEIGHBOR_LIMIT));
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    },
    [metric, mergeGraph]
  );

  const onEdgeTap = useCallback(async (source: string, target: string) => {
    try {
      setModal({
        type: "conversation",
        data: await api.conversation(source, target),
      });
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  const onPick = useCallback(
    async (ip: string) => {
      try {
        const g = await api.neighbors(ip, metric, NEIGHBOR_LIMIT);
        setElements(graphToElements(g));
        setModal({ type: "node", data: await api.node(ip) });
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    },
    [metric]
  );

  const handleExpand = useCallback(
    (ip: string) => {
      setModal(null);
      onNodeExpand(ip);
    },
    [onNodeExpand]
  );

  // Clear any focus/expansion and return to the plain top-N view.
  const resetView = useCallback(async () => {
    setModal(null);
    try {
      setElements(graphToElements(await api.top(metric, limit)));
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [metric, limit]);

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
            <span>{fmtNum(stats.conversations)} conversations</span>
            <span>{fmtNum(stats.total_pkts)} pkts</span>
            <span>{fmtBytes(stats.total_bytes)}</span>
          </div>
        )}
        <Controls
          metric={metric}
          setMetric={setMetric}
          limit={limit}
          setLimit={setLimit}
          labelMode={labelMode}
          setLabelMode={setLabelMode}
          onPick={onPick}
          onReset={resetView}
        />
      </header>

      {error && <div className="error">{error}</div>}

      <main className="canvas-wrap">
        <GraphCanvas
          elements={elements}
          labelMode={labelMode}
          onNodeTap={onNodeTap}
          onNodeExpand={onNodeExpand}
          onEdgeTap={onEdgeTap}
        />
        <div className="legend">
          <div className="legend-title">
            showing {counts.nodes} nodes · {counts.edges} edges
          </div>
          <LegendRow color={nodeColor("unicast", true)} label="local host" />
          <LegendRow color={nodeColor("unicast", false)} label="external host" />
          <LegendRow color={nodeColor("multicast", false)} label="multicast" />
          <LegendRow color={nodeColor("broadcast", false)} label="broadcast" />
          <div className="legend-hint">
            click node = details · right-click node = expand · click edge = ports
          </div>
        </div>
      </main>

      {modal && (
        <DetailModal
          content={modal}
          onClose={() => setModal(null)}
          onExpand={handleExpand}
        />
      )}
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
