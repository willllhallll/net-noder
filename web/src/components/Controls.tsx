import { useState } from "react";
import { api } from "../api";
import type { LabelMode, NodeT } from "../types";
import { fmtBytes, nodeColor } from "../format";

interface Props {
  labelMode: LabelMode;
  setLabelMode: (m: LabelMode) => void;
  onPick: (ip: string) => void;
}

export default function Controls({
  labelMode,
  setLabelMode,
  onPick,
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

  return (
    <div className="controls">
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
            placeholder="Name or IP, e.g. Laptop or 192.168"
            value={q}
            onChange={(e) => runSearch(e.target.value)}
          />
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
