# Keycloak setup for CIT Nexus

## Status and scope

The Keycloak integration verifies login and exposes a protected
`GET /api/auth/me` identity check. It does not add approval, booking, timetable,
or other role-protected workflow endpoints. Those remain disabled until a
separate implementation and authorization review.

The local realm export contains role definitions and client configuration only.
It contains no users, passwords, client secrets, private keys, or Admin account.

## Architecture

- React is an OIDC public client named `cit-nexus-frontend`.
- The React app uses Keycloak's `keycloak-js` adapter with Authorization Code
  Flow and PKCE `S256`. Access and refresh tokens remain in memory.
- FastAPI is an OIDC resource server. It discovers signing keys from the
  configured issuer's OIDC discovery document and JWKS endpoint; it does not
  use a client secret or trust frontend role values.
- Keycloak signs access tokens with `RS256` by default. FastAPI requires an
  explicit asymmetric algorithm allowlist, validates the signature, exact
  issuer, API audience, `exp`, `nbf` when present, and authorized party (`azp`).
- The stable identity key is the verified `(iss, sub)` pair. Display names and
  email addresses are not authorization identifiers.
- Only the authenticated identity endpoint is available in this integration.
  Existing scheduling and booking endpoints remain unchanged and are not made
  permission-sensitive here.

## Local Keycloak

Prerequisites: Docker Compose, Node.js/npm, and the backend virtual environment.
Docker was not available in the inspected development environment, so Keycloak
startup and realm import could not be exercised there.

1. Select a currently supported, security-patched Keycloak image and set its
   exact version/digest in `KEYCLOAK_IMAGE`. The Compose file intentionally
   does not choose an unreviewed server version.
2. Provide `KC_BOOTSTRAP_ADMIN_USERNAME` and
   `KC_BOOTSTRAP_ADMIN_PASSWORD` in the current process environment through a
   local secret manager. Set `KEYCLOAK_IMAGE` to the selected pinned image.
   Do not place credentials in shell history or source control. Compose fails
   if any required setting is missing:

   ```powershell
   docker compose up -d
   ```

3. Open `http://localhost:8080`, sign in to the Keycloak administration
   console using the local bootstrap administrator, and verify that the
   imported realm is `cit-nexus`.
4. Configure the React and FastAPI environment variables below, then start
   FastAPI and Vite. Use `localhost` consistently for Keycloak so the token
   issuer is exactly `http://localhost:8080/realms/cit-nexus`.
5. Create or connect real users only through an administrator-controlled
   identity lifecycle. Assign each user to the appropriate managed role group
   and, when applicable, to an administrator-managed
   `/departments/<CODE>` group.

The bootstrap administrator controls Keycloak infrastructure; it is not a CIT
Nexus application Admin. Do not import real users into the realm export. After
the initial console setup, rotate the bootstrap secret and restrict its use.
The named development volume preserves Keycloak state. Realm imports do not
overwrite an already existing realm on subsequent starts.

## Realm, client, roles, and claims

The realm export is `keycloak/realm-export/CIT-Nexus-realm.json`.

| Item | Configuration |
|---|---|
| Realm | `cit-nexus` |
| React client | `cit-nexus-frontend`, public client, Standard Flow enabled |
| PKCE | `S256`; implicit and direct access grants disabled |
| Local redirect URIs | Explicit `localhost:5173` and `127.0.0.1:5173` development origins only |
| API audience | `cit-nexus-api`, added by the `cit-nexus-api` default client scope |
| Role claim | Keycloak's built-in signed `realm_access.roles` claim |
| Department claim | Signed access-token array `cit_department_groups`, mapped from full Keycloak group paths such as `/departments/CSE` |
| Public registration account type | Required Keycloak user-profile attribute `account_type` with `student` and `admin_request` choices |

The application roles are `ADMIN`, `FACULTY`, and `STUDENT`, matching FastAPI's
authorization allowlist. The realm export provides managed groups for those
roles and assigns no users. Legacy `department_coordinator` and
`club_representative` groups do not grant access to protected APIs. Role
assignment and department group membership must be controlled by Keycloak
administrators; users must not be allowed to edit their own group memberships.

Self-registration asks users to select Student or “Admin (request access).”
This is a profile attribute, not a role assignment. In particular, selecting
the admin request option never grants the `ADMIN` realm role; a Keycloak
administrator must verify the request and explicitly assign the managed admin
group. Student access likewise depends on the appropriate administrator-
controlled role assignment. When applying this to an already-imported realm,
update its User Profile configuration and enable self-registration in the
Keycloak Admin Console; startup realm import does not overwrite existing realm
configuration.

