import { formatHazardLabel, tierClass } from "./tierUtils.js";

const REGIONS = [
  "",
  "Midwest",
  "Northeast",
  "Southeast",
  "South Central",
  "Mountain",
  "West",
];

const HAZARDS = ["overall", "winter", "hurricane", "flood", "severe_storm", "heat"];

export default function HubSidebar({
  rankings,
  region,
  onRegionChange,
  hazard,
  onHazardChange,
  scoreKind,
  onScoreKindChange,
  loading,
  error,
  selectedHubCode,
  selectedHubDetail,
  loadingDetail,
  onSelectHub,
  onAskAboutHub,
}) {
  return (
    <aside className="sidebar-panel">
      <div className="panel-header">
        <h2>🩷 Portfolio Hubs</h2>
        <span className="panel-count">{rankings.length}</span>
      </div>

      <div className="filter-stack">
        <div className="filter-row-two">
          <label className="filter-field">
            <span>Region</span>
            <select value={region} onChange={(e) => onRegionChange(e.target.value)}>
              <option value="">All Regions</option>
              {REGIONS.filter(Boolean).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>

          <label className="filter-field">
            <span>Hazard</span>
            <select value={hazard} onChange={(e) => onHazardChange(e.target.value)}>
              {HAZARDS.map((h) => (
                <option key={h} value={h}>
                  {h === "overall" ? "Overall" : formatHazardLabel(h)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {hazard === "overall" && (
          <div className="score-kind-toggle" role="group" aria-label="Score type">
            <button
              type="button"
              className={scoreKind === "structural" ? "active" : ""}
              onClick={() => onScoreKindChange("structural")}
            >
              Structural
            </button>
            <button
              type="button"
              className={scoreKind === "operational" ? "active" : ""}
              onClick={() => onScoreKindChange("operational")}
            >
              Operational
            </button>
          </div>
        )}
      </div>

      {selectedHubCode && (
        <div className="hub-detail-card">
          <div className="hub-detail-top">
            <strong>
              {selectedHubDetail?.city || selectedHubCode} ({selectedHubCode})
            </strong>
            <button
              type="button"
              className="icon-close-btn"
              onClick={() => onSelectHub(null)}
              aria-label="Close hub details"
            >
              ✕
            </button>
          </div>

          {loadingDetail && <p className="muted-note">Loading breakdown…</p>}

          {!loadingDetail && selectedHubDetail && (
            <>
              <div className="hub-detail-scores">
                <span>
                  Structural:{" "}
                  <strong>
                    {selectedHubDetail.overall_score !== null
                      ? Number(selectedHubDetail.overall_score).toFixed(1)
                      : "—"}
                  </strong>{" "}
                  ({selectedHubDetail.overall_tier || "—"})
                </span>
                <span>
                  Operational:{" "}
                  <strong>
                    {selectedHubDetail.operational_score !== null
                      ? Number(selectedHubDetail.operational_score).toFixed(1)
                      : "—"}
                  </strong>
                </span>
              </div>

              {selectedHubDetail.details?.hazards && (
                <div className="hub-detail-hazards">
                  {Object.entries(selectedHubDetail.details.hazards).map(([k, v]) => (
                    <div key={k} className="mini-hazard-chip">
                      <span>{formatHazardLabel(k)}</span>
                      <strong>{Number(v.score || 0).toFixed(0)}</strong>
                    </div>
                  ))}
                </div>
              )}

              <button
                type="button"
                className="ask-hub-btn"
                onClick={() =>
                  onAskAboutHub?.(
                    `Why is ${selectedHubDetail.city} (${selectedHubDetail.code}) hub's weather disruption risk ${selectedHubDetail.overall_tier || "at this level"}?`
                  )
                }
              >
                Explain {selectedHubDetail.code} in Chat
              </button>
            </>
          )}
        </div>
      )}

      <div className="hub-list">
        {loading && <div className="muted-note">Loading rankings…</div>}
        {error && <div className="error-inline">{error}</div>}
        {!loading && !error && rankings.length === 0 && (
          <div className="muted-note">No scored snapshots available yet.</div>
        )}

        {rankings.map((hub) => {
          const isSelected = selectedHubCode === hub.code;
          return (
            <button
              key={hub.code}
              type="button"
              className={`hub-card ${isSelected ? "selected" : ""}`}
              onClick={() => onSelectHub(isSelected ? null : hub.code)}
            >
              <div className="hub-card-top">
                <span className="hub-rank-badge">{hub.rank}</span>
                <span className="hub-name">
                  <strong>{hub.code}</strong> · {hub.city}, {hub.state}
                </span>
                <span className="hub-score-val">{Number(hub.score || 0).toFixed(1)}</span>
              </div>
              <div className="hub-card-score-row">
                <div className="mini-bar-track">
                  <div
                    className={`mini-bar-fill ${tierClass(hub.tier)}`}
                    style={{ width: `${Math.min(100, Math.max(0, Number(hub.score || 0)))}%` }}
                  />
                </div>
                <span className={`tier-pill ${tierClass(hub.tier)}`}>{hub.tier}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
