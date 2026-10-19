# MessQry AI production-readiness roadmap

## First production release

The recommended first release is a **single-organization, AWS-hosted production service with a usable web console** for registering supported Python models, storing and retrieving their artifacts, serving them through authenticated prediction endpoints, recording evaluations, enforcing promotion rules, and observing production behavior. It should support one documented model packaging/runtime contract and one AWS region. Production access must be authenticated and authorized; deployment changes and evaluation decisions must be auditable; backups and rollback procedures must be exercised before release.

The current repository is a control-plane scaffold. It records model metadata and artifact URIs, deployment selections/history, and caller-supplied metric checks. It does not upload or inspect model artifacts, execute model evaluations, launch a model runtime, serve predictions, authenticate callers, monitor inference traffic, or deploy to AWS. Existing behavior remains unverified until the checks below pass.

## First-release boundaries

- One organization / one isolated deployment. No shared-tenant SaaS or customer self-service billing.
- A responsive web console is a first-release requirement for registry, deployment, evaluation, and operational workflows; an API-only release is not complete.
- A single, explicitly supported model packaging and serving contract. **Working assumption:** MLflow `pyfunc` packaged models. This requires confirmation before Phase 2/3 implementation.
- AWS-hosted artifact storage and runtime are in scope; local SQLite is a developer convenience, not a production database.
- Manual registration and controlled evaluation/promotion are in scope. Automated retraining, automatic traffic shifting, and autonomous promotion are not required for the first release.
- Operational dashboards, alerts, retention, and cost reporting cover the first-release runtime and its AWS resources; enterprise-wide FinOps is future work.
- Infrastructure and delivery definitions may be prepared and validated locally. Provisioning, deployment, publishing, or paid cloud use require a separate explicit instruction.

## Assumptions and decisions requiring owner input

These decisions do not block the repository audit. They must be settled before the dependent implementation phase starts.

| Decision | Working recommendation | Needed by |
| --- | --- | --- |
| Supported model format/runtime | MLflow `pyfunc`; reject unrecognized packaging and avoid loading arbitrary artifacts in the API process | Phase 2 |
| Authentication and identity | **Owner-selected:** OIDC-compatible user login plus scoped service credentials. Select the specific identity-provider product before integration. | Phase 1 approach selected; provider integration is Phase 3 |
| Artifact storage | Private S3 bucket, scoped IAM, checksums, and short-lived access where needed | Phase 2 |
| Deployment isolation | Dedicated container/task per model version with explicit CPU/memory limits; no arbitrary privileged containers | Phase 3 |
| Evaluation data and reproducibility | Versioned evaluation dataset reference, evaluator/runtime version, metric results, and threshold policy recorded together | Phase 4 |
| Production region, availability, and recovery targets | Owner must choose region, availability objective, RPO, and RTO before infrastructure sizing | Phase 6 |
| Data retention and cost limits | Owner must approve request/metric retention, artifact retention, and alert budgets | Phase 5 |
| Frontend stack and visual system | Working recommendation: React, TypeScript, and Vite with an accessible component system; confirm before frontend scaffold | Phase 1 |

## Candidate first-release architecture

This is a working design to guide implementation, not an approved deployment specification:

- An authenticated HTTPS edge routes requests to the FastAPI control-plane API. The API manages registry metadata, evaluation records, and deployment intent; it never loads arbitrary uploaded model code.
- A responsive web client uses the same authenticated edge and documented API contracts. It never accesses PostgreSQL, S3, or model runtimes directly.
- PostgreSQL stores registry, evaluation, deployment state, and audit history. Alembic migrations run as a controlled release step.
- A private S3 bucket stores immutable model packages and checksums. The API and runtime receive narrowly scoped IAM access.
- A separate, resource-limited model runtime serves predictions. It is isolated from the control plane and receives only the selected model package and bounded request data.
- Centralized logs and metrics capture operational signals with payload and credential redaction. Secrets come from a managed secret store.
- Staging and production are separate environments in one AWS region. The region and recovery objectives remain owner decisions.

