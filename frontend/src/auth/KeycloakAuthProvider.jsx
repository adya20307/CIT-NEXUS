import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { AuthContext } from "./KeycloakAuthContext.js";
import {
  getAccessToken,
  getKeycloak,
  initializeKeycloak,
  isKeycloakConfigured,
  verifyWithApi,
} from "./keycloakClient.js";
import { getDashboardPath } from "./roles.js";

export function KeycloakAuthProvider({ children }) {
  const [status, setStatus] = useState(
    isKeycloakConfigured ? "loading" : "unconfigured",
  );
  const [identity, setIdentity] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const keycloak = getKeycloak();
    if (!keycloak) return undefined;

    let active = true;
    async function initialize() {
      try {
        const authenticated = await initializeKeycloak(keycloak);
        if (!active) return;
        if (!authenticated) {
          await keycloak.login({ redirectUri: window.location.href });
          return;
        }

        setStatus("verifying");
        const token = await getAccessToken();
        const result = await verifyWithApi(token);
        if (active) {
          setIdentity(result);
          setStatus(
            getDashboardPath(result.roles) ? "authenticated" : "access-denied",
          );
          setError("");
        }
      } catch {
        if (active) {
          setIdentity(null);
          setStatus("error");
          setError(
            "Keycloak sign-in or FastAPI token verification failed. Check the configured issuer, client, audience, and API settings.",
          );
        }
      }
    }

    initialize();
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async () => {
    const keycloak = getKeycloak();
    if (!keycloak) {
      setError("Keycloak sign-in is not configured.");
      setStatus("error");
      return;
    }
    try {
      await keycloak.login({ redirectUri: window.location.href });
    } catch {
      setError("Could not start Keycloak sign-in.");
      setStatus("error");
    }
  }, []);

  const logout = useCallback(async () => {
    const keycloak = getKeycloak();
    if (!keycloak) return;
    setIdentity(null);
    setStatus("anonymous");
    try {
      await keycloak.logout({ redirectUri: window.location.origin });
    } catch {
      setError("Could not complete Keycloak sign-out.");
      setStatus("error");
    }
  }, []);

  const value = useMemo(() => ({
    status,
    identity,
    error,
    configured: isKeycloakConfigured,
    login,
    logout,
  }), [status, identity, error, login, logout]);

  return (
    <AuthContext.Provider value={value}>
      {status === "authenticated" ? children : (
        <main className="auth-loading" aria-live="polite">
          <h1>
            {status === "access-denied"
              ? "Access Denied"
              : status === "error"
                ? "CIT Nexus sign-in unavailable"
                : status === "unconfigured"
                  ? "Keycloak is not configured"
                  : "Signing in to CIT Nexus"}
          </h1>
          <p>
            {status === "access-denied"
              ? "Your account does not have a recognized CIT Nexus application role. Contact a Keycloak administrator."
              : error || (status === "unconfigured"
                ? "Set the VITE_KEYCLOAK_URL, VITE_KEYCLOAK_REALM, and VITE_KEYCLOAK_CLIENT_ID environment variables."
                : "Verifying your Keycloak session and access token…")}
          </p>
          {status === "access-denied" ? (
            <button type="button" onClick={logout}>Sign out</button>
          ) : (status === "error" || status === "unconfigured") && (
            <button type="button" onClick={login} disabled={!isKeycloakConfigured}>
              Try sign-in again
            </button>
          )}
        </main>
      )}
    </AuthContext.Provider>
  );
}
