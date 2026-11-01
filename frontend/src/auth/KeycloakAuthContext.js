import { createContext, useContext } from "react";

export const AuthContext = createContext(null);

export function useKeycloakAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error(
      "useKeycloakAuth must be used within KeycloakAuthProvider.",
    );
  }
  return context;
}
