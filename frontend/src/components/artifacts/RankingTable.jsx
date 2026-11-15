import { formatHazardLabel, tierClass } from "../tierUtils.js";

export default function RankingTable({ artifact }) {
  const data = artifact?.data || {};
  const items = data.items || [];

  if (items.length === 0) {
    return (
      <div className="artifact-card">
        <div className="artifact-header">
          <span className="artifact-title">👑 {artifact?.title || "Hub Ranking"}</span>
        </div>
        <p className="muted-note">No matching hubs scored yet.</p>
      </div>
    );
  }

  return (
    <div className="artifact-card">
      <div className="artifact-header">
        <span className="artifact-title">👑 {artifact.title}</span>
        <span className="artifact-meta">
          {formatHazardLabel(data.hazard)} · {data.score_kind || "structural"}
        </span>
      </div>
      <div className="table-scroll">
        <table className="pink-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Hub</th>
              <th>Region</th>
              <th>Score</th>
              <th>Tier</th>
              <th>Top Driver</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.code}>
                <td className="rank-cell">{item.rank}</td>
                <td>
                  <div className="hub-cell">
                    <strong>{item.code}</strong>
                    <span>
                      {item.city}, {item.state}
                    </span>
                  </div>
                </td>
                <td>{item.region}</td>
                <td>
                  <div className="score-inline">
                    <span className="score-num">{Number(item.score).toFixed(1)}</span>
                    <div className="mini-bar-track">
                      <div
                        className={`mini-bar-fill ${tierClass(item.tier)}`}
                        style={{ width: `${Math.min(100, Math.max(0, Number(item.score)))}%` }}
                      />
                    </div>
                  </div>
                </td>
                <td>
                  <span className={`tier-pill ${tierClass(item.tier)}`}>{item.tier}</span>
                </td>
                <td className="driver-cell">{item.top_driver || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
