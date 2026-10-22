import { useEffect, useRef, useState, type RefObject } from "react";
import {
  ArrowRight,
  BrainCircuit,
  ChartNoAxesCombined,
  CircleCheck,
  CircleX,
  Database,
  FlaskConical,
  Menu,
  PanelsTopLeft,
  Rocket,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { loadOverview, normalizeApiTimestamp, type OverviewData } from "./api";
import { ModelsPage } from "./ModelsPage";
import { DeploymentsPage } from "./DeploymentsPage";
import type { Model } from "./types";
import { AuthScreen } from "./AuthScreen";
import {
  getAuthMessage,
  getAuthStatus,
  initializeAuth,
  subscribeAuth,
  type AuthStatus,
} from "./auth";

const sections = [
  { label: "Overview", icon: PanelsTopLeft },
  { label: "Models", icon: BrainCircuit },
  { label: "Deployments", icon: Rocket },
  { label: "Evaluations", icon: FlaskConical },
  { label: "Monitoring", icon: ChartNoAxesCombined },
  { label: "Settings", icon: SlidersHorizontal },
];

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(normalizeApiTimestamp(value)),
  );
}

function initials(value: string) {
  return value
    .split(/[\s_-]+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function Sidebar({
  active,
  onNavigate,
  open,
  onClose,
  isMobile,
  sidebarRef,
  activeNavRef,
}: {
  active: string;
  onNavigate: (section: string) => void;
  open: boolean;
  onClose: () => void;
  isMobile: boolean;
  sidebarRef: RefObject<HTMLElement | null>;
  activeNavRef: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <>
      {open && <button aria-label="Close navigation" className="scrim" onClick={onClose} />}
      <aside
        aria-hidden={isMobile && !open}
        aria-label="Workspace navigation"
        aria-modal={isMobile && open ? true : undefined}
        className={`sidebar ${open ? "sidebar-open" : ""}`}
        id="workspace-navigation"
        inert={isMobile && !open}
        ref={sidebarRef}
        role={isMobile ? "dialog" : undefined}
      >
        <a className="brand" href="#overview" onClick={() => { onNavigate("Overview"); onClose(); }}>
          <span aria-hidden="true" className="brand-mark">M</span>
          <span className="brand-name">MessQry<span>AI</span></span>
        </a>
        <div className="workspace-switcher">
          <div className="workspace-avatar"><PanelsTopLeft size={17} aria-hidden="true" /></div>
          <div className="workspace-copy"><strong>Local workspace</strong><span>MessQry AI</span></div>
        </div>
        <div className="nav-label">Workspace</div>
        <nav aria-label="Main navigation" className="main-nav">
          {sections.map(({ label, icon: Icon }) => (
            <button
              aria-current={active === label ? "page" : undefined}
              aria-label={label}
              className={`nav-item ${active === label ? "active" : ""}`}
              key={label}
              ref={active === label ? activeNavRef : null}
              onClick={() => { onNavigate(label); onClose(); }}
            >
            <Icon className="nav-icon" size={19} strokeWidth={2.1} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status"><span aria-hidden="true" />Local preview</div>
        </div>
      </aside>
    </>
  );
}

function StatusPill({ children, tone = "neutral" }: { children: string; tone?: "neutral" | "success" | "warning" | "failure" | "info" }) {
  return <span className={`status-pill ${tone}`}><i />{children}</span>;
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
}: {
  label: string;
  value: string | number;
  note: string;
  icon: typeof BrainCircuit;
}) {
  return (
    <article className="metric-card">
      <div className="metric-top"><span>{label}</span><Icon className="metric-icon" size={17} aria-hidden="true" /></div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
    </article>
  );
}

function LifecycleCard() {
  const nodes = [
    { title: "Register", caption: "Version the artifact", icon: Database },
    { title: "Evaluate", caption: "Check quality", icon: FlaskConical },
    { title: "Deploy", caption: "Select a version", icon: Rocket },
    { title: "Observe", caption: "Track behavior", icon: ChartNoAxesCombined },
  ];
  return (
    <article className="panel lifecycle-panel">
      <div className="panel-heading"><div><div className="eyebrow">THE MODEL LOOP</div><h3>From artifact to operation</h3></div><span className="panel-index">01 / LIFECYCLE</span></div>
      <div className="lifecycle-track" role="img" aria-label="Model lifecycle: register, evaluate, deploy, observe">
        {nodes.map(({ title, caption, icon: Icon }, index) => (
          <div className="lifecycle-step" key={title}>
            <div className={`lifecycle-node node-${index + 1}`}><Icon size={20} strokeWidth={2.1} /></div>
            <strong>{title}</strong><span>{caption}</span>
            {index < nodes.length - 1 && <span className="flow-line" />}
          </div>
        ))}
      </div>
      <div className="lifecycle-foot"><span><span className="live-dot" /> LIFECYCLE WORKFLOW</span><span>Visual guide · operational data shown separately</span></div>
    </article>
  );
}

function ActivityPanel({ items }: { items: OverviewData["activity"] }) {
  return (
    <article className="panel activity-panel">
      <div className="panel-heading"><div><div className="eyebrow">LATEST CHANGES</div><h3>Recent activity</h3></div><span className="activity-count">{items.length.toString().padStart(2, "0")}</span></div>
      {items.length === 0 ? (
        <div className="inline-empty"><div className="empty-glyph"><ChartNoAxesCombined size={19} /></div><div><strong>No activity recorded yet</strong><span>Evaluations and deployment changes will appear here.</span></div></div>
      ) : (
        <ol className="activity-list">
          {items.map((item) => <li key={item.id}>
            <span className={`activity-marker ${item.status}`} aria-label={item.status}>{item.status === "failure" ? <CircleX size={16} /> : item.status === "success" ? <CircleCheck size={16} /> : <ChartNoAxesCombined size={16} />}</span>
            <div className="activity-copy"><strong>{item.label}</strong><span>{item.detail}</span></div>
            <time>{formatDate(item.time)}</time>
          </li>)}
        </ol>
      )}
      <div className="panel-footer"><span>Events are read from the registry API</span><ArrowRight size={14} /></div>
    </article>
  );
}

function ModelPanel({ models, onNavigate }: { models: Model[]; onNavigate: (section: string) => void }) {
  return (
    <article className="panel model-panel">
      <div className="panel-heading"><div><div className="eyebrow">REGISTRY</div><h3>Models</h3></div><button className="text-action" onClick={() => onNavigate("Models")}>Open registry <ArrowRight size={14} /></button></div>
      {models.length === 0 ? (
        <div className="registry-empty"><div className="empty-illustration"><BrainCircuit size={25} /></div><strong>Your registry is ready</strong><p>Register a model to start tracking versions, evaluations, and deployment selections.</p><button className="button-primary" onClick={() => onNavigate("Models")}>Explore models <ArrowRight size={15} /></button></div>
      ) : (
        <ul className="model-list">{models.slice(0, 4).map((model) => <li key={model.id}>
          <div className="model-avatar">{initials(model.name)}</div>
          <div className="model-copy"><strong>{model.name}</strong><span>{model.versions?.length ?? 0} registered versions</span></div>
          <span className="model-version">{model.versions?.length ? `v${model.versions.at(-1)?.version}` : "NO VERSION"}</span>
        </li>)}</ul>
      )}
    </article>
  );
}

function SignalPanel() {
  return (
    <article className="panel signal-panel">
      <div className="panel-heading"><div><div className="eyebrow">PRODUCTION SIGNALS</div><h3>Monitoring</h3></div><StatusPill tone="warning">NOT CONNECTED</StatusPill></div>
      <div className="signal-empty"><div className="signal-mark"><ChartNoAxesCombined size={20} strokeWidth={2.1} /></div><div><strong>Telemetry is not available yet</strong><p>Inference traffic and runtime metrics are not connected in this release stage. No live latency, drift, uptime, or cost values are being reported.</p></div></div>
      <div className="signal-footer"><span>Telemetry unavailable</span><span>Monitoring is planned for Phase 5</span></div>
    </article>
  );
}

function Overview({ data, onNavigate }: { data: OverviewData; onNavigate: (section: string) => void }) {
  const healthy = data.deployments.length;
  return (
    <>
      <section className="welcome-row">
        <div><div className="eyebrow welcome-eyebrow">MODEL OPERATIONS / OVERVIEW</div><h1>Model operations</h1><p>Registry activity, deployment selections, and evaluation records from your configured API.</p></div>
        <div className="welcome-side"><div className="system-state"><div><span>CONTROL PLANE</span><strong><i /> API CONNECTED</strong></div></div><div className="welcome-meta"><span>WORKSPACE</span><code>LOCAL / SINGLE ORG</code></div></div>
      </section>
      <section className="metric-grid" aria-label="Workspace summary">
        <MetricCard label="REGISTERED MODELS" value={data.models.length.toString().padStart(2, "0")} note={data.models.length ? "From model registry" : "No models registered"} icon={BrainCircuit} />
        <MetricCard label="DEPLOYMENT SELECTIONS" value={healthy.toString().padStart(2, "0")} note={healthy ? "Version selections recorded" : "No deployment selections"} icon={Rocket} />
        <MetricCard label="EVALUATION RECORDS" value={data.evaluationCount === null ? "—" : data.evaluationCount.toString().padStart(2, "0")} note={data.evaluationCount === null ? "Evaluation access not granted" : data.evaluationCount ? "Recorded metric checks" : "No evaluations recorded"} icon={FlaskConical} />
        <article className="metric-card metric-unavailable"><div className="metric-top"><span>INFERENCE HEALTH</span><ChartNoAxesCombined className="metric-icon" size={19} aria-hidden="true" /></div><div className="metric-value unavailable-value">—</div><div className="metric-note">No serving telemetry source</div></article>
      </section>
      <section className="content-grid content-top"><LifecycleCard /><ActivityPanel items={data.activity} /></section>
      <section className="content-grid content-bottom"><ModelPanel models={data.models} onNavigate={onNavigate} /><SignalPanel /></section>
    </>
  );
}

function EmptySection({ section, onNavigate }: { section: string; onNavigate: (section: string) => void }) {
  const descriptions: Record<string, string> = {
    Models: "Manage model records, versions, and artifact metadata.",
    Deployments: "Review environment selections, promotions, and rollback history.",
    Evaluations: "Inspect recorded metric checks and quality thresholds.",
    Monitoring: "Operational telemetry and alerts will appear here when a serving runtime is connected.",
    Settings: "Workspace access and service configuration will be available here.",
  };
  return <section className="placeholder-panel"><div className="eyebrow">MESSQRY AI / {section.toUpperCase()}</div><h1>{section}</h1><p>{descriptions[section]}</p><div className="placeholder-state"><span className="status-pill">IN DEVELOPMENT</span><span>This section is not part of the current Overview milestone.</span></div><button className="button-secondary" onClick={() => onNavigate("Overview")}>Back to overview <ArrowRight size={14} /></button></section>;
}

export default function App() {
  const [authStatus, setAuthStatus] = useState<AuthStatus>(getAuthStatus());
  const [authMessage, setAuthMessage] = useState<string | undefined>(getAuthMessage());
  const [active, setActive] = useState("Overview");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia?.("(max-width: 760px)").matches === true,
  );
  const mobileMenuRef = useRef<HTMLButtonElement>(null);
  const activeNavRef = useRef<HTMLButtonElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const allowedEnvironments = ["local", "development", "staging", "production"];
  const configuredEnvironment = import.meta.env.VITE_DEPLOYMENT_ENV?.trim().toLowerCase();
  const environmentLabel = allowedEnvironments.includes(configuredEnvironment ?? "")
    ? configuredEnvironment!.toUpperCase()
    : "LOCAL";

  useEffect(() => {
    const unsubscribe = subscribeAuth((nextStatus, message) => {
      setAuthStatus(nextStatus);
      setAuthMessage(message);
    });
    void initializeAuth();
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setIsMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (authStatus !== "local" && authStatus !== "authenticated") {
      setData(null);
      setError(false);
      return;
    }
    let alive = true;
    setError(false);
    setData(null);
    loadOverview().then((result) => { if (alive) setData(result); }).catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [authStatus, refresh]);

  useEffect(() => {
    if (!mobileNavOpen || !isMobile) return;
    const focusFrame = window.requestAnimationFrame(() => activeNavRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileNavOpen(false);
        window.requestAnimationFrame(() => mobileMenuRef.current?.focus());
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = sidebarRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isMobile, mobileNavOpen]);

  const navigate = (section: string) => setActive(section);
  const closeMobileNav = () => {
    setMobileNavOpen(false);
    if (isMobile) window.requestAnimationFrame(() => mobileMenuRef.current?.focus());
  };
  if (authStatus !== "local" && authStatus !== "authenticated") {
    return <AuthScreen status={authStatus} message={authMessage} />;
  }
  return (
    <div className="app-shell">
      <Sidebar
        active={active}
        activeNavRef={activeNavRef}
        isMobile={isMobile}
        onClose={closeMobileNav}
        onNavigate={navigate}
        open={mobileNavOpen}
        sidebarRef={sidebarRef}
      />
      <main className="main-content" inert={isMobile && mobileNavOpen}>
        <header className="topbar"><button aria-controls="workspace-navigation" aria-expanded={mobileNavOpen} aria-label="Open navigation" className="mobile-menu icon-button" onClick={() => setMobileNavOpen(true)} ref={mobileMenuRef}><Menu size={19} /></button><div className="breadcrumb"><span>MessQry AI</span><span className="breadcrumb-slash">/</span><strong>{active}</strong></div><div className="topbar-tools"><span className="environment-chip"><i /> {environmentLabel}</span></div></header>
        <div className="page-wrap">
          {active === "Overview" ? <>
            {error ? <div className="load-banner error-state" role="alert"><span><X size={16} /> Could not load workspace data from the API.</span><button onClick={() => setRefresh((current) => current + 1)}>Retry <ArrowRight size={13} /></button></div> : !data ? <div className="load-banner" role="status"><span className="spinner" /> Connecting to registry API…</div> : null}
            {data && <Overview data={data} onNavigate={navigate} />}
          </> : active === "Models" ? <ModelsPage
            models={data?.models ?? []}
            loading={!data && !error}
            loadError={error}
            onRefresh={() => setRefresh((current) => current + 1)}
          /> : active === "Deployments" ? <DeploymentsPage
            models={data?.models ?? []}
            loading={!data && !error}
            loadError={error}
            onRefresh={() => setRefresh((current) => current + 1)}
          /> : <EmptySection section={active} onNavigate={navigate} />}
          <footer className="page-footer"><span>MESSQRY AI <i>·</i> MODEL LIFECYCLE WORKSPACE</span><span><span className="footer-dot" /> DATA FROM CONFIGURED API <span className="footer-sep">/</span> NO DEMO METRICS</span></footer>
        </div>
      </main>
    </div>
  );
}
