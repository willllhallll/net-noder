import type { Category } from "../types";

interface Props {
  categories: Category[];
  active: Set<string>;
  setActive: (s: Set<string>) => void;
  nodeCount: number;
  edgeCount: number;
}

// The hosts-view filter: one toggle row per endpoint category (each VLAN + the fixed
// Public / Unassigned / Multicast / Broadcast buckets), driven entirely by the
// /api/categories registry so it grows dynamically with the ingested VLAN set.
// Unchecking a category hides its nodes (and, in App, any edges touching them).
export default function VlanFilter({
  categories,
  active,
  setActive,
  nodeCount,
  edgeCount,
}: Props) {
  const allOn = categories.length > 0 && active.size >= categories.length;

  function toggle(key: string) {
    const next = new Set(active);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setActive(next);
  }

  function toggleAll() {
    setActive(allOn ? new Set() : new Set(categories.map((c) => c.category_key)));
  }

  return (
    <div className="vlan-filter">
      <div className="legend-title">
        {nodeCount} endpoints · {edgeCount} connections
      </div>

      <button className="select-all-btn" onClick={toggleAll}>
        {allOn ? "Deselect all" : "Select all"}
      </button>

      <div className="cat-list">
        {categories.map((c) => (
          <label key={c.category_key} className="cat-row">
            <input
              type="checkbox"
              checked={active.has(c.category_key)}
              onChange={() => toggle(c.category_key)}
            />
            <span className="dot" style={{ background: c.colour }} />
            {c.label}
          </label>
        ))}
      </div>
    </div>
  );
}
