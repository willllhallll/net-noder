import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { fmtNum } from "../format";

// One side's ports, colour-coded by the stack they belong to. Built for port churn:
// a single endpoint can use 10k+ ports, so the chip grid is windowed (only the rows in
// view are mounted) and a search box narrows the list. Each chip's border + dot take
// the colour passed alongside the port, so the column recolours in lock-step with the
// flow edges when the active tier changes.
export interface PortChip {
  port: number;
  colour: string;
}

interface Props {
  label: string;
  ports: PortChip[];
}

const CHIP_W = 64; // px, fixed so column count is computable for windowing
const CHIP_H = 22;
const GAP = 6;
const PAD = 6; // scroller padding (matches .port-col-vbody)
const ROW_H = CHIP_H + GAP;
const VIEW_H = 320; // visible scroll height
const OVERSCAN = 2; // extra rows above/below the viewport
const MAX_COLS = 2; // each port list is at most two chips wide

export default function PortColumn({ label, ports }: Props) {
  const [query, setQuery] = useState("");
  const [scrollTop, setScrollTop] = useState(0);
  const [width, setWidth] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim();
    if (!q) return ports;
    return ports.filter((p) => String(p.port).includes(q));
  }, [ports, query]);

  // Columns fit the padded inner width, so the right-most chip never spills past the
  // scroller (clientWidth includes the horizontal padding, so subtract it first). Capped
  // at MAX_COLS so each port list stays at most two chips wide, however wide the drawer.
  const avail = Math.max(CHIP_W, width - 2 * PAD);
  const cols = Math.min(MAX_COLS, Math.max(1, Math.floor((avail + GAP) / (CHIP_W + GAP))));
  const rows = Math.ceil(filtered.length / cols);
  const totalH = rows * ROW_H;

  const firstRow = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const lastRow = Math.min(rows, Math.ceil((scrollTop + VIEW_H) / ROW_H) + OVERSCAN);
  const start = firstRow * cols;
  const end = Math.min(filtered.length, lastRow * cols);

  const visible = [];
  for (let i = start; i < end; i++) {
    const { port, colour } = filtered[i];
    const r = Math.floor(i / cols);
    const c = i % cols;
    visible.push(
      <span
        key={port}
        className="port-chip"
        style={{
          position: "absolute",
          top: r * ROW_H,
          left: c * (CHIP_W + GAP),
          width: CHIP_W,
          borderColor: colour,
        }}
        title={`:${port}`}
      >
        <span className="port-chip-dot" style={{ background: colour }} />:{port}
      </span>
    );
  }

  return (
    <div className="port-col">
      <div className="port-col-head">
        {label} <span className="muted">({fmtNum(ports.length)})</span>
      </div>
      <input
        className="port-search"
        type="text"
        inputMode="numeric"
        placeholder="filter ports…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {ports.length === 0 ? (
        <div className="port-col-empty muted">—</div>
      ) : (
        <div
          ref={bodyRef}
          className="port-col-vbody"
          style={{ height: VIEW_H }}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
        >
          <div style={{ position: "relative", height: totalH, width: avail }}>
            {visible}
          </div>
        </div>
      )}
    </div>
  );
}
