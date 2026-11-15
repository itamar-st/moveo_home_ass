import { useState } from "react";

function TraceList({ trace }) {
  return (
    <div className="tool-trace-list">
      {trace.map((item, idx) => {
        const statusText = item.ok === null ? "Running" : item.ok ? "OK" : "Error";
        const statusClass = item.ok === null ? "pending" : item.ok ? "ok" : "err";
        return (
          <div key={item.call_id || idx} className="tool-trace-item">
            <div className="tool-trace-row">
              <span className="tool-badge">
                <span className={`trace-status-dot ${statusClass}`} aria-hidden="true" />
                {item.name}
              </span>
              <span className="tool-step-meta">
                Step {item.step} · {statusText}
                {item.duration_ms > 0 ? ` · ${item.duration_ms} ms` : ""}
              </span>
            </div>
            {item.rationale && <div className="tool-rationale">{item.rationale}</div>}
            <div className="tool-args">
              <code>{JSON.stringify(item.arguments || {})}</code>
            </div>
            {item.summary && <div className="tool-summary">{item.summary}</div>}
          </div>
        );
      })}
    </div>
  );
}

export default function ToolTrace({ trace, isLive = false, embedded = false }) {
  const [expanded, setExpanded] = useState(isLive);

  if (!trace || trace.length === 0) {
    return null;
  }

  if (embedded) {
    return <TraceList trace={trace} />;
  }

  const totalMs = trace.reduce((sum, item) => sum + (Number(item.duration_ms) || 0), 0);

  return (
    <div className="tool-trace">
      <button
        type="button"
        className="tool-trace-toggle"
        onClick={() => setExpanded((prev) => !prev)}
      >
        <span>
          Tool calls ({trace.length}
          {totalMs > 0 ? ` · ${totalMs} ms` : ""})
        </span>
        <span className="chevron">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && <TraceList trace={trace} />}
    </div>
  );
}
