import { useCallback, useEffect, useRef, useState } from "react";
import { useVoiceInput } from "../hooks/useVoiceInput.js";
import { useVoiceOutput } from "../hooks/useVoiceOutput.js";
import MessageBubble from "./MessageBubble.jsx";
import ToolTrace from "./ToolTrace.jsx";

const STARTER_QUESTIONS = [
  {
    id: "q1",
    text: "Which hubs in the Midwest are most exposed to winter disruption?",
  },
  {
    id: "q2",
    text: "Compare Miami and Houston in terms of hurricane and flood exposure.",
  },
  {
    id: "q3",
    text: "What percentage of days in Denver last year had snowfall?",
  },
  {
    id: "q4",
    text: "Why is the Dallas hub's weather disruption risk high?",
  },
];

function formatStageLabel(statusStage) {
  if (!statusStage) return "Thinking…";
  switch (statusStage.stage) {
    case "calling_tool":
      return `Running ${statusStage.detail || "tool"}…`;
    case "repairing_output":
      return "Validating output…";
    case "fallback":
      return "Building fallback summary…";
    default:
      return `Analyzing (step ${statusStage.step || 1})…`;
  }
}

export default function ChatWindow({
  sessionId: _sessionId,
  messages,
  loadingHistory,
  isStreaming,
  statusStage,
  liveTrace,
  errorState,
  onClearError,
  sendBurstKey: _sendBurstKey,
  availableModels = [],
  selectedModel = "",
  onSelectModel,
  onSendMessage,
  onNewChat,
  onOpenMethodology,
}) {
  const [draft, setDraft] = useState("");

  const bottomRef = useRef(null);
  const lastSpokenMsgIdRef = useRef(null);
  const wasStreamingRef = useRef(false);

  const handleVoiceTranscript = useCallback((transcript) => {
    setDraft((prev) => (prev.trim() ? `${prev.trim()} ${transcript}` : transcript));
  }, []);

  const voiceInput = useVoiceInput({ onTranscript: handleVoiceTranscript });
  const voiceOutput = useVoiceOutput();

  useEffect(() => {
    const assistantMessages = messages.filter((m) => m.role === "assistant");
    const latest = assistantMessages[assistantMessages.length - 1];

    if (loadingHistory) {
      if (latest) {
        lastSpokenMsgIdRef.current = latest.id;
      }
      return;
    }

    if (wasStreamingRef.current && !isStreaming && latest && latest.id !== lastSpokenMsgIdRef.current) {
      lastSpokenMsgIdRef.current = latest.id;
      if (voiceOutput.autoSpeak && latest.answer?.answer_markdown) {
        voiceOutput.speak(latest.answer.answer_markdown, latest.id);
      }
    } else if (!wasStreamingRef.current && latest && !lastSpokenMsgIdRef.current) {
      lastSpokenMsgIdRef.current = latest.id;
    }

    wasStreamingRef.current = isStreaming;
  }, [messages, isStreaming, loadingHistory, voiceOutput]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, liveTrace, isStreaming]);

  const handleSubmit = (e) => {
    e?.preventDefault();
    if (!draft.trim() || isStreaming) {
      return;
    }
    if (voiceInput.isListening) {
      voiceInput.stopListening();
    }
    const text = draft;
    setDraft("");
    onSendMessage(text);
  };

  const handleNewChatClick = () => {
    voiceInput.stopListening();
    voiceOutput.stop();
    lastSpokenMsgIdRef.current = null;
    onNewChat();
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
          <h1>💖 Weather Risk Agent</h1>
          <span className="version-tag">WDRI v1</span>
        </div>

        <div className="chat-header-actions">
          {availableModels.length > 0 && (
            <label className="model-select-wrap">
              <span className="sr-only">LLM Model</span>
              <select
                className="model-select"
                aria-label="Select LLM model"
                value={selectedModel}
                onChange={(e) => onSelectModel?.(e.target.value)}
                disabled={isStreaming}
                title={
                  availableModels.find((m) => m.id === selectedModel)?.description ||
                  "Choose LLM model"
                }
              >
                {availableModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            className={`toolbar-btn ${voiceOutput.autoSpeak ? "active" : ""}`}
            onClick={voiceOutput.toggleAutoSpeak}
            disabled={!voiceOutput.isSupported}
            aria-pressed={voiceOutput.autoSpeak}
            aria-label={voiceOutput.autoSpeak ? "Auto-Read On" : "Auto-Read Off"}
            title={
              voiceOutput.isSupported
                ? "Automatically read assistant replies aloud"
                : "Speech synthesis is not supported in this browser"
            }
          >
            {voiceOutput.autoSpeak ? "🔊 Auto-Read" : "🔇 Auto-Read"}
          </button>
          <button
            type="button"
            className="toolbar-btn"
            onClick={onOpenMethodology}
          >
            Methodology
          </button>
          <button
            type="button"
            className="primary-compact-btn"
            onClick={handleNewChatClick}
            disabled={isStreaming && messages.length === 0}
          >
            New chat
          </button>
        </div>
      </header>

      <div className="chat-scroll-area">
        <div className="chat-column">
          {loadingHistory && (
            <div className="center-note">Restoring conversation…</div>
          )}

          {!loadingHistory && messages.length === 0 && (
            <div className="empty-hero">
              <h2>💕 Ask about hub weather risk</h2>
              <p>
                Analyze 20 US distribution hubs across winter, hurricane, flood, severe storm, and
                heat exposure.
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
                    {q.text}
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
              voiceSupported={voiceOutput.isSupported}
              isSpeakingThis={voiceOutput.isSpeaking && voiceOutput.speakingMessageId === msg.id}
              onSpeak={voiceOutput.speak}
              onStopSpeaking={voiceOutput.stop}
            />
          ))}

          {isStreaming && (
            <div className="message-row assistant-row streaming-row">
              <div className="assistant-content">
                <div className="streaming-status">
                  <span className="typing-hearts" aria-hidden="true">
                    <span>💗</span>
                    <span>💜</span>
                    <span>💗</span>
                  </span>
                  <span>{formatStageLabel(statusStage)}</span>
                </div>
                {liveTrace?.length > 0 && <ToolTrace trace={liveTrace} isLive />}
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
                    ? "Rate limit reached: "
                    : "Notice: "}
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
      </div>

      <form className="composer-bar" onSubmit={handleSubmit}>
        <div className="composer-inner">
          {voiceInput.error && (
            <div className="voice-error-banner" role="alert">
              <span>{voiceInput.error}</span>
              <button
                type="button"
                className="icon-close-btn"
                onClick={voiceInput.clearError}
                aria-label="Dismiss voice error"
              >
                ✕
              </button>
            </div>
          )}
          <div className="composer-input-wrap">
            <textarea
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                voiceInput.isListening
                  ? voiceInput.interimTranscript || "Listening… speak your question"
                  : "Ask about hub risk, compare cities, or check weather stats…"
              }
              disabled={isStreaming}
              maxLength={2000}
            />
            <button
              type="button"
              className={`voice-mic-btn ${voiceInput.isListening ? "listening" : ""}`}
              onClick={voiceInput.toggleListening}
              disabled={isStreaming || !voiceInput.isSupported}
              aria-label={voiceInput.isListening ? "Stop voice input" : "Start voice input"}
              aria-pressed={voiceInput.isListening}
              title={
                !voiceInput.isSupported
                  ? "Voice input is not supported in this browser"
                  : voiceInput.isListening
                    ? "Stop listening"
                    : "Dictate question with microphone"
              }
            >
              {voiceInput.isListening ? "⏹" : "🎙"}
            </button>
            <button
              type="submit"
              className="send-btn"
              disabled={isStreaming || !draft.trim()}
            >
              {isStreaming ? "Thinking…" : "Send 💖"}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