Trust boundaries are the public client to authenticated edge, control plane to metadata database, control plane/runtime to private artifact storage, and control plane to isolated model runtime. The specific identity-provider product, exact AWS compute service, region, and isolation profile remain open decisions.

## Phases and dependencies

Verification commands below are planned acceptance checks, not evidence that they have already passed. Run database migration checks against a disposable database; never use or reset the ignored local `messqry.db` as a test fixture.

### Phase 0 — Repository audit and stabilization

**Outcome:** establish a trustworthy, repeatable baseline while preserving existing work.

**Required tasks**

- P0.1 Inventory repository contents, Git history, current branch, ignored local data, and tracked/untracked changes.
- P0.2 Verify and resolve the reported deployment-history, optimistic-concurrency, and readiness-schema findings.
- P0.3 Add regression/integration tests for the implemented registry, evaluation, deployment, readiness, and migration behavior.
- P0.4 Make local setup reproducible with declared development dependencies and consistent lint/format configuration.
- P0.5 Run checks against a clean disposable SQLite database and record actual results and remaining defects.

**Acceptance criteria:** unrelated and existing uncommitted work is preserved; all known review findings have regression coverage; migrations upgrade cleanly from base and metadata; tests and configured static checks pass; the setup steps work in a clean supported Python environment. Any outstanding failure is recorded rather than called complete.

