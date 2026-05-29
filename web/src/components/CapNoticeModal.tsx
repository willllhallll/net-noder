import type { GraphMeta } from "../types";
import { fmtNum } from "../format";

interface Props {
  notice: GraphMeta | null;
  onClose: () => void;
}

// Shown when the dataset exceeded the host-view node cap. The graph keeps the
// highest-traffic endpoints; this explains the cap and that the rest is still
// reachable by clicking an endpoint or using Find endpoint.
export default function CapNoticeModal({ notice, onClose }: Props) {
  if (!notice) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>
          ×
        </button>
        <h2>Too many endpoints</h2>
        <p>
          Showing the {fmtNum(notice.shown_endpoints)} highest-traffic endpoints of{" "}
          {fmtNum(notice.total_endpoints)} total. A cap of {fmtNum(notice.cap)}{" "}
          endpoints is applied to keep the graph responsive.
        </p>
        <p className="muted">
          Nothing is lost: clicking an endpoint and the Find endpoint search still
          reach the full dataset — drilling down reveals every connection.
        </p>
        <button className="modal-btn" onClick={onClose}>
          Got it
        </button>
      </div>
    </div>
  );
}
