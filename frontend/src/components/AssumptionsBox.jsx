import { useState } from "react";

export default function AssumptionsBox({ assumptions = [], caveats = [] }) {
  const [open, setOpen] = useState(false);
  const items = [...(assumptions || [])];
  for (const c of caveats || []) {
    if (!items.includes(c)) {
      items.push(c);
    }
  }

  if (items.length === 0) {
    return null;
  }

  return (
    <div className="assumptions-box">
      <button
        type="button"
        className="assumptions-toggle"
        onClick={() => setOpen((v) => !v)}
      >
        <span>🌷 Assumptions &amp; Scope ({items.length})</span>
        <span>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <ul className="assumptions-list">
          {items.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
