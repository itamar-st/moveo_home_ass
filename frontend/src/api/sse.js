import { fetchEventSource } from "@microsoft/fetch-event-source";
import { API_BASE_URL } from "./client.js";

export class StreamHttpError extends Error {
  constructor(status, code, message, details = null) {
    super(message);
    this.name = "StreamHttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/**
 * Stream a single chat turn over POST /api/chat/stream using fetch-event-source.
 */
export async function streamChat({ sessionId, message, model, onEvent, signal }) {
  const body = { message };
  if (sessionId) {
    body.session_id = sessionId;
  }
  if (model) {
    body.model = model;
  }

  await fetchEventSource(`${API_BASE_URL}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal,
    openWhenHidden: true,

    async onopen(response) {
      if (response.ok) {
        return;
      }
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      const errObj = payload?.error;
      throw new StreamHttpError(
        response.status,
        errObj?.code || (response.status === 429 ? "rate_limited" : "http_error"),
        errObj?.message || `Chat stream failed with status ${response.status}`,
        errObj?.details || null
      );
    },

    onmessage(msg) {
      if (!msg.event || msg.event === "ping") {
        return;
      }
      let data = {};
      if (msg.data) {
        try {
          data = JSON.parse(msg.data);
        } catch {
          data = { raw: msg.data };
        }
      }
      onEvent?.({ event: msg.event, data });
    },

    onerror(err) {
      // Throwing stops fetch-event-source's automatic reconnect loop for POST turns.
      throw err;
    },
  });
}
