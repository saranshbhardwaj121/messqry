import { useMemo, useState, type FormEvent } from "react";
import { ArrowDownToLine, ArrowUpFromLine, BrainCircuit, Plus, Search } from "lucide-react";
import { createModel, normalizeApiTimestamp, uploadModelArtifact } from "./api";
import type { Model } from "./types";

function formatBytes(value: number | null | undefined): string {
  if (!value || value < 1) return "Size unavailable";
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function ModelsPage({
  models,
  loading,
  loadError,
  onRefresh,
}: {
  models: Model[];
  loading: boolean;
  loadError: boolean;
  onRefresh: (newModelId?: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showRegistration, setShowRegistration] = useState(false);
  const [modelName, setModelName] = useState("");
  const [description, setDescription] = useState("");
  const [framework, setFramework] = useState("");
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const filteredModels = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return models;
    return models.filter((model) =>
      `${model.name} ${model.description ?? ""}`.toLowerCase().includes(normalized),
    );
  }, [models, query]);
  const selectedModel =
    models.find((model) => model.id === selectedId) ?? filteredModels[0] ?? models[0] ?? null;

  async function registerModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const model = await createModel({ name: modelName, description });
      setModelName("");
      setDescription("");
      setShowRegistration(false);
      setNotice(`Model “${model.name}” was registered.`);
      setSelectedId(model.id);
      onRefresh(model.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model registration failed.");
    } finally {
      setBusy(false);
    }
  }

  async function registerVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedModel) return;
    if (!packageFile) {
      setError("Choose a ZIP package before uploading.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const version = await uploadModelArtifact(selectedModel.id, packageFile, framework);
      setPackageFile(null);
      setFramework("");
      setNotice(`Version v${version.version} was validated and stored.`);
      onRefresh(selectedModel.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Artifact upload failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="registry-page" aria-labelledby="models-title">
      <header className="registry-page-header">
        <div>
          <div className="eyebrow">MODEL REGISTRY / VERSIONED ARTIFACTS</div>
          <h1 id="models-title">Models</h1>
          <p>Register model records, upload supported packages, and inspect their version history.</p>
        </div>
        <button
          className="button-primary"
          onClick={() => {
            setShowRegistration((current) => !current);
            setError("");
            setNotice("");
          }}
        >
          <Plus size={15} aria-hidden="true" /> Register model
        </button>
      </header>

      {notice && <div className="registry-notice" role="status">{notice}</div>}
      {error && <div className="registry-error" role="alert">{error}</div>}

      {showRegistration && (
        <form className="registry-form panel" onSubmit={registerModel}>
          <div className="panel-heading">
            <div><div className="eyebrow">NEW RECORD</div><h2>Register a model</h2></div>
            <span className="panel-index">MODEL METADATA</span>
          </div>
          <label>
            Model name
            <input
              autoFocus
              maxLength={120}
              onChange={(event) => setModelName(event.target.value)}
              placeholder="e.g. fraud-risk-classifier"
              required
              value={modelName}
            />
          </label>
          <label>
            Description <span className="form-optional">OPTIONAL</span>
            <textarea
              maxLength={1000}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What does this model predict?"
              rows={2}
              value={description}
            />
          </label>
          <div className="form-actions">
            <button className="button-secondary" onClick={() => setShowRegistration(false)} type="button">Cancel</button>
            <button className="button-primary" disabled={busy} type="submit">{busy ? "Registering…" : "Create model"}</button>
          </div>
        </form>
      )}

      <div className="registry-toolbar">
        <label className="registry-search">
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search models</span>
          <input onChange={(event) => setQuery(event.target.value)} placeholder="Search models" value={query} />
        </label>
        <span className="registry-count">{models.length} {models.length === 1 ? "MODEL" : "MODELS"}</span>
      </div>

      {loading ? (
        <div className="registry-state panel" role="status"><span className="spinner" /> Loading registry…</div>
      ) : loadError ? (
        <div className="registry-state panel" role="alert">
          <strong>Registry data could not be loaded.</strong>
          <button className="button-secondary" onClick={() => onRefresh()} type="button">Retry</button>
        </div>
      ) : models.length === 0 ? (
        <div className="registry-state panel">
          <BrainCircuit size={27} aria-hidden="true" />
          <strong>Your model registry is empty</strong>
          <p>Create a model record, then upload an MLflow <code>pyfunc</code> ZIP as its first version.</p>
          <button className="button-primary" onClick={() => setShowRegistration(true)} type="button"><Plus size={15} /> Register first model</button>
        </div>
      ) : filteredModels.length === 0 ? (
        <div className="registry-state panel" role="status"><strong>No models match “{query}”.</strong><p>Try a different name or description.</p></div>
      ) : (
        <div className="registry-layout">
          <nav className="registry-model-list panel" aria-label="Registered models">
            <div className="panel-heading"><div><div className="eyebrow">WORKSPACE</div><h2>All models</h2></div><span className="activity-count">{filteredModels.length}</span></div>
            <ul>
              {filteredModels.map((model) => (
                <li key={model.id}>
                  <button
                    aria-current={selectedModel?.id === model.id ? "true" : undefined}
                    className={`registry-model-item ${selectedModel?.id === model.id ? "selected" : ""}`}
                    onClick={() => { setSelectedId(model.id); setNotice(""); setError(""); }}
                  >
                    <span className="registry-model-icon"><BrainCircuit size={16} aria-hidden="true" /></span>
                    <span className="registry-model-copy"><strong>{model.name}</strong><span>{model.versions?.length ?? 0} versions</span></span>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          {selectedModel && (
            <div className="registry-detail-column">
              <article className="registry-detail panel">
                <div className="registry-detail-heading">
                  <div className="registry-model-icon large"><BrainCircuit size={19} aria-hidden="true" /></div>
                  <div><div className="eyebrow">MODEL DETAILS</div><h2>{selectedModel.name}</h2></div>
                  <span className="registry-version-count">{selectedModel.versions?.length ?? 0} VERSIONS</span>
                </div>
                <p>{selectedModel.description || "No description provided."}</p>
                <dl className="registry-meta">
                  <div><dt>Model ID</dt><dd><code>{selectedModel.id}</code></dd></div>
                  <div><dt>Created</dt><dd>{new Date(normalizeApiTimestamp(selectedModel.created_at)).toLocaleDateString()}</dd></div>
                </dl>
              </article>

              <section className="registry-versions panel" aria-labelledby="versions-title">
                <div className="panel-heading"><div><div className="eyebrow">IMMUTABLE ARTIFACTS</div><h2 id="versions-title">Version history</h2></div><span className="activity-count">{selectedModel.versions?.length ?? 0}</span></div>
                {selectedModel.versions?.length ? (
                  <ol className="version-list">
                    {[...selectedModel.versions].reverse().map((version) => (
                      <li key={version.id}>
                        <span className="version-mark"><ArrowDownToLine size={15} aria-hidden="true" /></span>
                        <div className="version-copy"><strong>Version v{version.version}</strong><span>{version.package_format ?? "Legacy artifact"}{version.framework ? ` · ${version.framework}` : ""} · {formatBytes(version.artifact_size_bytes)}</span></div>
                        <span className={`status-pill ${version.validation_status === "validated" ? "success" : "warning"}`}><i />{version.validation_status === "validated" ? "VALIDATED" : "UNVERIFIED"}</span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="version-empty"><span>No artifact versions yet.</span><span>Upload an MLflow <code>pyfunc</code> ZIP below to create the first version.</span></div>
                )}
              </section>

              <form className="registry-upload panel" noValidate onSubmit={registerVersion}>
                <div className="panel-heading"><div><div className="eyebrow">NEW IMMUTABLE VERSION</div><h2>Upload package</h2></div><ArrowUpFromLine size={17} aria-hidden="true" /></div>
                <p>Upload a ZIP containing a root <code>MLmodel</code> file with the <code>python_function</code> flavor. The API validates its paths and limits without loading model code.</p>
                <label>
                  Model package (.zip)
                  <input
                    accept=".zip,application/zip,application/x-zip-compressed"
                    onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)}
                    required
                    type="file"
                  />
                </label>
                <label>
                  Framework <span className="form-optional">OPTIONAL</span>
                  <input maxLength={80} onChange={(event) => setFramework(event.target.value)} placeholder="e.g. scikit-learn" value={framework} />
                </label>
                <div className="form-actions">
                  <span className="upload-limit">ZIP up to 512 MB</span>
                  <button className="button-primary" disabled={busy || !packageFile} type="submit">{busy ? "Validating and storing…" : "Upload version"}</button>
                </div>
              </form>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
