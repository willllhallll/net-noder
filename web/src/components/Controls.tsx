import { useState } from "react";
import { api } from "../api";
import type { LabelMode, Metric, NodeT } from "../types";
import { fmtBytes, nodeColor } from "../format";

interface Props {
  metric: Metric;
  setMetric: (m: Metric) => void;
  limit: number;
  setLimit: (n: number) => void;
  labelMode: LabelMode;
  setLabelMode: (m: LabelMode) => void;
  onPick: (ip: string) => void;
  onReset: () => void;
}

export default function Controls({
  metric,
  setMetric,
  limit,
  setLimit,
  labelMode,
  setLabelMode,
  onPick,
  onReset,
}: Props) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<NodeT[]>([]);
  const [busy, setBusy] = useState(false);

  async function runSearch(value: string) {
    setQ(value);
    if (value.trim().length < 1) {
      setResults([]);
      return;
    }
    setBusy(true);
    try {
      setResults(await api.search(value.trim()));
    } catch {
      setResults([]);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setQ("");
    setResults([]);
    onReset();
  }

  return (
    <div className="controls">
      <label className="field">
        <span>Rank by</span>
        <select
          value={metric}
          onChange={(e) => setMetric(e.target.value as Metric)}
        >
          <option value="bytes">Bytes</option>
          <option value="pkts">Packets</option>
        </select>
      </label>

      <label className="field">
        <span>Top N edges</span>
        <input
          type="number"
          min={1}
          max={2000}
          value={limit}
          onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
        />
      </label>

      <label className="field">
        <span>Labels</span>
        <select
          value={labelMode}
          onChange={(e) => setLabelMode(e.target.value as LabelMode)}
        >
          <option value="ip">IP</option>
          <option value="name">Given name</option>
        </select>
      </label>

      <div className="field search">
        <span>Find endpoint</span>
        <div className="search-row">
          <input
            type="text"
            placeholder="IP prefix, e.g. 192.168"
            value={q}
            onChange={(e) => runSearch(e.target.value)}
          />
          <button
            className="reset-btn"
            onClick={reset}
            title="Clear search and return to the top-N view"
          >
            Reset
          </button>
        </div>
        {results.length > 0 && (
          <ul className="search-results">
            {busy && <li className="muted">searching…</li>}
            {results.map((r) => (
              <li
                key={r.ip}
                onClick={() => {
                  onPick(r.ip);
                  setResults([]);
                  setQ("");
                }}
              >
                <span
                  className="dot"
                  style={{ background: nodeColor(r.kind, r.is_local) }}
                />
                {r.given_name ? (
                  <>
                    <strong>{r.given_name}</strong>
                    <span className="muted"> {r.ip}</span>
                  </>
                ) : (
                  r.ip
                )}
                <span className="muted"> · {fmtBytes(r.total_bytes)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
