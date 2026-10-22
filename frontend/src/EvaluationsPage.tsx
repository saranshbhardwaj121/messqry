import { useEffect, useMemo, useState, type FormEvent } from "react";
import { CircleAlert, CircleCheck, CircleX, FlaskConical, Plus, RefreshCw } from "lucide-react";
import {
  ApiResponseError,
  createEvaluationPolicy,
  loadEvaluationPolicies,
  loadVersionEvaluations,
  normalizeApiTimestamp,
  recordPolicyEvaluation,
} from "./api";
import type { Evaluation, EvaluationPolicy, MetricThreshold, Model } from "./types";

type EvaluationData = {
  policies: EvaluationPolicy[];
  byVersion: Map<number, Evaluation[]>;
};

const emptyData: EvaluationData = { policies: [], byVersion: new Map() };

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(normalizeApiTimestamp(value)),
  );
}

function parseJsonObject(value: string, fieldName: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${fieldName} must be valid JSON.`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${fieldName} must be a JSON object.`);
  }
  return parsed as Record<string, unknown>;
}

function parseMetrics(value: string): Record<string, number> {
  const parsed = parseJsonObject(value, "Metrics");
  if (!Object.keys(parsed).length) throw new Error("Enter at least one metric.");
  for (const [name, metric] of Object.entries(parsed)) {
    if (!name.trim() || typeof metric !== "number" || !Number.isFinite(metric)) {
      throw new Error("Metrics must map non-empty names to finite numbers.");
    }
  }
  return parsed as Record<string, number>;
}

function parseThresholds(value: string): Record<string, MetricThreshold> {
  const parsed = parseJsonObject(value, "Thresholds");
  if (!Object.keys(parsed).length) throw new Error("Enter at least one threshold.");
  for (const [name, raw] of Object.entries(parsed)) {
    if (!name.trim() || !raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("Each threshold needs a metric name, operator, and value.");
    }
    const threshold = raw as Record<string, unknown>;
    if (
      !["gt", "gte", "lt", "lte"].includes(String(threshold.operator)) ||
      typeof threshold.value !== "number" ||
      !Number.isFinite(threshold.value)
    ) {
      throw new Error(`Threshold for “${name}” must use gt, gte, lt, or lte and a finite value.`);
    }
  }
  return parsed as Record<string, MetricThreshold>;
}

function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const leftEntries = Object.entries(left as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
  return leftEntries.length === rightEntries.length && leftEntries.every(
    ([key, value], index) => key === rightEntries[index][0] && sameJson(value, rightEntries[index][1]),
  );
}

function latestForPolicy(items: Evaluation[], policyId: string): { evaluation?: Evaluation; tied: boolean } {
  const matches = items
    .filter((item) => item.policy_id === policyId)
    .sort((left, right) => Date.parse(normalizeApiTimestamp(right.created_at)) - Date.parse(normalizeApiTimestamp(left.created_at)));
  if (!matches.length) return { tied: false };
  const latestTime = normalizeApiTimestamp(matches[0].created_at);
  const tied = matches.filter((item) => normalizeApiTimestamp(item.created_at) === latestTime);
  return tied.length > 1 ? { tied: true } : { evaluation: tied[0], tied: false };
}

function thresholdLabel(threshold: MetricThreshold) {
  const operator = { gt: ">", gte: "≥", lt: "<", lte: "≤" }[threshold.operator];
  return `${operator} ${threshold.value}`;
}

function outcomeLabel(evaluation: Evaluation) {
  return evaluation.passed ? "Passed" : "Failed";
}

