import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ArrowRightLeft, Clock3, Rocket, RotateCcw } from "lucide-react";
import {
  loadDeploymentHistory,
  loadDeployments,
  normalizeApiTimestamp,
  promoteDeployment,
  rollbackDeployment,
} from "./api";
import type { Deployment, DeploymentEvent, Model } from "./types";

const environments = ["staging", "production"];

function dateLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(normalizeApiTimestamp(value)),
  );
}

export function DeploymentsPage({
  models,
  loading,
  loadError,
  onRefresh,
}: {
  models: Model[];
  loading: boolean;
  loadError: boolean;
  onRefresh: () => void;
}) {
  const [modelId, setModelId] = useState("");
  const [environment, setEnvironment] = useState("staging");
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [history, setHistory] = useState<DeploymentEvent[]>([]);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [versionId, setVersionId] = useState("");
  const [reason, setReason] = useState("");
  const [confirmAction, setConfirmAction] = useState<"promote" | "rollback" | null>(null);
  const [busy, setBusy] = useState(false);

  const selectedModel = models.find((model) => model.id === modelId) ?? models[0] ?? null;
  const versions = useMemo(
    () => [...(selectedModel?.versions ?? [])].sort((left, right) => right.version - left.version),
    [selectedModel],
  );
  const deployment = deployments.find((item) => item.environment === environment) ?? null;
  const selectedVersion = versions.find((version) => version.id === versionId) ?? versions[0] ?? null;

  useEffect(() => {
    if (selectedModel && modelId !== selectedModel.id) setModelId(selectedModel.id);
  }, [modelId, selectedModel]);

  useEffect(() => {
    if (!selectedModel) {
      setDeployments([]);
      setHistory([]);
      return;
    }
    let active = true;
    setLoadingDetails(true);
    setError("");
    Promise.all([
      loadDeployments(selectedModel.id),
      loadDeploymentHistory(selectedModel.id, environment),
    ])
      .then(([items, events]) => {
        if (!active) return;
        setDeployments(items);
        setHistory(events);
        const current = items.find((item) => item.environment === environment);
        const defaultVersion = current?.model_version.id ?? selectedModel.versions?.[0]?.id ?? "";
        setVersionId((previous) =>
          selectedModel.versions?.some((version) => version.id === previous)
            ? previous
            : defaultVersion,
        );
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Deployment data could not be loaded.");
      })
      .finally(() => {
        if (active) setLoadingDetails(false);
      });
    return () => {
      active = false;
    };
  }, [selectedModel, environment, notice]);

  async function applyChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedModel || !selectedVersion || !confirmAction) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const change = confirmAction === "rollback" ? rollbackDeployment : promoteDeployment;
      await change(selectedModel.id, environment, selectedVersion.version, reason.trim());
      setNotice(
        confirmAction === "rollback"
          ? `Rolled ${selectedModel.name} back to v${selectedVersion.version} in ${environment}.`
          : `Selected ${selectedModel.name} v${selectedVersion.version} for ${environment}.`,
      );
      setConfirmAction(null);
      setReason("");
      onRefresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Deployment change failed.");
    } finally {
      setBusy(false);
    }
  }

  const changingExisting = Boolean(deployment && selectedVersion && deployment.model_version.id !== selectedVersion.id);

  return (
    <section className="registry-page deployments-page" aria-labelledby="deployments-title">
      <header className="registry-page-header">
        <div>
          <div className="eyebrow">DEPLOYMENT CONTROL / VERSION SELECTIONS</div>
          <h1 id="deployments-title">Deployments</h1>
          <p>Choose a registered version for an environment and review its promotion and rollback history.</p>
        </div>
      </header>

      {notice && <div className="registry-notice" role="status">{notice}</div>}
      {error && <div className="registry-error" role="alert">{error}</div>}

      {loading || loadingDetails ? (
        <div className="registry-state panel" role="status"><span className="spinner" /> Loading deployment state…</div>
      ) : loadError ? (
        <div className="registry-state panel" role="alert"><strong>Workspace data could not be loaded.</strong><button className="button-secondary" onClick={onRefresh}>Retry</button></div>
      ) : models.length === 0 ? (
        <div className="registry-state panel"><Rocket size={25} aria-hidden="true" /><strong>Register a model before deploying</strong><p>Deployment targets are registered immutable model versions.</p></div>
      ) : (
        <div className="deployment-workspace">
          <section className="panel deployment-selection" aria-label="Deployment target">
            <div className="panel-heading"><div><div className="eyebrow">TARGET</div><h2>Environment selection</h2></div><Rocket size={17} aria-hidden="true" /></div>
            <div className="deployment-fields">
              <label>Model
                <select value={selectedModel.id} onChange={(event) => { setModelId(event.target.value); setConfirmAction(null); }}>
                  {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
                </select>
              </label>
              <label>Environment
                <select value={environment} onChange={(event) => { setEnvironment(event.target.value); setConfirmAction(null); }}>
                  {environments.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
            </div>
            <div className="deployment-current">
              <span className="deployment-icon"><Rocket size={17} aria-hidden="true" /></span>
              <div><span className="eyebrow">CURRENT VERSION / {environment.toUpperCase()}</span><strong>{deployment ? `v${deployment.model_version.version}` : "No version selected"}</strong><small>{deployment ? `Updated ${dateLabel(deployment.updated_at)}` : "This environment has no deployment record."}</small></div>
              <span className={`status-pill ${deployment ? "success" : "neutral"}`}><i />{deployment ? "SELECTED" : "EMPTY"}</span>
            </div>
            {!versions.length ? (
              <div className="version-empty" role="status">
                <strong>No deployable version is available for {selectedModel.name}.</strong>
                <span>Upload a validated package from the Models page, or select another model above.</span>
              </div>
            ) : <form className="deployment-change" onSubmit={applyChange}>
              <label>Registered version
                <select value={selectedVersion?.id ?? ""} onChange={(event) => { setVersionId(event.target.value); setConfirmAction(null); }}>
                  {versions.map((version) => <option key={version.id} value={version.id}>v{version.version} · {version.package_format ?? "legacy"} · {version.validation_status ?? "unverified"}</option>)}
                </select>
              </label>
              <label>Change reason <span className="form-optional">OPTIONAL</span>
                <input maxLength={1000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="e.g. approved release" />
              </label>
              {confirmAction ? (
                <div className="deployment-confirm" role="group" aria-label="Confirm deployment change">
                  <p>Confirm {confirmAction} of <strong>{selectedModel.name} v{selectedVersion?.version}</strong> in <strong>{environment}</strong>?</p>
                  <button className="button-secondary" type="button" disabled={busy} onClick={() => setConfirmAction(null)}>Cancel</button>
                  <button className="button-primary" type="submit" disabled={busy}>{busy ? "Saving…" : `Confirm ${confirmAction}`}</button>
                </div>
              ) : (
                <div className="form-actions">
                  {deployment && changingExisting && <button className="button-secondary" type="button" onClick={() => setConfirmAction("rollback")}><RotateCcw size={14} aria-hidden="true" /> Roll back to v{selectedVersion?.version}</button>}
                  <button className="button-primary" type="button" disabled={!selectedVersion || deployment?.model_version.id === selectedVersion.id} onClick={() => setConfirmAction("promote")}><ArrowRightLeft size={14} aria-hidden="true" /> {deployment ? "Promote version" : "Deploy version"}</button>
                </div>
              )}
            </form>}
          </section>

          <section className="panel deployment-history" aria-labelledby="deployment-history-title">
            <div className="panel-heading"><div><div className="eyebrow">AUDIT HISTORY</div><h2 id="deployment-history-title">{selectedModel.name} · {environment}</h2></div><span className="activity-count">{history.length}</span></div>
            {history.length ? (
              <ol className="deployment-events">
                {history.map((event) => {
                  const target = selectedModel.versions?.find((version) => version.id === event.to_model_version_id);
                  const source = selectedModel.versions?.find((version) => version.id === event.from_model_version_id);
                  return <li key={event.id}>
                    <span className="deployment-event-icon"><Clock3 size={15} aria-hidden="true" /></span>
                    <div className="deployment-event-copy"><strong>{event.action === "deploy" ? "Initial deployment" : event.action === "rollback" ? "Rollback" : "Promotion"}</strong><span>{source ? `v${source.version} → ` : ""}v{target?.version ?? "unknown"}{event.reason ? ` · ${event.reason}` : ""}</span></div>
                    <time dateTime={normalizeApiTimestamp(event.created_at)}>{dateLabel(event.created_at)}</time>
                  </li>;
                })}
              </ol>
            ) : <div className="version-empty"><span>No deployment history for this environment.</span><span>Confirmed changes are recorded here.</span></div>}
          </section>
        </div>
      )}
    </section>
  );
}

