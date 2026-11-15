import { useEffect, useRef, useState } from "react";
import MessageBubble from "./MessageBubble.jsx";
import ToolTrace from "./ToolTrace.jsx";

const STARTER_QUESTIONS = [
  {
    id: "q1",
    tag: "Winter Ranking",
    icon: "❄️",
    text: "Which hubs in the Midwest are most exposed to winter disruption?",
  },
  {
    id: "q2",
    tag: "Hub Comparison",
    icon: "🌀",
    text: "Compare Miami and Houston in terms of hurricane and flood exposure.",
  },
  {
    id: "q3",
    tag: "Weather Stat",
    icon: "🏔️",
    text: "What percentage of days in Denver last year had snowfall?",
  },
  {
    id: "q4",
    tag: "Driver Breakdown",
    icon: "⚡",
    text: "Why is the Dallas hub's weather disruption risk high?",
  },
];

const SPARKLE_EMOJIS = ["💖", "✨", "🌸", "🎀", "💫", "🩷", "🌷"];

function createBurstParticles(burstId) {
  return Array.from({ length: 14 }, (_, idx) => {
    const angle = (-155 + (idx / 13) * 140) * (Math.PI / 180);
    const distance = 44 + (idx % 4) * 18;
    return {
      id: `${burstId}-${idx}`,
      emoji: SPARKLE_EMOJIS[idx % SPARKLE_EMOJIS.length],
      tx: `${Math.round(Math.cos(angle) * distance)}px`,
      ty: `${Math.round(Math.sin(angle) * distance - 18)}px`,
      rot: `${(idx % 2 === 0 ? 1 : -1) * (15 + (idx % 5) * 12)}deg`,
      delay: `${(idx % 4) * 35}ms`,
    };
  });
}

function formatStageLabel(statusStage) {
  if (!statusStage) return "Thinking… 🌸";
  switch (statusStage.stage) {
    case "calling_tool":
      return `Running deterministic tool: ${statusStage.detail || "lookup"}… ✨`;
    case "repairing_output":
      return "Validating & refining structured output… 🎀";
    case "fallback":
      return "Building deterministic backup answer… 🌷";
    default:
      return `Analyzing step ${statusStage.step || 1}… 💖`;
  }
}

export default function ChatWindow({
  sessionId,
  messages,
  loadingHistory,
  isStreaming,
  statusStage,
  liveTrace,
  errorState,
  onClearError,
  sendBurstKey,
  onSendMessage,
  onNewChat,
  onOpenMethodology,
}) {
  const [draft, setDraft] = useState("");
  const [particles, setParticles] = useState([]);
  const [buttonPopping, setButtonPopping] = useState(false);

  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveTrace, isStreaming]);

  useEffect(() => {
    if (!sendBurstKey) {
      return;
    }
    setParticles(createBurstParticles(sendBurstKey));
    setButtonPopping(true);

    const popTimer = window.setTimeout(() => setButtonPopping(false), 450);
    const clearTimer = window.setTimeout(() => setParticles([]), 950);
    return () => {
      window.clearTimeout(popTimer);
      window.clearTimeout(clearTimer);
    };
  }, [sendBurstKey]);

  const handleSubmit = (e) => {
    e?.preventDefault();
    if (!draft.trim() || isStreaming) {
      return;
    }
    const text = draft;
    setDraft("");
    onSendMessage(text);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <section className="chat-panel">
      <header className="chat-header">
        <div className="chat-title-group">
          <div className="brand-badge" aria-hidden="true">
            🎀
          </div>
          <div>
            <h1>Weather Risk Intelligence Agent</h1>
            <p className="chat-subtitle">
              US Logistics Resilience Analyst · Deterministic WDRI v1
              {sessionId ? ` · Session ${sessionId.slice(0, 8)}` : ""}
            </p>
          </div>
        </div>

        <div className="chat-header-actions">
          <button
            type="button"
            className="ghost-pink-btn"
            onClick={onOpenMethodology}
          >
            📐 Methodology
          </button>
          <button
            type="button"
            className="soft-pink-btn"
            onClick={onNewChat}
            disabled={isStreaming && messages.length === 0}
          >
            ✨ New Chat
          </button>
        </div>
      </header>

      <div className="chat-scroll-area">
        {loadingHistory && (
          <div className="center-note">Restoring your conversation… 🌸</div>
        )}

        {!loadingHistory && messages.length === 0 && (
          <div className="empty-hero">
            <div className="hero-pill">✨ Deterministic Weather &amp; Hazard Analytics</div>
            <h2>Which hub needs resilience investment first?</h2>
            <p>
              Ask about any of our 20 US distribution hubs across winter, hurricane, flood,
              severe storm, or extreme heat exposure. Every number is computed by verified
              tools—never guessed.
            </p>

            <div className="starter-grid">
              {STARTER_QUESTIONS.map((q) => (
                <button
                  key={q.id}
                  type="button"
                  className="starter-card"
                  disabled={isStreaming}
                  onClick={() => onSendMessage(q.text)}
                >
                  <div className="starter-top">
                    <span>{q.icon}</span>
                    <span className="starter-tag">{q.tag}</span>
                  </div>
                  <div className="starter-text">{q.text}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            onSelectFollowUp={onSendMessage}
            isStreaming={isStreaming}
          />
        ))}

        {isStreaming && (
          <div className="message-row assistant-row streaming-row">
            <div className="assistant-avatar pulse-avatar" aria-hidden="true">
              🌸
            </div>
            <div className="assistant-bubble streaming-bubble">
              <div className="streaming-status">
                <span className="typing-hearts" aria-hidden="true">
                  <span>💖</span>
                  <span>✨</span>
                  <span>💖</span>
                </span>
                <span>{formatStageLabel(statusStage)}</span>
              </div>
              <ToolTrace trace={liveTrace} isLive />
            </div>
          </div>
        )}

        {errorState && (
          <div
            className={`error-banner ${
              errorState.code === "rate_limited" ? "rate-limit-banner" : ""
            }`}
            role="alert"
          >
            <div>
              <strong>
                {errorState.code === "rate_limited"
                  ? "⏳ Rate limit reached: "
                  : "⚠️ Notice: "}
              </strong>
              <span>{errorState.message}</span>
            </div>
            <button
              type="button"
              className="icon-close-btn"
              onClick={onClearError}
              aria-label="Dismiss error"
            >
              ✕
            </button>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <form className="composer-bar" onSubmit={handleSubmit}>
        <div className="composer-input-wrap">
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about Midwest winter risk, compare hubs, or inspect Dallas drivers… ✨"
            disabled={isStreaming}
            maxLength={2000}
          />
          <div className="send-btn-anchor">
            {particles.length > 0 && (
              <div className="send-burst-layer" aria-hidden="true">
                {particles.map((p) => (
                  <span
                    key={p.id}
                    className="send-particle"
                    style={{
                      "--tx": p.tx,
                      "--ty": p.ty,
                      "--rot": p.rot,
                      animationDelay: p.delay,
                    }}
                  >
                    {p.emoji}
                  </span>
                ))}
              </div>
            )}
            <button
              type="submit"
              className={`send-btn ${buttonPopping ? "send-btn-pop" : ""}`}
              disabled={isStreaming || !draft.trim()}
            >
              {isStreaming ? "Thinking…" : "Send 💖"}
            </button>
          </div>
        </div>
        <div className="composer-hint">
          Press <kbd>Enter</kbd> to send · <kbd>Shift + Enter</kbd> for new line · 20 portfolio
          hubs only
        </div>
      </form>
    </section>
  );
}
