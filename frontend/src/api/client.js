const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    const err = new Error(
      payload?.error?.message || `Request failed with status ${response.status}`
    );
    err.status = response.status;
    err.code = payload?.error?.code || "http_error";
    err.details = payload?.error?.details || null;
    throw err;
  }

  return response.json();
}

export function fetchHealth() {
  return requestJson("/api/health");
}

export function fetchHubs(region = "") {
  const qs = region ? `?region=${encodeURIComponent(region)}` : "";
  return requestJson(`/api/hubs${qs}`);
}

export function fetchHubDetail(code) {
  return requestJson(`/api/hubs/${encodeURIComponent(code)}`);
}

export function fetchRankings({
  hazard = "overall",
  region = "",
  scoreKind = "structural",
  limit = 20,
} = {}) {
  const params = new URLSearchParams({
    hazard,
    score_kind: scoreKind,
    limit: String(limit),
  });
  if (region) {
    params.set("region", region);
  }
  return requestJson(`/api/rankings?${params.toString()}`);
}

export function fetchScoringMeta() {
  return requestJson("/api/meta/scoring");
}

export function createChatSession() {
  return requestJson("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function fetchSessionMessages(sessionId) {
  return requestJson(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`);
}

export async function fetchAlertsSafe(limit = 50) {
  try {
    return await requestJson(`/api/alerts?limit=${limit}`);
  } catch (err) {
    if (err.status === 404) {
      return [];
    }
    throw err;
  }
}

export { API_BASE_URL };
