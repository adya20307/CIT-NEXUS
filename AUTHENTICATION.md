# Authentication and authorization prerequisite

## Status

CIT Nexus has selected Keycloak, and a local OIDC integration is being prepared.
The provider is not yet configured or running. The former frontend role
selector was presentation state only and was removed; it was never proof of
identity. Approval actions remain disabled until a trusted provider is
configured and the workflow itself is separately approved and implemented.

No issuer, client ID, audience, role claim, department claim, or credentials are
currently configured. Do not add placeholder values, hardcoded users, or
client-supplied roles.

## Provider decision

The planned provider is Keycloak. Local setup and claim configuration are
documented in [KEYCLOAK_SETUP.md](./KEYCLOAK_SETUP.md). Local issuer, audience,
and signing keys are not considered configured until an operator starts
Keycloak and provisions the realm.

## Configuration required before integration

Supply these values through the deployment's secret/configuration mechanism;
do not commit credentials or environment-specific secrets:

| Setting | Requirement |
|---|---|
| OIDC issuer URL | Exact HTTPS issuer identifier; it must match the token's `iss` claim and discovery metadata. |
| Discovery/JWKS source | Retrieved from the trusted issuer's discovery document or explicitly configured from the same trusted provider. |
| Frontend client ID | Public OIDC client registered for the CIT Nexus React application. |
| API audience | Expected audience for FastAPI access tokens; validate `aud` on every authenticated request. |
| Signing algorithms | Explicit allowlist agreed with the provider; reject unsigned tokens and algorithms outside the allowlist. |
| Stable identity | Use the verified `(iss, sub)` pair as the external identity key. Do not use display name or email as the authorization identity. |
| Role source and claim name | Identify either a signed role claim or a server-controlled role mapping. Define exact values for Admin, Department Coordinator, Faculty, and Club Representative. |
| Department source and claim name | Identify a signed department claim or server-controlled department mapping. Define canonical department identifiers and how non-department users are represented. |
| Administrator provisioning | Define a controlled process to bind the first Admin role to a real provider subject. No default or bootstrap password/account may be shipped. |

OIDC client secrets, private keys, signing keys, and provider credentials must
remain in the identity provider or deployment secret store. A browser-based
React client should use Authorization Code with PKCE and must not treat a
role chosen in the UI as an identity claim.

## FastAPI verification requirements

Before enabling protected endpoints, the backend must:

1. Verify the access-token signature using the configured issuer's trusted
   signing keys and the explicit algorithm allowlist.
2. Validate issuer, audience, expiry, not-before, and the token's intended use.
   Reject missing, malformed, expired, or unverifiable tokens.
3. Derive the principal only from verified claims and/or a server-controlled
   mapping keyed by `(iss, sub)`. Never accept a role or department from a
   request body, query parameter, or frontend selector.
4. Fail closed: return `401` for missing or invalid authentication and `403`
   for an authenticated principal lacking the required role or department
   scope.
5. Apply authorization to every protected API operation, regardless of which
   frontend page initiated it.

## Workflow authorization requirements

After identity verification is established and administrator approval is
recorded, workflow implementation must enforce these server-side rules:

- A requester cannot review or approve their own request.
- Coordinators are scoped to their verified department; department-lab
  approvals must not cross department boundaries.
- Club representatives can view and cancel only their own requests.
- Owners cannot directly cancel an approved booking. An approved-booking
  cancellation is a request that only an authorized Admin or Reviewer may
  approve.
- Approval, rejection, and cancellation decisions retain the request history
  and record the authenticated actor, reason where applicable, status, and
  timestamp in the audit history. Requests must not be silently deleted.
- Booking priority must be enforced by the API using the approved priority
  configuration, not just displayed in the UI.

## Enablement gate

Do not implement or enable authenticated approval actions until all of the
following are true:

1. An administrator selects and configures the OIDC provider.
2. The issuer, client ID, API audience, role source, and department source are
   documented with their expected values and claim semantics.
3. The backend's token verification and principal mapping are implemented and
   verified against that provider.
4. Administrator approval for provider integration is recorded.
5. Authentication and authorization tests verify invalid-token rejection,
   role and department boundaries, and protected workflow operations.

Until then, role-based approval behavior is intentionally unavailable. This
document specifies prerequisites only; it does not add authentication,
authorization, database changes, or placeholder configuration.
