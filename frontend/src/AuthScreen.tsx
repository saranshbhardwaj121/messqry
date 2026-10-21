import { useState } from "react";
import { CircleAlert, KeyRound, LockKeyhole, LogIn, ShieldCheck } from "lucide-react";
import { beginSignIn, endSession, type AuthStatus } from "./auth";

export function AuthScreen({ status, message }: { status: AuthStatus; message?: string }) {
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const configuration = status === "configuration_required";
  const denied = status === "denied";
  const expired = status === "expired";
  const error = status === "error";

  const title = configuration
    ? "Sign-in setup required"
    : denied
      ? "Workspace access denied"
      : expired
        ? "Your session expired"
        : error
          ? "Sign-in could not be completed"
          : "Sign in to MessQry AI";
  const detail = configuration
    ? message
    : denied
      ? message ?? "Your account does not have the required workspace permission."
      : expired
        ? message ?? "Sign in again to continue using the workspace."
        : error
          ? message ?? "Try signing in again."
          : "Use your organization’s Cognito sign-in to open the model operations workspace.";

  const signIn = async () => {
    setBusy(true);
    setActionError(undefined);
    try {
      await beginSignIn();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Sign-in could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand"><span className="brand-mark">M</span><strong>MessQry<span>AI</span></strong></div>
        <div className={`auth-icon ${denied || error ? "auth-icon-warning" : ""}`}>
          {denied || error ? <CircleAlert size={22} /> : expired ? <LockKeyhole size={22} /> : <ShieldCheck size={22} />}
        </div>
        <div className="eyebrow">SECURE WORKSPACE</div>
        <h1 id="auth-title">{title}</h1>
        <p className="auth-detail" role={denied || expired || error || actionError ? "alert" : undefined}>{actionError ?? detail}</p>

        {!configuration && (
          <div className="auth-actions">
            {denied ? (
              <button className="button-primary" disabled={busy} onClick={endSession}>
                <LogIn size={16} /> Sign in with another account
              </button>
            ) : (
              <button className="button-primary" disabled={busy} onClick={() => void signIn()}>
                <LogIn size={16} /> {busy ? "Opening sign-in…" : expired ? "Sign in again" : "Continue with Cognito"}
              </button>
            )}
          </div>
        )}

        <aside className="credential-guidance">
          <KeyRound size={17} aria-hidden="true" />
          <div><strong>Using a service credential?</strong><p>Keep machine credentials in a trusted server or secret manager. Do not paste them into this browser.</p></div>
        </aside>
        <footer>Requests use scoped access. The API enforces permissions for every operation.</footer>
      </section>
    </main>
  );
}
