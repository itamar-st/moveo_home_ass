import { useEffect, useState } from "react";
import { fetchScoringMeta } from "../api/client.js";
import { formatHazardLabel, formatMetricLabel } from "./tierUtils.js";

export default function MethodologyModal({ isOpen, onClose }) {
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!isOpen || meta) {
      return;
    }
    let active = true;
    setLoading(true);
    fetchScoringMeta()
      .then((data) => {
        if (active) {
          setMeta(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || "Failed to load scoring methodology");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [isOpen, meta]);

  if (!isOpen) {
    return null;
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2>💖 WDRI Scoring Methodology</h2>
            <p className="panel-subtitle">
              Deterministic Weather Disruption Risk Index ({meta?.scoring_version || "wdri-v1"})
            </p>
          </div>
          <button
            type="button"
            className="icon-close-btn"
            onClick={onClose}
            aria-label="Close methodology modal"
          >
            ✕
          </button>
        </div>

        <div className="modal-body">
          {loading && <p className="muted-note">Loading scoring parameters…</p>}
          {error && <p className="error-inline">{error}</p>}

          {meta && (
            <>
              <section className="meta-section">
                <h3>Composite Formula</h3>
                <p className="formula-box">
                  <code>
                    WDRI = {meta.weights?.composite_blend?.weighted_avg ?? 0.7} × Σ(w_h ×
                    score_h) + {meta.weights?.composite_blend?.max_hazard ?? 0.3} ×
                    max(hazard scores)
                  </code>
                </p>
                <div className="meta-chips-grid">
                  {Object.entries(meta.weights?.hazards || {}).map(([k, w]) => (
                    <div key={k} className="meta-chip">
                      <span>{formatHazardLabel(k)}</span>
                      <strong>{Math.round(Number(w) * 100)}%</strong>
                    </div>
                  ))}
                </div>
              </section>

              <section className="meta-section">
                <h3>Risk Tiers &amp; Live Alert Bumps</h3>
                <div className="meta-two-col">
                  <div>
                    <ul className="meta-list">
                      {(meta.tiers || []).map((t) => (
                        <li key={t.name}>
                          <strong>{t.name}:</strong> {t.min} – {t.max}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <ul className="meta-list">
                      {Object.entries(meta.alert_bumps || {}).map(([sev, pts]) => (
                        <li key={sev}>
                          <strong>{sev} NWS Alert:</strong> +{pts} operational pts
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </section>

              <section className="meta-section">
                <h3>Observed Metric Thresholds &amp; Caps (5-yr mean)</h3>
                <div className="meta-chips-grid">
                  {Object.entries(meta.caps || {}).map(([metric, cap]) => (
                    <div key={metric} className="meta-chip">
                      <span>{formatMetricLabel(metric)}</span>
                      <strong>Cap: {cap}/yr</strong>
                    </div>
                  ))}
                </div>
              </section>

              <section className="meta-section">
                <h3>Public Data Sources</h3>
                <ul className="meta-list">
                  {(meta.data_sources || []).map((src, idx) => (
                    <li key={idx}>
                      <strong>{src.name}:</strong> {src.purpose || src.cadence || ""}
                    </li>
                  ))}
                </ul>
              </section>

              <section className="meta-section">
                <h3>Explicit Assumptions &amp; Uncertainty</h3>
                <ul className="meta-list">
                  {(meta.assumptions || []).map((a, idx) => (
                    <li key={idx}>{a}</li>
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