The selected future cancellation-reviewer policy is Admin plus a
same-department Coordinator for departmental lab cancellation requests. This
policy is documentation only; there are no cancellation or approval endpoints.

## Configuration

Set these values in the process environment or deployment configuration.
They have no committed defaults for issuer, audience, or client identity.

| Variable | Local value / purpose |
|---|---|
| `KEYCLOAK_ISSUER_URL` | `http://localhost:8080/realms/cit-nexus` |
| `KEYCLOAK_API_AUDIENCE` | `cit-nexus-api` |
| `KEYCLOAK_FRONTEND_CLIENT_ID` | `cit-nexus-frontend`; checked against token `azp` |
| `KEYCLOAK_ALLOWED_ALGORITHMS` | `RS256`; must be an approved asymmetric signing algorithm |
| `KEYCLOAK_ROLE_CLAIM` | `realm_access.roles` |
| `KEYCLOAK_DEPARTMENT_CLAIM` | `cit_department_groups` |
| `CIT_NEXUS_CORS_ORIGINS` | Comma-separated exact browser origins; local default permits the two Vite origins |
| `VITE_KEYCLOAK_URL` | `http://localhost:8080` |
| `VITE_KEYCLOAK_REALM` | `cit-nexus` |
| `VITE_KEYCLOAK_CLIENT_ID` | `cit-nexus-frontend` |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` |
| `KEYCLOAK_IMAGE` | Exact supported Keycloak image reference for local Compose |
| `KC_BOOTSTRAP_ADMIN_USERNAME` | Local-only Keycloak infrastructure bootstrap account name |
| `KC_BOOTSTRAP_ADMIN_PASSWORD` | Local-only bootstrap secret; never expose to React or commit |

Only the Keycloak bootstrap credentials are secrets. The Vite variables are
public client configuration and must never include a client secret. Production
must set an HTTPS issuer, API audience, exact HTTPS CORS origins, and a
currently supported Keycloak release behind a TLS-terminating trusted proxy.
Production secrets belong in a secret manager, not `.env` files in source
control. The local `start-dev` configuration must not be used in production.

After Keycloak is running and the bootstrap account values have been injected
by the local secret manager, set the non-secret application settings in the
PowerShell process that launches each service:

```powershell
$env:KEYCLOAK_ISSUER_URL = "http://localhost:8080/realms/cit-nexus"
$env:KEYCLOAK_API_AUDIENCE = "cit-nexus-api"
$env:KEYCLOAK_FRONTEND_CLIENT_ID = "cit-nexus-frontend"
$env:KEYCLOAK_ALLOWED_ALGORITHMS = "RS256"
$env:KEYCLOAK_ROLE_CLAIM = "realm_access.roles"
$env:KEYCLOAK_DEPARTMENT_CLAIM = "cit_department_groups"
$env:CIT_NEXUS_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
$env:VITE_KEYCLOAK_URL = "http://localhost:8080"
$env:VITE_KEYCLOAK_REALM = "cit-nexus"
$env:VITE_KEYCLOAK_CLIENT_ID = "cit-nexus-frontend"
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
```

Install backend dependencies from the project root:

```powershell
python -m pip install -r backend\requirements.txt
```

Launch Uvicorn from the `backend` directory and Vite from the `frontend`
directory in processes that received the corresponding environment variables.
Do not copy the Keycloak bootstrap credentials into either application process.

## React login behavior

`keycloak-js` initializes with `login-required` and PKCE `S256`. When the required
public configuration is absent, the sign-in button stays disabled. On login,
the React app sends the access token as a Bearer token to `/api/auth/me` and
shows the identity only after FastAPI validates it. The ID token is not sent to
the API, and users cannot select a role.

FastAPI validates the access token and returns the application roles from the
signed `realm_access.roles` claim. The React app routes `ADMIN` to `/admin`,
`FACULTY` to `/faculty`, and `STUDENT` to `/student`, with Admin taking
precedence when multiple application roles are assigned. Accounts without a
recognized role see Access Denied. Protected API endpoints independently
verify the token and enforce role requirements; frontend routing is not
authorization.

The JavaScript adapter holds tokens in memory; do not copy them into
`localStorage`, `sessionStorage`, URLs, logs, or analytics. Refresh the access
token through the adapter before authenticated API calls.

## CIT Nexus Keycloak login theme

The branded sign-in page is a Keycloak theme, so the existing Authorization
Code + PKCE flow, login form action, client, and redirect URIs remain owned by
Keycloak. The isometric campus artwork is stored at
`frontend/public/images/cit-nexus-campus.svg` and mounted into the Keycloak
theme's image resources by both Compose configurations.

After starting or recreating Keycloak so the theme bind mounts are applied,
open the Keycloak Admin Console, select the application realm, and choose
**Realm settings → Themes → Login theme → cit-nexus**, then save. This step is
required for realms that already exist: Keycloak's `--import-realm` does not
overwrite an existing realm. The realm export sets `loginTheme` for a new
realm import. The theme only changes login-page presentation; it does not
alter authentication behavior.

## FastAPI verification and fail-closed behavior

`backend/app/auth.py` loads and validates OIDC discovery metadata against the
configured issuer, requires the JWKS endpoint to share the issuer origin, and
uses PyJWT's JWKS client with a bounded timeout and key-set cache. An unknown
key ID causes the client to refresh the key set. Tokens must have a valid
signature, configured algorithm, exact issuer, API audience, `exp`, non-empty
`sub`, and the configured frontend `azp`. PyJWT validates `nbf` when present;
30 seconds of clock skew is allowed.

The API rejects missing, malformed, expired, wrong-issuer, wrong-audience,
wrong-client, disallowed-algorithm, and unverifiable tokens with `401`. A
missing/misconfigured issuer or unavailable discovery/JWKS service returns
`503`, not an unauthenticated success. Identity and authorization claims are
derived only from the verified signed token. No endpoint accepts role or
department values supplied by the frontend.

An authenticated user with no recognized CIT Nexus role may receive an empty
role list from `/api/auth/me`; future protected operations must independently
enforce role and department scope and return `403` when access is not allowed.

## Secure initial application Admin provisioning

1. An authorized IAM operator accesses the Keycloak administration console
   with the infrastructure bootstrap account.
2. The operator creates or federates the real institutional identity and
   verifies the account with the appropriate administrator.
3. The operator assigns the account to `cit-nexus-admins`; the role is never
   inferred from email, username, or a request parameter.
4. A second authorized operator reviews the role assignment through the
   institution's IAM change-control process. Record the real Keycloak subject
   in the institutional access-provisioning record.
5. Rotate/restrict the bootstrap credential. Do not ship a bootstrap
   application account, sample password, or seeded Admin identity.

## Security risks and mitigations

- **Over-broad redirects/origins:** only explicit local development origins are
  configured; production origins must be exact HTTPS URLs.
- **Role/department escalation:** only managed groups issue signed claims; do
  not permit self-service group membership changes; the API whitelists roles
  and accepts only `/departments/<CODE>` group paths.
- **Token substitution:** validate asymmetric signature, issuer, audience,
  authorized party, lifetime, and signing-key ID; do not accept ID tokens.
- **JWKS availability/rotation:** refresh cached keys on key-ID misses, use
  bounded network timeouts, and fail closed when issuer metadata or keys are
  unavailable.
- **Token theft:** keep tokens in memory, use short-lived access tokens, avoid
  browser storage/logging, and use HTTPS outside loopback development.
- **Bootstrap account exposure:** require operator-supplied local secrets,
  ignore `.env` files, keep Keycloak bound to loopback in local Compose, and
  use a secret manager plus a production database and TLS in deployment.
- **Confused-deputy / role-only UI:** the browser may display identity but
  cannot authorize API operations; every future protected FastAPI route must
  use verified-principal and permission dependencies.

## Verification plan

1. Validate realm JSON and Compose configuration, then start Keycloak on a
   machine with Docker and confirm realm/client import and discovery metadata.
2. Confirm Authorization Code + PKCE `S256`, strict redirect matching, role
   claims, `cit_department_groups`, and `cit-nexus-api` audience on a real login.
3. Run backend unit tests for valid tokens and invalid signatures, issuer,
   audience, authorized party, algorithm, expiration, future `nbf`, and missing
   configuration.
4. Confirm `/api/auth/me` returns `401` without a valid token and `503` when
   identity infrastructure is not configured or available.
5. Run the existing React lint/build/tests and backend examination scheduler
   regression tests. No OR-Tools objective or booking-engine change is
   included.
6. Before production, add deployment-specific TLS/proxy/CORS validation,
   provider key-rotation tests, and operational review of Admin provisioning.
