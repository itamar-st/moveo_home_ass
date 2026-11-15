import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSessionMessages } from "../api/client.js";
import { StreamHttpError, streamChat } from "../api/sse.js";

const SESSION_STORAGE_KEY = "wria_session_id";

function hydrateMessagesFromHistory(rawMessages) {
  const uiMessages = [];
  let pendingTools = [];

  for (const msg of rawMessages || []) {
    if (msg.role === "user") {
      pendingTools = [];
      uiMessages.push({
        id: `msg-${msg.id}`,
        role: "user",
        text: msg.content,
        createdAt: msg.created_at,
      });
    } else if (msg.role === "tool") {
      if (msg.payload) {
        pendingTools.push(msg.payload);
      }
    } else if (msg.role === "assistant") {
      const payload = msg.payload || {};
      const answer = payload.final_answer || {
        answer_markdown: msg.content,
        intent: "general",
        hubs: [],
        key_points: [],
        reasoning: [],
        assumptions: [],
        confidence: "medium",
        confidence_rationale: "",
        follow_ups: [],
      };
      const toolTrace = Array.isArray(payload.tool_trace)
        ? payload.tool_trace
        : pendingTools;
      pendingTools = [];
      uiMessages.push({
        id: `msg-${msg.id}`,
        role: "assistant",
        answer,
        artifacts: payload.artifacts || [],
        toolTrace,
        model: payload.model || "",
        latencyMs: payload.latency_ms ?? null,
        warnings: payload.warnings || [],
        createdAt: msg.created_at,
      });
    }
  }

  return uiMessages;
}

export function useChatStream() {
  const [sessionId, setSessionId] = useState(() => {
    try {
      return window.localStorage.getItem(SESSION_STORAGE_KEY) || null;
    } catch {
      return null;
    }
  });
  const [messages, setMessages] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const [isStreaming, setIsStreaming] = useState(false);
  const [statusStage, setStatusStage] = useState(null);
  const [liveTrace, setLiveTrace] = useState([]);
  const [errorState, setErrorState] = useState(null);
  const [sendBurstKey, setSendBurstKey] = useState(0);

  const abortRef = useRef(null);

  const persistSessionId = useCallback((id) => {
    setSessionId(id);
    try {
      if (id) {
        window.localStorage.setItem(SESSION_STORAGE_KEY, id);
      } else {
        window.localStorage.removeItem(SESSION_STORAGE_KEY);
      }
    } catch {
      // Ignore storage errors in private browsing.
    }
  }, []);

  useEffect(() => {
    if (!sessionId) {
      return;
    }
    let active = true;
    setLoadingHistory(true);
    fetchSessionMessages(sessionId)
      .then((raw) => {
        if (active) {
          setMessages(hydrateMessagesFromHistory(raw));
        }
      })
      .catch((err) => {
        if (!active) return;
        if (err.status === 404) {
          persistSessionId(null);
          setMessages([]);
        }
      })
      .finally(() => {
        if (active) {
          setLoadingHistory(false);
        }
      });

    return () => {
      active = false;
    };
  }, [sessionId, persistSessionId]);

  const newChat = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    persistSessionId(null);
    setMessages([]);
    setLiveTrace([]);
    setStatusStage(null);
    setErrorState(null);
    setIsStreaming(false);
  }, [persistSessionId]);

  const sendMessage = useCallback(
    async (rawText) => {
      const text = rawText.trim();
      if (!text || isStreaming) {
        return;
      }

      setSendBurstKey((k) => k + 1);
      setErrorState(null);
      setIsStreaming(true);
      setStatusStage({ stage: "thinking", step: 1, detail: "Starting analyst turn..." });
      setLiveTrace([]);

      const userTempId = `user-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id: userTempId,
          role: "user",
          text,
          createdAt: new Date().toISOString(),
          justSent: true,
        },
      ]);

      const runTurn = async (activeSessionId) => {
        const controller = new AbortController();
        abortRef.current = controller;

        let localTrace = [];
        let receivedFinal = false;

        await streamChat({
          sessionId: activeSessionId,
          message: text,
          signal: controller.signal,
          onEvent: ({ event, data }) => {
            if (event === "session" && data.session_id) {
              persistSessionId(data.session_id);
            } else if (event === "status") {
              setStatusStage(data);
            } else if (event === "tool_call") {
              const item = {
                step: data.step,
                call_id: data.call_id,
                name: data.name,
                arguments: data.arguments || {},
                rationale: data.rationale || "",
                ok: null,
                summary: "Running...",
                data: {},
                caveats: [],
                duration_ms: 0,
              };
              localTrace = [...localTrace, item];
              setLiveTrace(localTrace);
            } else if (event === "tool_result") {
              localTrace = localTrace.map((t) =>
                t.call_id === data.call_id
                  ? {
                      ...t,
                      ok: Boolean(data.ok),
                      summary: data.summary || "",
                      data: data.data || {},
                      caveats: data.caveats || [],
                      duration_ms: data.duration_ms ?? 0,
                    }
                  : t
              );
              setLiveTrace(localTrace);
            } else if (event === "error") {
              setErrorState({
                code: data.code || "stream_error",
                message: data.message || "The language model encountered an issue.",
                retryable: Boolean(data.retryable),
              });
            } else if (event === "final") {
              receivedFinal = true;
              const assistantMsg = {
                id: `assistant-${data.message_id || Date.now()}`,
                role: "assistant",
                answer: data.answer,
                artifacts: data.artifacts || [],
                toolTrace: localTrace,
                model: data.model || "",
                latencyMs: data.latency_ms ?? null,
                warnings: data.warnings || [],
                createdAt: new Date().toISOString(),
              };
              setMessages((prev) => [...prev, assistantMsg]);
              setLiveTrace([]);
              setStatusStage(null);
            } else if (event === "done") {
              setIsStreaming(false);
              setStatusStage(null);
            }
          },
        });

        if (!receivedFinal) {
          setStatusStage(null);
        }
      };

      try {
        try {
          await runTurn(sessionId);
        } catch (err) {
          if (err instanceof StreamHttpError && err.status === 404 && sessionId) {
            // Stale localStorage session: clear and retry once with a fresh session.
            persistSessionId(null);
            await runTurn(null);
          } else {
            throw err;
          }
        }
      } catch (err) {
        if (err?.name !== "AbortError") {
          setErrorState({
            code: err?.code || "network_error",
            message: err?.message || "Failed to connect to the chat stream.",
            retryable: err?.status === 429 || err?.status >= 500 || !err?.status,
            details: err?.details || null,
          });
        }
      } finally {
        abortRef.current = null;
        setIsStreaming(false);
        setStatusStage(null);
      }
    },
    [isStreaming, sessionId, persistSessionId]
  );

  return {
    sessionId,
    messages,
    loadingHistory,
    isStreaming,
    statusStage,
    liveTrace,
    errorState,
    clearError: () => setErrorState(null),
    sendBurstKey,
    sendMessage,
    newChat,
  };
}
