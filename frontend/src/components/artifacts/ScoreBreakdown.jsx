import { formatHazardLabel, formatMetricLabel, tierClass } from "../tierUtils.js";

export default function ScoreBreakdown({ artifact }) {
  const data = artifact?.data || {};
  const details = data.details || {};
  const hazards = details.hazards || {};
  const drivers = details.drivers || [];
  const hazardEntries = Object.entries(hazards).sort(
    (a, b) => (b[1]?.contribution || 0) - (a[1]?.contribution || 0)
  );

  return (
    <div className="artifact-card">
      <div className="artifact-header">
        <span className="artifact-title">🌸 {artifact.title}</span>
        <span className="artifact-meta">
          Rank #{data.rank} of {data.total_hubs} · {data.region}
        </span>
      </div>

      <div className="kpi-pair">
        <div className="kpi-pill">
          <span className="kpi-label">Structural WDRI</span>
          <div className="kpi-value-row">
            <strong>{Number(data.overall_score || 0).toFixed(1)}</strong>
            <span className={`tier-pill ${tierClass(data.overall_tier)}`}>
              {data.overall_tier}
            </span>
          </div>
        </div>
        <div className="kpi-pill">
          <span className="kpi-label">Operational Score</span>
          <div className="kpi-value-row">
            <strong>{Number(data.operational_score || 0).toFixed(1)}</strong>
            <span className={`tier-pill ${tierClass(data.operational_tier)}`}>
              {data.operational_tier}
            </span>
          </div>
        </div>
      </div>

      <div className="breakdown-bars">
        {hazardEntries.map(([key, info]) => {
          const score = Number(info?.score || 0);
          const contrib = Number(info?.contribution || 0);
          const metrics = info?.metrics || {};
          const metricPreview = Object.entries(metrics)
            .map(([mKey, mVal]) => `${formatMetricLabel(mKey)}: ${mVal}/yr`)
            .join(" · ");

          return (
            <div key={key} className="breakdown-row">
              <div className="breakdown-top">
                <span className="breakdown-name">{formatHazardLabel(key)}</span>
                <span className="breakdown-vals">
                  Score <strong>{score.toFixed(1)}</strong> · +{contrib.toFixed(1)} pts to WDRI
                </span>
              </div>
              <div className="contrib-track">
                <div
                  className="contrib-fill"
                  style={{ width: `${Math.min(100, Math.max(2, score))}%` }}
                />
              </div>
              {metricPreview && <div className="breakdown-sub">{metricPreview}</div>}
            </div>
          );
        })}
      </div>

      {drivers.length > 0 && (
        <div className="drivers-box">
          <div className="drivers-title">Top Risk Drivers</div>
          <ul>
            {drivers.slice(0, 5).map((driver, idx) => (
              <li key={idx}>{driver}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
