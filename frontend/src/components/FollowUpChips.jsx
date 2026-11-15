export default function FollowUpChips({ followUps = [], onSelect, disabled = false }) {
  if (!followUps || followUps.length === 0) {
    return null;
  }

  return (
    <div className="followup-row">
      <span className="followup-label">Ask next:</span>
      <div className="followup-chips">
        {followUps.map((question, idx) => (
          <button
            key={idx}
            type="button"
            className="followup-chip"
            disabled={disabled}
            onClick={() => onSelect?.(question)}
          >
            ✨ {question}
          </button>
        ))}
      </div>
    </div>
  );
}
