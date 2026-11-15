import { severityClass } from "../tierUtils.js";

export default function AlertsList({ artifact }) {
  const data = artifact?.data || {};
  const alerts = data.alerts || [];

  return (
    <div className="artifact-card">
      <div className="artifact-header">
        <span className="artifact-title">{artifact.title}</span>
        <span className="artifact-meta">{alerts.length} active</span>
      </div>

      {alerts.length === 0 ? (
        <p className="muted-note">No active NWS weather alerts match this filter.</p>
      ) : (
        <ul className="alerts-artifact-list">
          {alerts.map((alert, idx) => (
            <li key={`${alert.hub}-${idx}`} className="alert-artifact-item">
              <div className="alert-artifact-top">
                <strong>
                  {alert.city} ({alert.hub})
                </strong>
                <span className={`tier-pill ${severityClass(alert.severity)}`}>
                  {alert.severity}
                </span>
              </div>
              <div className="alert-event-name">{alert.event}</div>
              {alert.headline && <p className="alert-headline">{alert.headline}</p>}
              <div className="alert-meta-row">
                <span>
                  Operational: {Number(alert.operational_score || 0).toFixed(1)} (
                  {alert.operational_tier})
                </span>
                {alert.expires && <span>Expires: {alert.expires}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
