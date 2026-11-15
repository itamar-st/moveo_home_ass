import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AssumptionsBox from "./AssumptionsBox.jsx";
import FollowUpChips from "./FollowUpChips.jsx";
import ToolTrace from "./ToolTrace.jsx";
import AlertsList from "./artifacts/AlertsList.jsx";
import ComparisonTable from "./artifacts/ComparisonTable.jsx";
import RankingTable from "./artifacts/RankingTable.jsx";
import ScoreBreakdown from "./artifacts/ScoreBreakdown.jsx";
import StatCard from "./artifacts/StatCard.jsx";

function renderArtifact(artifact, idx) {
  switch (artifact.type) {
    case "ranking_table":
      return <RankingTable key={idx} artifact={artifact} />;
    case "comparison":
      return <ComparisonTable key={idx} artifact={artifact} />;
    case "score_breakdown":
      return <ScoreBreakdown key={idx} artifact={artifact} />;
    case "stat":
      return <StatCard key={idx} artifact={artifact} />;
    case "alerts":
      return <AlertsList key={idx} artifact={artifact} />;
    default:
      return null;
  }
}

export default function MessageBubble({
  message,
  onSelectFollowUp,
  isStreaming = false,
}) {
  if (message.role === "user") {
    return (
      <div className={`message-row user-row ${message.justSent ? "just-sent" : ""}`}>
        <div className="user-bubble">
          <p>{message.text}</p>
        </div>
      </div>
    );
  }

  const answer = message.answer || {};
  const warnings = message.warnings || [];
  const isFallback =
    message.model === "deterministic-fallback" ||
    warnings.some((w) => w.toLowerCase().includes("fallback active"));
  const confidence = answer.confidence || "medium";
  const toolCaveats = (message.toolTrace || []).flatMap((t) => t.caveats || []);

  return (
    <div className="message-row assistant-row">
      <div className="assistant-avatar" aria-hidden="true">
        🌸
      </div>
      <div className="assistant-bubble">
        <div className="assistant-top-meta">
          <div className="meta-badges">
            <span
              className={`confidence-badge conf-${confidence}`}
              title={answer.confidence_rationale || "Confidence rating"}
            >
              {confidence === "high" ? "💖" : confidence === "medium" ? "🌷" : "🫧"}{" "}
              {confidence} confidence
            </span>
            {answer.intent && (
              <span className="intent-pill">{answer.intent.replaceAll("_", " ")}</span>
            )}
            {(answer.hubs || []).map((code) => (
              <span key={code} className="hub-code-pill">
                {code}
              </span>
            ))}
          </div>
          {message.latencyMs !== null && message.latencyMs !== undefined && (
            <span className="latency-meta">
              {message.model ? `${message.model} · ` : ""}
              {(message.latencyMs / 1000).toFixed(1)}s
            </span>
          )}
        </div>

        {isFallback && (
          <div className="fallback-banner">
            ⚠️ <strong>Deterministic Fallback Active:</strong> The LLM was unavailable or
            couldn&apos;t complete within schema limits, so this summary was rendered directly
            from verified database tools.
          </div>
        )}

        {warnings
          .filter((w) => !w.toLowerCase().includes("fallback active"))
          .map((warn, idx) => (
            <div key={idx} className="warning-banner">
              ⚠️ {warn}
            </div>
          ))}

        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {answer.answer_markdown || ""}
          </ReactMarkdown>
        </div>

        {(answer.key_points?.length > 0 || answer.reasoning?.length > 0) && (
          <div className="reasoning-card">
            {answer.key_points?.length > 0 && (
              <div>
                <div className="reasoning-heading">✨ Key Takeaways</div>
                <ul>
                  {answer.key_points.map((pt, idx) => (
                    <li key={idx}>{pt}</li>
                  ))}
                </ul>
              </div>
            )}
            {answer.reasoning?.length > 0 && (
              <div>
                <div className="reasoning-heading">🎀 Analytical Reasoning</div>
                <ul>
                  {answer.reasoning.map((step, idx) => (
                    <li key={idx}>{step}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {(message.artifacts || []).length > 0 && (
          <div className="artifacts-stack">
            {message.artifacts.map((art, idx) => renderArtifact(art, idx))}
          </div>
        )}

        <ToolTrace trace={message.toolTrace} />

        <AssumptionsBox assumptions={answer.assumptions} caveats={toolCaveats} />

        <FollowUpChips
          followUps={answer.follow_ups}
          onSelect={onSelectFollowUp}
          disabled={isStreaming}
        />
      </div>
    </div>
  );
}
