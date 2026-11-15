import { formatHazardLabel, tierClass } from "../tierUtils.js";

export default function ComparisonTable({ artifact }) {
  const data = artifact?.data || {};
  const hubs = data.hubs || [];
  const hazards = data.hazards || [];
  const winners = data.winners || {};

  if (hubs.length === 0) {
    return null;
  }

  return (
    <div className="artifact-card">
      <div className="artifact-header">
        <span className="artifact-title">🎀 {artifact.title}</span>
        <span className="artifact-meta">{hubs.length} hubs compared</span>
      </div>
      <div className="table-scroll">
        <table className="pink-table">
          <thead>
            <tr>
              <th>Hazard</th>
              {hubs.map((h) => (
                <th key={h.code}>
                  {h.city} ({h.code})
                </th>
              ))}
              <th>Highest Exposure</th>
            </tr>
          </thead>
          <tbody>
            {hazards.map((hazard) => {
              const winner = winners[hazard];
              const tied = winner?.tied_hubs || [];
              return (
                <tr key={hazard}>
                  <td className="hazard-name-cell">{formatHazardLabel(hazard)}</td>
                  {hubs.map((h) => {
                    const val = h.hazard_scores?.[hazard];
                    const isWinner = tied.includes(h.code);
                    return (
                      <td key={h.code} className={isWinner ? "winner-cell" : ""}>
                        <span className="score-num">
                          {val !== undefined && val !== null ? Number(val).toFixed(1) : "—"}
                        </span>
                        {isWinner && <span className="crown-tag" title="Highest exposure">✨</span>}
                      </td>
                    );
                  })}
                  <td>
                    {winner ? (
                      <span className={`tier-pill ${tierClass(winner.score >= 75 ? "Severe" : winner.score >= 50 ? "High" : winner.score >= 25 ? "Moderate" : "Low")}`}>
                        {tied.join(", ")} ({Number(winner.score).toFixed(1)})
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