**Verification commands:**

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff format --check .
```

Migration tests must create and use a temporary SQLite file, for example through pytest's `tmp_path`; they must not point at `messqry.db`. After the test suite is in place, the commands above are the canonical local checks.

### Phase 1 — Architecture and production foundations

**Depends on:** Phase 0.

**Outcome:** agree on service boundaries and establish production-safe configuration, persistence, API contracts, and delivery checks.

**Required tasks**

- P1.1 Record the proposed first-release architecture, trust boundaries, and supported deployment model; identify unapproved choices.
- P1.2 Record the first-release user/service authentication pattern and principal types; carry provider, claim mapping, and credential-lifecycle details into Phase 3 integration decisions.
- P1.3 Harden configuration validation, secret handling, database connection lifecycle, and structured error responses.
- P1.4 Define versioned API contracts, pagination/error conventions, and compatibility policy.
- P1.5 Add lint/type/security checks and CI that run the verified local checks.
- P1.6 Document schema migration, rollback, and environment-specific configuration procedures.
- P1.7 Define frontend user flows, information architecture, navigation, visual direction, accessibility baseline, and responsive behavior; confirm the frontend stack.
- P1.8 Scaffold the frontend application, API client/configuration, lint/test/build tooling, and local developer workflow.

**Acceptance criteria:** the architecture and authentication pattern/principal types are recorded; provider and detailed authorization choices are explicit and settled before serving integration; frontend navigation and workflows are approved; the frontend starts locally and builds against the documented API configuration; startup rejects invalid production configuration; secrets are not committed or logged; API errors are stable and non-sensitive; CI runs required checks on proposed changes; migration procedures are rehearsable without losing data.

**Verification commands:** `python -m pytest`; `ruff check .`; `ruff format --check .`; configured type/security tools; CI run on a branch; disposable-database migration upgrade/downgrade/upgrade.

For P1.8 and later frontend tasks, establish these scripts in the scaffold and run them at the repository root: `npm --prefix frontend run lint`, `npm --prefix frontend test`, and `npm --prefix frontend run build`.

### Phase 2 — Model registry and artifact management

**Depends on:** Phase 1 and confirmation of model packaging and artifact storage.

**Outcome:** provide a durable, access-controlled registry whose versions refer to validated, immutable artifacts.

**Required tasks**

- P2.1 Finalize model/version metadata and immutability rules for the supported package format.
- P2.2 Implement private artifact upload/download through the approved storage abstraction and S3 backend.
- P2.3 Validate package manifests, checksums, size limits, and permitted artifact locations without executing uploaded code in the control API.
- P2.4 Apply ownership/authorization checks to model and version operations.
- P2.5 Add lifecycle, access-control, corruption, and migration regression tests.
- P2.6 Build registry screens for model list/search, registration, detail, version history, artifact status, and empty/loading/error states.

**Acceptance criteria:** registered versions are immutable and traceable to checksummed artifacts; artifact access is private and authorized; invalid or oversized packages are rejected; credentials and user data do not appear in logs; supported lifecycle paths are covered by passing tests; users can complete registration and inspect model/version details in the web console with accessible validation and feedback states.

**Verification commands:** `python -m pytest`; `npm --prefix frontend test`; `ruff check .`; migration checks on disposable SQLite and PostgreSQL; artifact validation tests using local fixtures; UI flow tests for registration, list/detail, versions, and error/empty/loading states; S3 integration tests against an approved isolated test bucket only when access is explicitly available.

### Phase 3 — Secure model serving and deployment lifecycle

**Depends on:** Phases 1–2 and approval of runtime isolation/resource limits.

**Outcome:** deploy supported model versions through an authenticated, bounded serving path with reliable promotion, rollback, and audit.

**Required tasks**

- P3.1 Implement the approved serving adapter and prediction API contract for the supported model format.
- P3.2 Run model workloads outside the control API process with network, CPU, memory, filesystem, and timeout limits.
- P3.3 Implement authenticated deployment controls and authorization by model/environment.
- P3.4 Complete deployment state transitions, optimistic concurrency, idempotency, and immutable audit history.
- P3.5 Implement health checks, graceful shutdown, and rollback behavior for failed runtime starts.
- P3.6 Add integration, concurrency, abuse, and failure-injection tests.
- P3.7 Build deployment screens showing environment state, selected version, promotion/rollback actions, confirmation, and audit history.

**Acceptance criteria:** only supported artifacts can be served; a caller cannot access another unauthorized model; prediction requests are bounded and return documented errors; concurrent promotions cannot produce inconsistent current state or history; failed starts leave a recoverable state; rollback restores the selected prior version and produces an audit event; users can inspect deployment state and complete authorized promotion/rollback flows from the UI with clear confirmations and resulting history.

**Verification commands:** `python -m pytest`; `npm --prefix frontend test`; configured lint/type/security checks; SQLite and PostgreSQL concurrency tests; container security checks; API contract tests; browser tests for deployment state/promotion/rollback/history; failure-injection tests for start/stop and dependency loss.

### Phase 4 — Model evaluation and promotion policies

**Depends on:** Phases 1–3 and an agreed evaluation dataset/process.

**Outcome:** make evaluations reproducible and enforce quality thresholds before production promotion.

**Required tasks**

- P4.1 Define evaluation datasets, dataset versions, evaluator versions, metric semantics, and provenance.
- P4.2 Store evaluation runs and threshold policies as durable versioned records.
- P4.3 Enforce passing, current evaluation policies on production promotion; define override authorization and audit.
- P4.4 Add negative, boundary, reproducibility, and bypass-resistance tests.
- P4.5 Build evaluation screens for submitting approved evaluation inputs, reviewing metric/threshold results, and understanding promotion eligibility.

**Acceptance criteria:** an evaluation can be reproduced from recorded inputs; metric direction and threshold semantics are explicit; failed, missing, stale, or incompatible evaluations cannot be promoted to production except through an authorized, audited override; all policy paths are tested; the UI presents evaluation provenance, threshold outcomes, and promotion eligibility clearly.

**Verification commands:** `python -m pytest`; `npm --prefix frontend test`; evaluation replay and deterministic fixture comparisons; authorization and policy-bypass tests; UI tests for metric provenance, threshold outcomes, and promotion eligibility; migration checks on disposable SQLite and PostgreSQL.

### Phase 5 — Observability and operational controls

**Depends on:** Phases 1 and 3; Phase 4 for evaluation-quality signals.

**Outcome:** detect service/model degradation and control retention and runtime cost.

**Required tasks**

- P5.1 Add structured, correlated logs with sensitive-field redaction.
- P5.2 Record request volume, error rates, latency distributions, runtime health, and resource/cost estimates.
- P5.3 Define input/data-quality and drift signals appropriate to the supported model contract.
- P5.4 Add configurable alert conditions, delivery behavior, retention controls, and cost limits.
- P5.5 Add dashboards/runbooks and tests for metric correctness, redaction, alert firing, and retention.
- P5.6 Build operator dashboards for service/model health, latency, errors, drift, cost, and alert state, including empty and stale-data states.

**Acceptance criteria:** operators can identify a failing deployment and compare its quality/latency/error/cost signals; alerts fire on agreed thresholds; logs do not leak credentials or sensitive payloads; retention is enforced and documented; monitoring failure does not silently break prediction traffic; dashboard values, time ranges, units, and stale-data conditions are accurate and understandable.

**Verification commands:** `python -m pytest`; `npm --prefix frontend test`; telemetry contract tests; redaction tests; alert simulations; retention tests; dashboard tests for values, units, time ranges, and stale data; load tests with recorded latency/error/resource evidence.

### Phase 6 — AWS deployment and delivery

**Depends on:** Phases 1–5 and confirmed region, availability, RPO/RTO, and cost limits.

**Outcome:** produce reviewable infrastructure and delivery definitions for separate staging and production environments.

**Required tasks**

- P6.1 Add infrastructure as code for networking, compute, database, artifact storage, encryption, and least-privilege IAM.
- P6.2 Build and scan pinned application/runtime container images.
- P6.3 Integrate managed secrets, key rotation, backups, and recovery controls.
- P6.4 Add CI/CD with migration ordering, staged rollout, health gates, and rollback artifacts.
- P6.5 Document and locally validate deployment/rollback workflows for staging and production.
- P6.6 Package and deliver the frontend through the approved secure hosting path with separate staging/production configuration, cache rules, and browser security headers.

**Acceptance criteria:** infrastructure changes are reviewable and reproducible; staging and production are isolated; credentials are managed outside source control; CI deploy gates require verified build/test/security checks; frontend bundles contain no secrets and use the correct environment API endpoint; the rollback procedure and backup recovery are demonstrated in a non-production environment. Cloud provisioning/deployment is a separate user-authorized action.

**Verification commands:** infrastructure formatter/validator and policy scanner; container build and vulnerability scan; `npm --prefix frontend run build`; CI workflow checks; deployment package dry-run; browser checks for environment configuration and security headers; migration and rollback rehearsal in an explicitly approved staging environment.

### Phase 7 — Production readiness and release

**Depends on:** Phases 0–6.

**Outcome:** make an evidence-backed release decision and provide operators with complete runbooks.

**Required tasks**

- P7.1 Complete security and privacy review, threat model, dependency scan, and authorization audit.
- P7.2 Verify load, concurrency, latency, resource limits, and graceful degradation against agreed targets.
- P7.3 Exercise backup restoration, migration recovery, failed deployment recovery, and incident procedures.
- P7.4 Complete operator/user documentation, known limitations, support ownership, and incident runbooks.
- P7.5 Review release criteria, unresolved risks, rollback plan, and approval evidence.
- P7.6 Complete browser end-to-end journeys, keyboard/screen-reader/accessibility review, responsive checks, and UI performance acceptance.

**Acceptance criteria:** agreed security/performance/recovery objectives have measured evidence; registry, evaluation, deployment, rollback, and operations journeys pass browser end-to-end checks; keyboard, screen-reader, responsive, and UI performance criteria are met; no unresolved release-blocking issue remains; restore and rollback drills succeed; runbooks have an owner; release approval is explicit. Passing local checks alone does not establish production readiness.

**Verification commands:** full CI suite; frontend browser end-to-end, keyboard/accessibility, responsive, and performance suites; dependency/container/IaC scans; production-like load and failure drills; backup restore and migration rehearsal; release checklist review with stored evidence.

## Future SaaS capabilities (outside the first release)

- Multi-tenant organization management, tenant isolation, quotas, billing, and self-service onboarding.
- Automated retraining triggers and pipelines, automatic promotion/rollback, and broader framework support.
- Multi-region availability, enterprise identity integrations, and customer-managed keys.
- Advanced drift analytics, fleet-level cost optimization, and multi-cloud hosting.
