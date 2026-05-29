import { useEffect, useRef } from "react";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { LabelMode } from "../types";

cytoscape.use(fcose);

const STYLE: any[] = [
  {
    selector: "node",
    style: {
      "background-color": "data(color)",
      width: "data(size)",
      height: "data(size)",
      label: "data(label)",
      "font-size": 7,
      color: "#cbd5e1",
      "text-valign": "center",
      "text-halign": "center",
      "text-outline-color": "#0b0f17",
      "text-outline-width": 1.4,
      "border-width": 1,
      "border-color": "#0b0f17",
    },
  },
  {
    selector: "node:selected",
    style: { "border-width": 3, "border-color": "#f1fa8c" },
  },
  {
    selector: "edge",
    style: {
      width: "data(weight)",
      "line-color": "data(color)",
      "curve-style": "straight",
      opacity: 0.5,
      label: "data(label)",
      "font-size": 6,
      "line-height": 1.15,
      color: "#cbd5e1",
      "text-wrap": "wrap",
      "text-rotation": "none",
      "text-events": "yes",
      "text-background-color": "#0b0f17",
      "text-background-opacity": 0.82,
      "text-background-padding": 2,
      "text-background-shape": "roundrectangle",
      "text-border-color": "data(color)",
      "text-border-opacity": 0.7,
      "text-border-width": 1,
      // Hide the service chips when zoomed out so dense graphs stay readable;
      // they fade back in as you zoom into a region.
      "min-zoomed-font-size": 6,
    },
  },
  {
    selector: "edge:selected",
    style: {
      opacity: 1,
      "line-color": "#f1fa8c",
      "text-background-opacity": 0.95,
      "z-index": 10,
    },
  },
];

const LAYOUT: any = {
  name: "fcose",
  animate: true,
  animationDuration: 500,
  randomize: false,
  fit: true,
  padding: 40,
  nodeRepulsion: 9000,
  idealEdgeLength: 90,
};

interface Props {
  elements: ElementDefinition[];
  labelMode: LabelMode;
  onNodeTap: (ip: string) => void;
  onNodeExpand: (ip: string) => void;
  onEdgeTap: (source: string, target: string) => void;
}

// Given name when present and requested, else always the IP.
function nodeLabel(d: any, mode: LabelMode): string {
  return mode === "name" && d.name ? d.name : d.ip;
}

export default function GraphCanvas({
  elements,
  labelMode,
  onNodeTap,
  onNodeExpand,
  onEdgeTap,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const cbRef = useRef({ onNodeTap, onNodeExpand, onEdgeTap });
  cbRef.current = { onNodeTap, onNodeExpand, onEdgeTap };

  // Initialise once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: STYLE,
      elements: [],
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;
    cy.on("tap", "node", (e) => cbRef.current.onNodeTap(e.target.id()));
    cy.on("cxttap", "node", (e) => cbRef.current.onNodeExpand(e.target.id()));
    cy.on("tap", "edge", (e) => {
      const d = e.target.data();
      cbRef.current.onEdgeTap(d.source, d.target);
    });
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Sync elements incrementally (add new, drop removed), then relayout if changed.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const incoming = new Set(elements.map((e) => String(e.data!.id)));
    let changed = false;
    cy.batch(() => {
      cy.elements().forEach((el) => {
        if (!incoming.has(el.id())) {
          el.remove();
          changed = true;
        }
      });
      elements.forEach((el) => {
        const existing = cy.getElementById(String(el.data!.id));
        if (existing.empty()) {
          cy.add(el);
          changed = true;
        } else {
          existing.data(el.data!);
        }
      });
    });
    if (changed) cy.layout(LAYOUT).run();
  }, [elements]);

  // Relabel nodes when the IP/Name toggle changes (and after new nodes are added,
  // since this effect is keyed on `elements` too and declared after the sync effect).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      n.data("label", nodeLabel(n.data(), labelMode));
    });
  }, [labelMode, elements]);

  return <div ref={containerRef} className="graph" />;
}
