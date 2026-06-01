import { useState } from "react";
import { api } from "../api";
import type { LabelOpts, NodeT } from "../types";
import { fmtBytes, nodeColor } from "../format";

interface Props {
  labelOpts: LabelOpts;
  setLabelOpts: (m: LabelOpts) => void;
  onPick: (ip: string) => void;
}

// Top-bar control: two independent label toggles (given name / WHOIS name) + endpoint
// search (IP prefix, given-name or WHOIS-name substring). Picking a result focuses it.
export default function SearchBar({ labelOpts, setLabelOpts, onPick }: Props) {
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
      <div className="field">
        <span>Labels</span>
        <div className="label-toggles">
          <label className="label-toggle">
            <input
              type="checkbox"
              checked={labelOpts.given}
              onChange={(e) => setLabelOpts({ ...labelOpts, given: e.target.checked })}
            />
            Given name
          </label>
          <label className="label-toggle">
            <input
              type="checkbox"
              checked={labelOpts.whois}
              onChange={(e) => setLabelOpts({ ...labelOpts, whois: e.target.checked })}
            />
            WHOIS name
          </label>
        </div>
      </div>

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
                <span className="dot" style={{ background: nodeColor(r) }} />
                {r.given_name ? (
                  <>
                    <strong>{r.given_name}</strong>
                    <span className="muted"> {r.ip}</span>
                  </>
                ) : r.whois_name ? (
                  <>
                    <strong>{r.whois_name}</strong>
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
