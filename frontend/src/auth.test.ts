import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.sessionStorage.clear();
  window.history.replaceState({}, document.title, "/");
  vi.resetModules();
});

function idTokenWithNonce(nonce: string) {
  const payload = btoa(JSON.stringify({ nonce })).replace(/=/g, "");
  return `header.${payload}.signature`;
}

describe("OIDC browser session", () => {
  it("checks callback state and nonce, exchanges PKCE code, and sends the access token to the API", async () => {
    vi.stubEnv("VITE_COGNITO_DOMAIN", "https://messqry.auth.us-east-1.amazoncognito.com");
    vi.stubEnv("VITE_COGNITO_CLIENT_ID", "public-client-id");
    vi.stubEnv("VITE_COGNITO_REDIRECT_URI", "http://localhost/");
    vi.stubEnv("VITE_COGNITO_LOGOUT_URI", "http://localhost/");
    window.sessionStorage.setItem(
      "messqry.oidc.pending",
      JSON.stringify({ state: "expected-state", verifier: "pkce-verifier", nonce: "expected-nonce" }),
    );
    window.history.replaceState({}, document.title, "/?code=authorization-code&state=expected-state");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          access_token: "verified-by-api-access-token",
          id_token: idTokenWithNonce("expected-nonce"),
          token_type: "Bearer",
          expires_in: 3600,
        }),
      })
      .mockResolvedValueOnce({ ok: true, status: 200 })
      .mockResolvedValueOnce({ ok: false, status: 403 })
      .mockResolvedValueOnce({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);

    const auth = await import("./auth");
    await auth.initializeAuth();
    const protectedResponse = await auth.authorizedFetch("/api/v1/models");

    expect(auth.getAuthStatus()).toBe("authenticated");
    expect(protectedResponse.status).toBe(200);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://messqry.auth.us-east-1.amazoncognito.com/oauth2/token",
    );
    expect(String(fetchMock.mock.calls[0][1]?.body)).toContain("code_verifier=pkce-verifier");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Authorization")).toBe(
      "Bearer verified-by-api-access-token",
    );
    await auth.authorizedFetch("https://untrusted.example/collect");
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).has("Authorization")).toBe(false);
    expect(auth.getAuthStatus()).toBe("authenticated");
    await auth.authorizedFetch("/api/v1/models");
    expect(auth.getAuthStatus()).toBe("expired");
    expect(window.location.search).toBe("");
  });

  it("keeps the session authenticated when one API operation is forbidden", async () => {
    vi.stubEnv("VITE_COGNITO_DOMAIN", "https://messqry.auth.us-east-1.amazoncognito.com");
    vi.stubEnv("VITE_COGNITO_CLIENT_ID", "public-client-id");
    vi.stubEnv("VITE_COGNITO_REDIRECT_URI", "http://localhost/");
    vi.stubEnv("VITE_COGNITO_LOGOUT_URI", "http://localhost/");
    window.sessionStorage.setItem(
      "messqry.oidc.pending",
      JSON.stringify({ state: "s", verifier: "v", nonce: "n" }),
    );
    window.history.replaceState({}, document.title, "/?code=c&state=s");
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            access_token: "access",
            id_token: idTokenWithNonce("n"),
            token_type: "Bearer",
            expires_in: 3600,
          }),
        })
        .mockResolvedValueOnce({ ok: false, status: 403 }),
    );

    const auth = await import("./auth");
    await auth.initializeAuth();
    const response = await auth.authorizedFetch("/api/v1/models");

    expect(response.status).toBe(403);
    expect(auth.getAuthStatus()).toBe("authenticated");
    expect(auth.getAuthMessage()).toBeUndefined();
  });

  it("rejects a callback whose OAuth state does not match the stored request", async () => {
    vi.stubEnv("VITE_COGNITO_DOMAIN", "https://messqry.auth.us-east-1.amazoncognito.com");
    vi.stubEnv("VITE_COGNITO_CLIENT_ID", "public-client-id");
    vi.stubEnv("VITE_COGNITO_REDIRECT_URI", "http://localhost/");
    vi.stubEnv("VITE_COGNITO_LOGOUT_URI", "http://localhost/");
    window.sessionStorage.setItem(
      "messqry.oidc.pending",
      JSON.stringify({ state: "correct-state", verifier: "v", nonce: "n" }),
    );
    window.history.replaceState({}, document.title, "/?code=c&state=wrong-state");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const auth = await import("./auth");
    await auth.initializeAuth();

    expect(auth.getAuthStatus()).toBe("error");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });
});
