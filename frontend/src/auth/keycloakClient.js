import Keycloak from "keycloak-js";

export const API_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const keycloakConfig = {
  url: import.meta.env.VITE_KEYCLOAK_URL,
  realm: import.meta.env.VITE_KEYCLOAK_REALM,
  clientId: import.meta.env.VITE_KEYCLOAK_CLIENT_ID,
};

export const isKeycloakConfigured = Object.values(keycloakConfig).every(
  value => typeof value === "string" && value.trim().length > 0,
);

let keycloakInstance;
let initialization;

export function getKeycloak() {
  if (!isKeycloakConfigured) return null;
  if (!keycloakInstance) keycloakInstance = new Keycloak(keycloakConfig);
  return keycloakInstance;
}

export function initializeKeycloak(keycloak) {
  if (!initialization) {
    initialization = keycloak.init({
      onLoad: "login-required",
      pkceMethod: "S256",
      checkLoginIframe: false,
    }).catch(error => {
      initialization = undefined;
      throw error;
    });
  }
  return initialization;
}

export async function getAccessToken() {
  const keycloak = getKeycloak();
  if (!keycloak?.authenticated) {
    throw new Error("A Keycloak-authenticated session is required.");
  }

  await keycloak.updateToken(30);
  if (!keycloak.token) {
    throw new Error("Keycloak did not provide an access token.");
  }
  return keycloak.token;
}

export async function apiFetch(input, options = {}) {
  const token = await getAccessToken();
  const apiBase = new URL(API_URL);
  const requestUrl = new URL(input, `${apiBase.origin}/`);
  if (requestUrl.origin !== apiBase.origin) {
    throw new Error("Authenticated requests must target the configured API origin.");
  }

  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(requestUrl, { ...options, headers });
}

export async function verifyWithApi(token) {
  const response = await fetch(`${API_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(
      `FastAPI rejected identity verification (HTTP ${response.status}).`,
    );
  }
  const result = await response.json();
  if (!result.authenticated || !result.identity?.subject) {
    throw new Error("FastAPI returned an invalid identity response.");
  }
  return result;
}
