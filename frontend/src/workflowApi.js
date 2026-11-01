import { apiFetch } from "./auth/keycloakClient.js";

export async function requestJSON(path, options = {}) {
  const response = await apiFetch(path, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The API returned an invalid response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
    throw new Error(detail || `Request failed (HTTP ${response.status}).`);
  }
  return payload;
}
