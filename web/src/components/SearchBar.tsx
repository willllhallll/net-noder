import { useState } from "react";
import { api } from "../api";
import type { NodeT } from "../types";
import { fmtBytes, nodeColor } from "../format";
import Spinner from "./Spinner";

interface Props {
  onPick: (ip: string) => void;
}

// Top-bar control: endpoint search (IP prefix, given-name or WHOIS-name substring).
// Picking a result focuses it. (Label toggles now live in the FilterPanel.)
export default function SearchBar({ onPick }: Props) {
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
            {busy && (
              <li className="muted search-busy">
                <Spinner size="sm" /> searching…
              </li>
            )}
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
