import { useState } from "react";
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
  voiceSupported = false,
  isSpeakingThis = false,
  onSpeak,
  onStopSpeaking,
}) {
  const [activeTab, setActiveTab] = useState(null);

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
  const toolTrace = message.toolTrace || [];
  const toolCaveats = toolTrace.flatMap((t) => t.caveats || []);

  const assumptionItems = [...(answer.assumptions || [])];
  for (const c of toolCaveats) {
    if (!assumptionItems.includes(c)) {
      assumptionItems.push(c);
    }
  }

  const hasReasoning =
    (answer.key_points?.length || 0) > 0 || (answer.reasoning?.length || 0) > 0;

  const toggleTab = (tab) => {
    setActiveTab((prev) => (prev === tab ? null : tab));
  };

  return (
    <div className="message-row assistant-row">
      <div className="assistant-content">
        {isFallback && (
          <div className="fallback-banner">
            <strong>Deterministic Fallback:</strong> Rendered directly from verified database tools.
          </div>
        )}

        {warnings
          .filter((w) => !w.toLowerCase().includes("fallback active"))
          .map((warn, idx) => (
            <div key={idx} className="warning-banner">
              {warn}
            </div>
          ))}

        <div className="markdown-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {answer.answer_markdown || ""}
          </ReactMarkdown>
        </div>

        {(message.artifacts || []).length > 0 && (
          <div className="artifacts-stack">
            {message.artifacts.map((art, idx) => renderArtifact(art, idx))}
          </div>
        )}

        <div className="assistant-footer">
          <div className="assistant-footer-meta">
            <span
              className={`confidence-pill conf-${confidence}`}
              title={answer.confidence_rationale || "Confidence rating"}
            >
              <span className="conf-dot" aria-hidden="true" />
              {confidence} confidence
            </span>
            {message.latencyMs !== null && message.latencyMs !== undefined && (
              <span className="latency-meta">
                {(message.latencyMs / 1000).toFixed(1)}s
              </span>
            )}
          </div>

          <div className="assistant-footer-actions">
            {hasReasoning && (
              <button
                type="button"
                className={`meta-tab-btn ${activeTab === "reasoning" ? "active" : ""}`}
                onClick={() => toggleTab("reasoning")}
                aria-expanded={activeTab === "reasoning"}
              >
                Reasoning
              </button>
            )}
            {toolTrace.length > 0 && (
              <button
                type="button"
                className={`meta-tab-btn ${activeTab === "tools" ? "active" : ""}`}
                onClick={() => toggleTab("tools")}
                aria-expanded={activeTab === "tools"}
              >
                Tools ({toolTrace.length})
              </button>
            )}
            {assumptionItems.length > 0 && (
              <button
                type="button"
                className={`meta-tab-btn ${activeTab === "assumptions" ? "active" : ""}`}
                onClick={() => toggleTab("assumptions")}
                aria-expanded={activeTab === "assumptions"}
              >
                Assumptions ({assumptionItems.length})
              </button>
            )}
            {voiceSupported && answer.answer_markdown && (
              <button
                type="button"
                className={`meta-tab-btn voice-read-btn ${isSpeakingThis ? "speaking" : ""}`}
                onClick={() =>
                  isSpeakingThis
                    ? onStopSpeaking?.()
                    : onSpeak?.(answer.answer_markdown, message.id)
                }
                aria-label={
                  isSpeakingThis ? "Stop reading response" : "Read response aloud"
                }
              >
                {isSpeakingThis ? "⏹ Stop" : "🔊 Listen"}
              </button>
            )}
          </div>
        </div>

        {activeTab === "reasoning" && hasReasoning && (
          <div className="inspection-drawer">
            <div className="reasoning-card">
              {answer.key_points?.length > 0 && (
                <div>
                  <div className="reasoning-heading">Key Takeaways</div>
                  <ul>
                    {answer.key_points.map((pt, idx) => (
                      <li key={idx}>{pt}</li>
                    ))}
                  </ul>
                </div>
              )}
              {answer.reasoning?.length > 0 && (
                <div>
                  <div className="reasoning-heading">Analytical Reasoning</div>
                  <ul>
                    {answer.reasoning.map((step, idx) => (
                      <li key={idx}>{step}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "tools" && toolTrace.length > 0 && (
          <div className="inspection-drawer">
            <ToolTrace trace={toolTrace} embedded />
          </div>
        )}

        {activeTab === "assumptions" && assumptionItems.length > 0 && (
          <div className="inspection-drawer">
            <AssumptionsBox assumptions={answer.assumptions} caveats={toolCaveats} embedded />
          </div>
        )}

        <FollowUpChips
          followUps={answer.follow_ups}
          onSelect={onSelectFollowUp}
          disabled={isStreaming}
        />
      </div>
    </div>
  );
}