export function EvaluationsPage({
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
  const [versionNumber, setVersionNumber] = useState("");
  const [data, setData] = useState<EvaluationData>(emptyData);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [readDenied, setReadDenied] = useState(false);
  const [loadMessage, setLoadMessage] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  const selectedModel = models.find((model) => model.id === modelId) ?? models[0] ?? null;
  const versions = useMemo(
    () => [...(selectedModel?.versions ?? [])].sort((left, right) => right.version - left.version),
    [selectedModel],
  );
  const selectedVersionNumber = Number(versionNumber) || versions[0]?.version || 0;
  const selectedVersion = versions.find((version) => version.version === selectedVersionNumber) ?? versions[0] ?? null;
  const selectedEvaluations = selectedVersion ? data.byVersion.get(selectedVersion.version) ?? [] : [];
  const [policyId, setPolicyId] = useState("");

  const [datasetName, setDatasetName] = useState("");
  const [datasetVersion, setDatasetVersion] = useState("");
  const [datasetSha256, setDatasetSha256] = useState("");
  const [evaluatorName, setEvaluatorName] = useState("");
  const [evaluatorVersion, setEvaluatorVersion] = useState("");
  const [metricsJson, setMetricsJson] = useState('{\n  "auc": 0.95\n}');

  const [policyName, setPolicyName] = useState("");
  const [policyEnvironment, setPolicyEnvironment] = useState("production");
  const [policyVersion, setPolicyVersion] = useState("1");
  const [thresholdsJson, setThresholdsJson] = useState('{\n  "auc": { "operator": "gte", "value": 0.9 }\n}');

  useEffect(() => {
    if (selectedModel && modelId !== selectedModel.id) setModelId(selectedModel.id);
  }, [modelId, selectedModel]);

  useEffect(() => {
    if (!selectedVersion) {
      setVersionNumber("");
      return;
    }
    if (!versions.some((version) => version.version === Number(versionNumber))) {
      setVersionNumber(String(selectedVersion.version));
    }
  }, [selectedVersion, versionNumber, versions]);

  useEffect(() => {
    if (!selectedModel) {
      setData(emptyData);
      setReadDenied(false);
      setLoadMessage("");
      return;
    }
    let active = true;
    setLoadingDetails(true);
    setReadDenied(false);
    setLoadMessage("");
    const load = async () => {
      try {
        const [policies, evaluationsByVersion] = await Promise.all([
          loadEvaluationPolicies(selectedModel.id),
          Promise.all(
            (selectedModel.versions ?? []).map(async (version) => [
              version.version,
              await loadVersionEvaluations(selectedModel.id, version.version),
            ] as const),
          ),
        ]);
        if (!active) return;
        setData({ policies, byVersion: new Map(evaluationsByVersion) });
        if (!policies.some((policy) => policy.id === policyId)) {
          setPolicyId(policies[0]?.id ?? "");
        }
      } catch (cause) {
        if (!active) return;
        setData(emptyData);
        if (cause instanceof ApiResponseError && cause.status === 403) setReadDenied(true);
        else setLoadMessage(cause instanceof Error ? cause.message : "Evaluation data could not be loaded.");
      } finally {
        if (active) setLoadingDetails(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [selectedModel, reload]);

  const selectedPolicy = data.policies.find((policy) => policy.id === policyId) ?? null;
  const latestProductionPolicies = useMemo(() => {
    const latest = new Map<string, EvaluationPolicy>();
    for (const policy of data.policies.filter((item) => item.environment === "production")) {
      const current = latest.get(policy.name);
      if (!current || policy.version > current.version) latest.set(policy.name, policy);
    }
    return [...latest.values()].sort((left, right) => left.name.localeCompare(right.name));
  }, [data.policies]);

  const eligibility = useMemo(() => {
    if (!selectedVersion) return { ready: false, reason: "Select a model version to review eligibility." };
    if (!latestProductionPolicies.length) return { ready: false, reason: "No production evaluation policy is configured." };
    for (const policy of latestProductionPolicies) {
      const latest = latestForPolicy(data.byVersion.get(selectedVersion.version) ?? [], policy.id);
      if (latest.tied) return { ready: false, reason: `Policy “${policy.name}” has ambiguous latest evaluations at the same timestamp.` };
      if (!latest.evaluation) return { ready: false, reason: `Policy “${policy.name}” has no evaluation for v${selectedVersion.version}.` };
      const evaluation = latest.evaluation;
      if (
        evaluation.policy_name !== policy.name ||
        evaluation.policy_version !== policy.version ||
        evaluation.policy_environment !== policy.environment ||
        !sameJson(evaluation.thresholds, policy.thresholds)
      ) return { ready: false, reason: `Policy “${policy.name}” has an incompatible evaluation snapshot.` };
      if (!evaluation.passed) return { ready: false, reason: `The latest evaluation for “${policy.name}” failed.` };
    }
    return { ready: true, reason: `v${selectedVersion.version} passes all current production policies.` };
  }, [data.byVersion, latestProductionPolicies, selectedVersion]);

  const comparisonRows = useMemo(() => {
    if (!selectedPolicy) return [];
    return versions.map((version) => ({
      version,
      latest: latestForPolicy(data.byVersion.get(version.version) ?? [], selectedPolicy.id),
    }));
  }, [data.byVersion, selectedPolicy, versions]);

  async function submitEvaluation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedModel || !selectedVersion || !selectedPolicy) return;
    setBusy(true);
    setSubmitError("");
    setNotice("");
    try {
      const metrics = parseMetrics(metricsJson);
      await recordPolicyEvaluation(selectedModel.id, selectedVersion.version, {
        dataset_name: datasetName,
        dataset_version: datasetVersion,
        dataset_sha256: datasetSha256,
        evaluator_name: evaluatorName,
        evaluator_version: evaluatorVersion,
        policy_id: selectedPolicy.id,
        metrics,
      });
      setNotice(`Evaluation recorded for ${selectedModel.name} v${selectedVersion.version}.`);
      setReload((current) => current + 1);
      onRefresh();
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : "Evaluation could not be recorded.");
    } finally {
      setBusy(false);
    }
  }

  async function submitPolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedModel) return;
    setBusy(true);
    setSubmitError("");
    setNotice("");
    try {
      const thresholds = parseThresholds(thresholdsJson);
      const created = await createEvaluationPolicy(selectedModel.id, {
        name: policyName,
        environment: policyEnvironment,
        version: Number(policyVersion),
        thresholds,
      });
      setPolicyId(created.id);
      setNotice(`Policy “${created.name}” v${created.version} was created.`);
      setPolicyName("");
      setReload((current) => current + 1);
      onRefresh();
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : "Policy could not be created.");
    } finally {
      setBusy(false);
    }
  }

  const currentPolicyEvaluations = [...selectedEvaluations].sort(
    (left, right) => Date.parse(normalizeApiTimestamp(right.created_at)) - Date.parse(normalizeApiTimestamp(left.created_at)),
  );

  return (
    <section className="registry-page evaluations-page" aria-labelledby="evaluations-title">
      <header className="registry-page-header">
        <div>
          <div className="eyebrow">MODEL QUALITY / POLICY EVIDENCE</div>
          <h1 id="evaluations-title">Evaluations</h1>
          <p>Review metric submissions against versioned policies and inspect production eligibility.</p>
        </div>
      </header>

      {notice && <div className="registry-notice" role="status">{notice}</div>}
      {submitError && <div className="registry-error" role="alert">{submitError}</div>}
      {loadMessage && <div className="registry-error" role="alert">{loadMessage}</div>}

      {loading || loadingDetails ? (
        <div className="registry-state panel" role="status"><span className="spinner" /> Loading evaluation policies and records…</div>
      ) : loadError ? (
        <div className="registry-state panel" role="alert"><strong>Workspace data could not be loaded.</strong><button className="button-secondary" onClick={onRefresh}>Retry</button></div>
      ) : !models.length ? (
        <div className="registry-state panel"><FlaskConical size={25} aria-hidden="true" /><strong>Register a model before evaluating</strong><p>Evaluation runs belong to a specific registered model version.</p></div>
      ) : !selectedModel || !versions.length ? (
        <div className="registry-state panel"><FlaskConical size={25} aria-hidden="true" /><strong>No model versions are available</strong><p>Select a model with a registered version to inspect or record evaluations.</p>
          <label>Model<select value={selectedModel?.id ?? ""} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
        </div>
      ) : readDenied ? (
        <div className="registry-state panel" role="status"><CircleAlert size={24} aria-hidden="true" /><strong>Evaluation access is not granted</strong><p>Your current credential can access the workspace, but it does not include <code>messqry/evaluation.read</code>.</p></div>
      ) : (
        <>
          <section className="panel evaluation-target" aria-label="Evaluation target">
            <div className="panel-heading"><div><div className="eyebrow">TARGET VERSION</div><h2>Evaluation workspace</h2></div><button className="button-secondary" type="button" onClick={() => setReload((current) => current + 1)} aria-label="Refresh evaluations"><RefreshCw size={14} aria-hidden="true" /> Refresh</button></div>
            <div className="deployment-fields">
              <label>Model<select value={selectedModel.id} onChange={(event) => { setModelId(event.target.value); setVersionNumber(""); }}>
                {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select></label>
              <label>Version<select aria-label="Model version" value={String(selectedVersion.version)} onChange={(event) => setVersionNumber(event.target.value)}>
                {versions.map((version) => <option key={version.id} value={version.version}>v{version.version} · {version.validation_status ?? "unverified"}</option>)}
              </select></label>
            </div>
          </section>

          <section className={`evaluation-eligibility panel ${eligibility.ready ? "eligible" : "blocked"}`} aria-labelledby="eligibility-title">
            <div className="eligibility-icon">{eligibility.ready ? <CircleCheck size={19} /> : <CircleX size={19} />}</div>
            <div><div className="eyebrow">PRODUCTION POLICY CHECK</div><h2 id="eligibility-title">{eligibility.ready ? "Eligible by current policy evidence" : "Production promotion blocked"}</h2><p>{eligibility.reason}</p></div>
          </section>

          <div className="evaluation-layout">
            <div className="evaluation-main">
              <section className="panel evaluation-policy-panel" aria-labelledby="production-policies-title">
                <div className="panel-heading"><div><div className="eyebrow">RELEASE REQUIREMENTS</div><h2 id="production-policies-title">Current production policies</h2></div><span className="activity-count">{latestProductionPolicies.length}</span></div>
                {!latestProductionPolicies.length ? <div className="version-empty"><strong>No production policies yet.</strong><span>Create one to define the metrics required before production changes.</span></div> : (
                  <ul className="evaluation-policy-list">{latestProductionPolicies.map((policy) => {
                    const latest = latestForPolicy(selectedEvaluations, policy.id);
                    const evaluation = latest.evaluation;
                    const compatible = Boolean(evaluation && evaluation.policy_name === policy.name && evaluation.policy_version === policy.version && evaluation.policy_environment === policy.environment && sameJson(evaluation.thresholds, policy.thresholds));
                    const passed = Boolean(compatible && evaluation?.passed && !latest.tied);
                    const statusLabel = latest.tied ? "AMBIGUOUS" : !evaluation ? "MISSING" : !compatible ? "INCOMPATIBLE" : passed ? "PASS" : "FAIL";
                    return <li key={policy.id}>
                      <div className="policy-row-title"><strong>{policy.name} <span>v{policy.version}</span></strong><span className={`status-pill ${passed ? "success" : latest.tied || !evaluation || !compatible ? "warning" : "failure"}`}>{statusLabel}</span></div>
                      <div className="threshold-list">{Object.entries(policy.thresholds).map(([name, threshold]) => <span key={name}><code>{name}</code> {thresholdLabel(threshold)}</span>)}</div>
                    </li>;
                  })}</ul>
                )}
                <p className="evaluation-footnote">Quality eligibility only. Runtime readiness, deployment permissions, and the final API policy check are evaluated separately.</p>
              </section>

              <section className="panel evaluation-comparison" aria-labelledby="comparison-title">
                <div className="panel-heading"><div><div className="eyebrow">VERSION HISTORY</div><h2 id="comparison-title">Policy result comparison</h2></div>
                  <label className="compact-select">Policy<select aria-label="Comparison policy" value={policyId} onChange={(event) => setPolicyId(event.target.value)}>
                    {data.policies.map((policy) => <option key={policy.id} value={policy.id}>{policy.name} v{policy.version} · {policy.environment}</option>)}
                  </select></label>
                </div>
                {!selectedPolicy ? <div className="version-empty"><span>Create or select a policy to compare model versions.</span></div> : (
                  <div className="evaluation-table-wrap"><table className="evaluation-table"><thead><tr><th>Version</th><th>Latest result</th><th>Metrics</th><th>Evaluated</th></tr></thead><tbody>
                    {comparisonRows.map(({ version, latest }) => <tr key={version.id}>
                      <th scope="row">v{version.version}</th>
                      <td>{latest.tied ? <span className="status-pill warning">AMBIGUOUS</span> : latest.evaluation ? <span className={`status-pill ${latest.evaluation.passed ? "success" : "failure"}`}>{outcomeLabel(latest.evaluation)}</span> : <span className="status-pill neutral">NO RUN</span>}</td>
                      <td>{latest.evaluation ? Object.entries(latest.evaluation.metrics).map(([name, value]) => `${name}: ${value}`).join(" · ") : "—"}</td>
                      <td>{latest.evaluation ? formatDate(latest.evaluation.created_at) : "—"}</td>
                    </tr>)}
                  </tbody></table></div>
                )}
              </section>

              <section className="panel evaluation-records" aria-labelledby="records-title">
                <div className="panel-heading"><div><div className="eyebrow">RECORDED EVIDENCE</div><h2 id="records-title">{selectedModel.name} · v{selectedVersion.version}</h2></div><span className="activity-count">{selectedEvaluations.length}</span></div>
                {currentPolicyEvaluations.length ? <ol className="evaluation-record-list">{currentPolicyEvaluations.map((evaluation) => <li key={evaluation.id}>
                  <span className={`evaluation-record-mark ${evaluation.passed ? "success" : "failure"}`}>{evaluation.passed ? <CircleCheck size={16} /> : <CircleX size={16} />}</span>
                  <div className="evaluation-record-content">
                    <div className="policy-row-title"><strong>{evaluation.policy_name ?? "Legacy evaluation"} {evaluation.policy_version ? `· v${evaluation.policy_version}` : ""}</strong><span className={`status-pill ${evaluation.passed ? "success" : "failure"}`}>{outcomeLabel(evaluation)}</span></div>
                    <div className="evaluation-provenance">{evaluation.policy_name ? `${evaluation.policy_name}${evaluation.policy_version ? ` v${evaluation.policy_version}` : ""} · ` : ""}{evaluation.dataset_name ?? "Dataset provenance unavailable"}{evaluation.dataset_version ? ` · ${evaluation.dataset_version}` : ""} · {evaluation.evaluator_name ?? "Evaluator unknown"}{evaluation.evaluator_version ? ` ${evaluation.evaluator_version}` : ""}</div>
                    {evaluation.dataset_sha256 && <code className="evaluation-checksum">SHA-256 {evaluation.dataset_sha256}</code>}
                    <div className="threshold-results">{Object.entries(evaluation.thresholds ?? {}).map(([name, threshold]) => <span key={name}><code>{name}</code> {evaluation.metrics[name] ?? "—"} / {thresholdLabel(threshold)}</span>)}</div>
                    <time dateTime={normalizeApiTimestamp(evaluation.created_at)}>{formatDate(evaluation.created_at)}</time>
                  </div>
                </li>)}</ol> : <div className="version-empty"><span>No evaluations have been recorded for this version and policy.</span></div>}
              </section>
            </div>

            <aside className="evaluation-forms">
              <section className="panel evaluation-form-panel" aria-labelledby="record-evaluation-title">
                <div className="panel-heading"><div><div className="eyebrow">NEW RESULT</div><h2 id="record-evaluation-title">Record evaluation</h2></div><FlaskConical size={17} aria-hidden="true" /></div>
                <p className="evaluation-help">Submit metrics calculated by your evaluation process. The API records the provenance and checks the values against the selected immutable policy; it does not execute a dataset or model.</p>
                <form className="evaluation-form" onSubmit={submitEvaluation}>
                  <label>Policy<select aria-label="Evaluation policy" required value={policyId} onChange={(event) => setPolicyId(event.target.value)}>
                    <option value="">Select a policy</option>{data.policies.map((policy) => <option key={policy.id} value={policy.id}>{policy.name} v{policy.version} · {policy.environment}</option>)}
                  </select></label>
                  <div className="evaluation-fields-two">
                    <label>Dataset name<input required maxLength={128} value={datasetName} onChange={(event) => setDatasetName(event.target.value)} /></label>
                    <label>Dataset version<input required maxLength={128} value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)} /></label>
                  </div>
                  <label>Dataset SHA-256<input required pattern="[0-9a-fA-F]{64}" title="Enter a 64-character SHA-256 checksum" value={datasetSha256} onChange={(event) => setDatasetSha256(event.target.value)} /></label>
                  <div className="evaluation-fields-two">
                    <label>Evaluator<input required maxLength={128} value={evaluatorName} onChange={(event) => setEvaluatorName(event.target.value)} /></label>
                    <label>Evaluator version<input required maxLength={128} value={evaluatorVersion} onChange={(event) => setEvaluatorVersion(event.target.value)} /></label>
                  </div>
                  <label>Metrics <span className="form-optional">JSON OBJECT</span><textarea required rows={4} spellCheck={false} value={metricsJson} onChange={(event) => setMetricsJson(event.target.value)} /></label>
                  <button className="button-primary" type="submit" disabled={busy || !selectedPolicy || !selectedVersion}>{busy ? "Recording…" : "Record evaluation"}</button>
                </form>
              </section>

              <details className="panel evaluation-form-panel create-policy-panel">
                <summary><span><span className="eyebrow">POLICY MANAGEMENT</span><strong>Create policy version</strong></span><Plus size={17} aria-hidden="true" /></summary>
                <p className="evaluation-help">Policies are immutable. Publish a higher version when quality requirements change.</p>
                <form className="evaluation-form" onSubmit={submitPolicy}>
                  <label>Policy name<input required maxLength={120} value={policyName} onChange={(event) => setPolicyName(event.target.value)} /></label>
                  <div className="evaluation-fields-two">
                    <label>Environment<select value={policyEnvironment} onChange={(event) => setPolicyEnvironment(event.target.value)}><option value="production">production</option><option value="staging">staging</option></select></label>
                    <label>Version<input required min={1} max={2_147_483_647} type="number" value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} /></label>
                  </div>
                  <label>Metric thresholds <span className="form-optional">JSON OBJECT</span><textarea required rows={4} spellCheck={false} value={thresholdsJson} onChange={(event) => setThresholdsJson(event.target.value)} /></label>
                  <button className="button-secondary" type="submit" disabled={busy || !selectedModel}>{busy ? "Creating…" : "Create immutable policy"}</button>
                </form>
              </details>
            </aside>
          </div>
        </>
      )}
    </section>
  );
}
