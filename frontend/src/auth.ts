export type AuthStatus =
  | "local"
  | "loading"
  | "signed_out"
  | "authenticated"
  | "expired"
  | "denied"
  | "configuration_required"
  | "error";

type CognitoConfig = {
  domain: string;
  clientId: string;
  redirectUri: string;
  logoutUri: string;
  scopes: string[];
};

type PendingAuthorization = { state: string; verifier: string; nonce: string };
type AuthListener = (status: AuthStatus, message?: string) => void;

const pendingKey = "messqry.oidc.pending";
const env = import.meta.env;
const config: CognitoConfig | null = (() => {
  const domain = env.VITE_COGNITO_DOMAIN?.trim().replace(/\/$/, "");
  const clientId = env.VITE_COGNITO_CLIENT_ID?.trim();
  const redirectUri = env.VITE_COGNITO_REDIRECT_URI?.trim();
  const logoutUri = env.VITE_COGNITO_LOGOUT_URI?.trim();
  if (!domain || !clientId || !redirectUri || !logoutUri) return null;
  return {
    domain,
    clientId,
    redirectUri,
    logoutUri,
    scopes: (
      env.VITE_COGNITO_SCOPES ??
      "openid email profile messqry/registry.read messqry/registry.write messqry/deployment.read messqry/deployment.write messqry/prediction.invoke"
    )
      .split(/[ ,]+/)
      .map((scope: string) => scope.trim())
      .filter(Boolean),
  };
})();

let status: AuthStatus = "loading";
let message: string | undefined;
let accessToken: string | null = null;
let expiresTimer: number | undefined;
let initialization: Promise<void> | undefined;
const listeners = new Set<AuthListener>();

function publish(next: AuthStatus, detail?: string) {
  status = next;
  message = detail;
  for (const listener of listeners) listener(status, message);
}

function randomValue(bytes = 32): string {
  const data = crypto.getRandomValues(new Uint8Array(bytes));
  return btoa(String.fromCharCode(...data))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function decodeJwtPayload(token: string): Record<string, unknown> {
  const payload = token.split(".")[1];
  if (!payload) throw new Error("The identity response was invalid.");
  const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "="))) as Record<
    string,
    unknown
  >;
}

function cleanCallbackUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("code");
  url.searchParams.delete("state");
  url.searchParams.delete("error");
  url.searchParams.delete("error_description");
  window.history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
}

