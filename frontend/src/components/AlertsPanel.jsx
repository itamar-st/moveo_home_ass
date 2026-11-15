import { severityClass } from "./tierUtils.js";

export default function AlertsPanel({
  health,
  alerts = [],
  loading = false,
  streamConnected = false,
  onAskAlerts,
}) {
  const isBackendOk = health?.status === "ok";
  const isDbOk = health?.db === "ok";

  return (
    <aside className="alerts-panel">
      <div className="panel-header alerts-panel-header">
        <div>
          <h2>🎀 Risk Pulse &amp; Alerts</h2>
          <p className="panel-subtitle">Operational overlay &amp; system health</p>
        </div>
        <span className={`sse-badge ${streamConnected ? "live" : "offline"}`}>
          {streamConnected ? "● Live" : "○ Syncing"}
        </span>
      </div>

      <div className="health-card">
        <div className="health-row">
          <span>API Status</span>
          <span className={`status-dot ${isBackendOk ? "ok" : "err"}`}>
            {isBackendOk ? "Online 💖" : "Offline"}
          </span>
        </div>
        <div className="health-row">
          <span>Database</span>
          <span className={`status-dot ${isDbOk ? "ok" : "err"}`}>
            {isDbOk ? "Connected" : "Checking"}
          </span>
        </div>
        {health?.scoring_version && (
          <div className="health-row">
            <span>Scoring</span>
            <span>{health.scoring_version}</span>
          </div>
        )}
        {health?.weather_data_until && (
          <div className="health-row">
            <span>Weather Until</span>
            <span>{health.weather_data_until}</span>
          </div>
        )}
        {health?.model && (
          <div className="health-row">
            <span>Model</span>
            <span className="model-truncate" title={health.model}>
              {health.model}
            </span>
          </div>
        )}
      </div>

      <button
        type="button"
        className="check-alerts-btn"
        onClick={() =>
          onAskAlerts?.("Are there any active severe weather alerts at our hubs right now?")
        }
      >
        🌩️ Ask Agent for Live NWS Alerts
      </button>

      <div className="alerts-feed">
        <div className="alerts-feed-title">Recent Score &amp; Tier Alerts</div>
        {loading && <p className="muted-note">Checking alerts…</p>}
        {!loading && alerts.length === 0 && (
          <div className="empty-alerts-box">
            <span>🌸</span>
            <p>
              No risk-change alerts recorded yet. Live score &amp; tier changes stream here
              automatically.
            </p>
          </div>
        )}
        {!loading &&
          alerts.map((alert) => (
            <div key={alert.id} className="alert-feed-card">
              <div className="alert-feed-top">
                <strong>{alert.hub}</strong>
                <span className={`tier-pill ${severityClass(alert.alert_type)}`}>
                  {alert.alert_type?.replaceAll("_", " ")}
                </span>
              </div>
              <p className="alert-feed-msg">{alert.message}</p>
              <div className="alert-feed-time">
                {alert.old_score !== null && alert.old_score !== undefined
                  ? `${Number(alert.old_score).toFixed(1)} → `
                  : ""}
                {Number(alert.new_score).toFixed(1)}
              </div>
            </div>
          ))}
      </div>
    </aside>
  );
}
