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
        <h2>Alerts &amp; Status</h2>
        <span className={`sse-badge ${streamConnected ? "live" : "offline"}`}>
          {streamConnected ? "● Live" : "○ Syncing"}
        </span>
      </div>

      <div className="health-strip">
        <div className="health-strip-row">
          <span className={`status-dot ${isBackendOk ? "ok" : "err"}`}>
            API {isBackendOk ? "Online" : "Offline"}
          </span>
          <span className={`status-dot ${isDbOk ? "ok" : "err"}`}>
            DB {isDbOk ? "OK" : "Checking"}
          </span>
        </div>
        {health?.weather_data_until && (
          <div className="health-strip-meta">
            Data through {health.weather_data_until}
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
        Check Live NWS Alerts
      </button>

      <div className="alerts-feed">
        <div className="alerts-feed-title">Recent Risk Changes</div>
        {loading && <p className="muted-note">Checking alerts…</p>}
        {!loading && alerts.length === 0 && (
          <div className="empty-alerts-box">
            No risk-change alerts recorded yet. Score and tier changes appear here automatically.
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