async function finishAuthorization(): Promise<void> {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  const oauthError = url.searchParams.get("error");
  if (!code && !oauthError) return;

  const pendingValue = window.sessionStorage.getItem(pendingKey);
  window.sessionStorage.removeItem(pendingKey);
  cleanCallbackUrl();
  if (!config || !pendingValue || url.searchParams.get("state") === null) {
    publish("error", "Sign-in could not be verified. Start again from this page.");
    return;
  }

  let pending: PendingAuthorization;
  try {
    pending = JSON.parse(pendingValue) as PendingAuthorization;
  } catch {
    publish("error", "Sign-in could not be verified. Start again from this page.");
    return;
  }
  if (pending.state !== url.searchParams.get("state")) {
    publish("error", "Sign-in could not be verified. Start again from this page.");
    return;
  }
  if (oauthError) {
    publish("signed_out", "Sign-in was not completed. You can try again.");
    return;
  }
  if (!code) {
    publish("error", "Sign-in could not be verified. Start again from this page.");
    return;
  }

  const tokenResponse = await fetch(`${config.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: config.redirectUri,
      code_verifier: pending.verifier,
    }),
  });
  if (!tokenResponse.ok) throw new Error("Cognito rejected the sign-in response. Please try again.");
  const tokens = (await tokenResponse.json()) as {
    access_token?: string;
    id_token?: string;
    token_type?: string;
    expires_in?: number;
  };
  if (
    !tokens.access_token ||
    !tokens.id_token ||
    tokens.token_type?.toLowerCase() !== "bearer" ||
    !Number.isFinite(tokens.expires_in) ||
    (tokens.expires_in ?? 0) <= 0
  ) {
    throw new Error("Cognito returned an incomplete sign-in response.");
  }
  if (decodeJwtPayload(tokens.id_token).nonce !== pending.nonce) {
    throw new Error("Sign-in could not be verified. Start again from this page.");
  }
  // The UI uses tokens only for API requests; identity and scopes are verified by the API.
  accessToken = tokens.access_token;
  expiresTimer = window.setTimeout(() => {
    accessToken = null;
    publish("expired", "Your sign-in session expired. Sign in again to continue.");
  }, Math.max(0, ((tokens.expires_in ?? 0) - 5) * 1000));
  publish("authenticated");
}

export function getAuthStatus(): AuthStatus {
  return status;
}

export function getAuthMessage(): string | undefined {
  return message;
}

export function subscribeAuth(listener: AuthListener): () => void {
  listeners.add(listener);
  listener(status, message);
  return () => listeners.delete(listener);
}

export function initializeAuth(): Promise<void> {
  if (initialization) return initialization;
  initialization = (async () => {
    const hasPartialConfig = [
      env.VITE_COGNITO_DOMAIN,
      env.VITE_COGNITO_CLIENT_ID,
      env.VITE_COGNITO_REDIRECT_URI,
      env.VITE_COGNITO_LOGOUT_URI,
    ].some(Boolean);
    if (!config) {
      publish(
        env.PROD || hasPartialConfig ? "configuration_required" : "local",
        env.PROD || hasPartialConfig
          ? "Configure the Cognito domain, public app client, sign-in callback, and sign-out URL before using this build."
          : undefined,
      );
      return;
    }
    publish("loading");
    try {
      await finishAuthorization();
      if (status === "loading") publish("signed_out");
    } catch (error) {
      publish("error", error instanceof Error ? error.message : "Sign-in could not be completed.");
    }
  })();
  return initialization;
}

export async function beginSignIn(): Promise<void> {
  if (!config) {
    publish("configuration_required", "Cognito sign-in is not configured for this build.");
    return;
  }
  try {
    const verifier = randomValue(48);
    const stateValue = randomValue();
    const nonce = randomValue();
    window.sessionStorage.setItem(pendingKey, JSON.stringify({ state: stateValue, verifier, nonce }));
    const challenge = await challengeFor(verifier);
    const authorization = new URL(`${config.domain}/oauth2/authorize`);
    authorization.search = new URLSearchParams({
      response_type: "code",
      client_id: config.clientId,
      redirect_uri: config.redirectUri,
      scope: config.scopes.join(" "),
      state: stateValue,
      nonce,
      code_challenge: challenge,
      code_challenge_method: "S256",
    }).toString();
    window.location.assign(authorization.toString());
  } catch (cause) {
    publish("error", cause instanceof Error ? cause.message : "Sign-in could not be started.");
    throw cause;
  }
}

export function endSession(): void {
  accessToken = null;
  if (expiresTimer !== undefined) window.clearTimeout(expiresTimer);
  expiresTimer = undefined;
  window.sessionStorage.removeItem(pendingKey);
  if (!config) {
    publish("signed_out");
    return;
  }
  const logout = new URL(`${config.domain}/logout`);
  logout.search = new URLSearchParams({
    client_id: config.clientId,
    logout_uri: config.logoutUri,
  }).toString();
  window.location.assign(logout.toString());
}

function isConfiguredApiTarget(input: RequestInfo | URL): boolean {
  try {
    const requestUrl = input instanceof Request ? input.url : input.toString();
    const target = new URL(requestUrl, window.location.href);
    const configuredRoot = env.VITE_API_BASE_URL?.trim() || window.location.origin;
    const apiOrigin = new URL(configuredRoot, window.location.origin).origin;
    return target.origin === apiOrigin;
  } catch {
    return false;
  }
}

export async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const trustedApiTarget = isConfiguredApiTarget(input);
  if (accessToken && trustedApiTarget) headers.set("Authorization", `Bearer ${accessToken}`);
  const response = await fetch(input, { ...init, headers });
  if (trustedApiTarget && response.status === 401 && accessToken) {
    accessToken = null;
    if (expiresTimer !== undefined) window.clearTimeout(expiresTimer);
    expiresTimer = undefined;
    publish("expired", "Your sign-in session is no longer valid. Sign in again to continue.");
  }
  return response;
}
