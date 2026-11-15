import { useEffect, useState } from "react";
import { fetchHealth } from "./api/client.js";

export default function App() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetchHealth()
      .then((data) => {
        if (active) {
          setHealth(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err.message || "Failed to reach backend");
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
  }, []);

  const isBackendOk = health?.status === "ok";
  const isDbOk = health?.db === "ok";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Weather Risk Intelligence Agent</h1>
          <p className="subtitle">US Distribution Hub Weather Disruption Analytics</p>
        </div>
        <div className="status-badges">
          {loading && <span className="badge badge-neutral">Checking backend…</span>}
          {!loading && error && <span className="badge badge-error">Backend unreachable</span>}
          {!loading && health && (
            <>
              <span className={`badge ${isBackendOk ? "badge-ok" : "badge-error"}`}>
                {isBackendOk ? "backend ok" : "backend error"}
              </span>
              <span className={`badge ${isDbOk ? "badge-ok" : "badge-error"}`}>
                {isDbOk ? "db ok" : "db error"}
              </span>
            </>
          )}
        </div>
      </header>

      <main className="app-main">
        <section className="card">
          <h2>System Status</h2>
          {loading && <p>Connecting to API…</p>}
          {error && <p className="error-text">Error: {error}</p>}
          {health && (
            <dl className="status-grid">
              <dt>Status</dt>
              <dd>{health.status}</dd>
              <dt>Database</dt>
              <dd>{health.db}</dd>
              <dt>Scheduler</dt>
              <dd>{health.scheduler}</dd>
              <dt>Scoring Version</dt>
              <dd>{health.scoring_version}</dd>
              <dt>Model</dt>
              <dd>{health.model}</dd>
              <dt>Last Scored At</dt>
              <dd>{health.last_scored_at ?? "Not yet scored"}</dd>
              <dt>Weather Data Until</dt>
              <dd>{health.weather_data_until ?? "Not yet backfilled"}</dd>
            </dl>
          )}
        </section>
      </main>
    </div>
  );
}
