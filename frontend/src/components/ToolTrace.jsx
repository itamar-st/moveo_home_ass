import { useState } from "react";

export default function ToolTrace({ trace, isLive = false }) {
  const [expanded, setExpanded] = useState(isLive);

  if (!trace || trace.length === 0) {
    return null;
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
          🪄 How I got this ({trace.length} tool call{trace.length > 1 ? "s" : ""}
          {totalMs > 0 ? ` · ${totalMs} ms` : ""})
        </span>
        <span className="chevron">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div className="tool-trace-list">
          {trace.map((item, idx) => {
            const statusIcon =
              item.ok === null ? "⏳" : item.ok ? "💖" : "⚠️";
            return (
              <div key={item.call_id || idx} className="tool-trace-item">
                <div className="tool-trace-row">
                  <span className="tool-badge">
                    {statusIcon} {item.name}
                  </span>
                  <span className="tool-step-meta">
                    Step {item.step}
                    {item.duration_ms > 0 ? ` · ${item.duration_ms} ms` : ""}
                  </span>
                </div>
                {item.rationale && (
                  <div className="tool-rationale">{item.rationale}</div>
                )}
                <div className="tool-args">
                  <code>{JSON.stringify(item.arguments || {})}</code>
                </div>
                {item.summary && (
                  <div className="tool-summary">{item.summary}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
