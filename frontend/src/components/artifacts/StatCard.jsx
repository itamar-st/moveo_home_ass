import { formatMetricLabel } from "../tierUtils.js";

export default function StatCard({ artifact }) {
  const d = artifact?.data || {};
  const isTotal = d.unit === "cm" || d.unit === "mm";
  const periodRange = Array.isArray(d.period_resolved)
    ? `${d.period_resolved[0]} → ${d.period_resolved[1]}`
    : "";

  return (
    <div className="artifact-card stat-card">
      <div className="artifact-header">
        <span className="artifact-title">✨ {artifact.title}</span>
        <span className="artifact-meta">{periodRange}</span>
      </div>

      <div className="stat-hero-grid">
        <div className="stat-big">
          <div className="stat-number">
            {isTotal ? `${Number(d.value || 0).toFixed(1)} ${d.unit}` : `${Number(d.percentage || 0).toFixed(2)}%`}
          </div>
          <div className="stat-caption">
            {isTotal
              ? `Total ${formatMetricLabel(d.metric)}`
              : `${d.days_matching} of ${d.days_total} observed days`}
          </div>
        </div>

        <div className="stat-details">
          <div>
            <span className="stat-dt">Metric</span>
            <span className="stat-dd">{formatMetricLabel(d.metric)}</span>
          </div>
          <div>
            <span className="stat-dt">Threshold</span>
            <span className="stat-dd">{d.threshold || "—"}</span>
          </div>
          <div>
            <span className="stat-dt">Matching Days</span>
            <span className="stat-dd">
              {d.days_matching} / {d.days_total}
            </span>
          </div>
          <div>
            <span className="stat-dt">Data Coverage</span>
            <span className="stat-dd">{Number(d.coverage_pct || 0).toFixed(1)}%</span>
          </div>
        </div>
      </div>
    </div>
  );
}
