# MessQry AI progress

## Current status

- **Current phase:** Phase 1 — Architecture and production foundations (in progress)
- **Current task:** P1.3 — Harden production configuration, secret handling, database lifecycle, and structured error responses.
- **Phase count:** 1 of 8 phases complete. Phase 0 is complete; no claim of production readiness is made.
- **Phase 0 task count:** 5 of 5 required tasks complete.
- **Phase 1 task count:** 2 of 8 required tasks complete; the architecture proposal and owner-selected authentication pattern are recorded.
- **Blockers:** The specific identity-provider product, detailed role/scope mapping, and credential rotation remain open before Phase 3 auth integration. PostgreSQL service/access is not configured; only disposable SQLite was exercised.
- **Next action:** complete P1.3 verification, including application shutdown/lifespan behavior, then proceed to API contracts and CI.

## Progress by phase

### Phase 0 — Repository audit and stabilization (5/5 tasks complete)

- [x] **P0.1** Inventory repository contents, Git history, current branch, ignored local data, and tracked/untracked changes. **Complete:** reviewed `AGENTS.md`, Git history/status/diff, file inventory, config, source, migrations, and local Python dependency versions on 2026-10-19.
- [x] **P0.2** Verify and resolve the reported deployment-history, optimistic-concurrency, and readiness-schema findings. Regression coverage confirms event history, initial deployment insert conflicts -> 409, stale promotion conflicts -> 409 without extra history, and top-level readiness 503 JSON matching OpenAPI.
- [x] **P0.3** Add regression/integration tests for registry, evaluation, deployment, readiness, and migration behavior. Eight tests exercise route functions with isolated SQLite sessions, OpenAPI/readiness response shape, and migration lifecycle. Direct route tests are used because FastAPI TestClient hangs at startup in this Windows environment, including on a minimal app.
- [x] **P0.4** Make local setup reproducible with declared development dependencies and consistent lint/format configuration. Added the `dev` extra, pytest settings, Ruff rules, and documented clean-environment install/check commands.
- [x] **P0.5** Run checks against a clean disposable SQLite database and record actual results and remaining defects. Results: 8 passed; Alembic upgrade/check/downgrade/upgrade passed; Ruff check and format check passed; `pip check` passed. The ignored `messqry.db` was not used.

**Phase acceptance:** satisfied for the local SQLite scope and verified findings. PostgreSQL-specific behavior, HTTP transport behavior, and production behavior remain unverified and are later-phase work.

### Phase 1 — Architecture and production foundations (2/8 tasks complete)

- [x] **P1.1** Record first-release architecture, trust boundaries, and supported deployment model. Candidate architecture and unresolved choices are documented in `ROADMAP.md`; owner approval is still pending.
- [x] **P1.2** Record the first-release authentication pattern and principal types. Owner selected OIDC-compatible user login plus scoped service credentials; specific provider, detailed role/scope mapping, and credential rotation are Phase 3 integration decisions.
- [ ] **P1.3** Harden configuration validation, secret handling, database lifecycle, and structured errors. **Current task; implementation present, verification pending for ASGI lifespan behavior.**
- [ ] **P1.4** Define versioned API contracts, pagination/error conventions, and compatibility policy.
- [ ] **P1.5** Add lint/type/security checks and CI.
- [ ] **P1.6** Document schema migration, rollback, and environment-specific configuration.
- [ ] **P1.7** Define frontend user flows, information architecture, navigation, visual direction, accessibility, and responsive behavior; confirm the stack.
- [ ] **P1.8** Scaffold the frontend, API client/configuration, lint/test/build tooling, and local developer workflow.

### Phase 2 — Model registry and artifact management (0/6 tasks complete)

- [ ] **P2.1** Finalize model/version metadata and immutability rules for the supported package format.
- [ ] **P2.2** Implement private artifact storage abstraction and S3 backend.
- [ ] **P2.3** Validate manifests, checksums, size limits, and permitted artifact locations.
- [ ] **P2.4** Add ownership/authorization checks to model and version operations.
- [ ] **P2.5** Add lifecycle, access-control, corruption, and migration regression tests.
- [ ] **P2.6** Build registry screens for model list/search, registration, detail, version history, artifact status, and empty/loading/error states.

### Phase 3 — Secure model serving and deployment lifecycle (0/7 tasks complete)

- [ ] **P3.1** Implement the approved serving adapter and prediction API contract.
- [ ] **P3.2** Isolate model workloads and enforce resource/network/filesystem/time limits.
- [ ] **P3.3** Add authenticated deployment controls and authorization by model/environment.
- [ ] **P3.4** Complete deployment transitions, optimistic concurrency, idempotency, and immutable audit history.
- [ ] **P3.5** Add runtime health, graceful shutdown, and failed-start rollback behavior.
- [ ] **P3.6** Add integration, concurrency, abuse, and failure-injection tests.
- [ ] **P3.7** Build deployment screens for environment state, selected version, promotion/rollback confirmations, and audit history.

### Phase 4 — Model evaluation and promotion policies (0/5 tasks complete)

- [ ] **P4.1** Define evaluation datasets, evaluator versions, metric semantics, and provenance.
- [ ] **P4.2** Store reproducible evaluation runs and threshold policies.
- [ ] **P4.3** Enforce evaluation policies on production promotion with audited overrides.
- [ ] **P4.4** Add boundary, reproducibility, and policy-bypass tests.
- [ ] **P4.5** Build evaluation screens for inputs, metric/threshold results, provenance, and promotion eligibility.

### Phase 5 — Observability and operational controls (0/6 tasks complete)

- [ ] **P5.1** Add structured correlated logs and sensitive-field redaction.
- [ ] **P5.2** Record request, latency, error, runtime-health, resource, and cost signals.
- [ ] **P5.3** Define data-quality and drift signals for the supported model contract.
- [ ] **P5.4** Add alerts, retention controls, and cost limits.
- [ ] **P5.5** Add dashboards/runbooks and telemetry/alert/retention tests.
- [ ] **P5.6** Build operator dashboards for health, latency, errors, drift, cost, alerts, and stale/empty data states.

### Phase 6 — AWS deployment and delivery (0/6 tasks complete)

- [ ] **P6.1** Add infrastructure as code for isolated staging/production resources and least-privilege IAM.
- [ ] **P6.2** Build and scan pinned application/runtime images.
- [ ] **P6.3** Add managed secrets, rotation, backups, and recovery controls.
- [ ] **P6.4** Add CI/CD with migration ordering, staged rollout, health gates, and rollback artifacts.
- [ ] **P6.5** Document and locally validate deployment/rollback workflows.
- [ ] **P6.6** Package and deliver the frontend with isolated environment configuration, safe cache rules, and browser security headers.

### Phase 7 — Production readiness and release (0/6 tasks complete)

- [ ] **P7.1** Complete security/privacy review, threat model, dependency scan, and authorization audit.
- [ ] **P7.2** Verify load, concurrency, latency, resources, and graceful degradation against agreed targets.
- [ ] **P7.3** Exercise backup restoration, migration recovery, and deployment/incident recovery.
- [ ] **P7.4** Complete user/operator documentation, known limitations, ownership, and runbooks.
- [ ] **P7.5** Review evidence, release blockers, rollback plan, and explicit approval criteria.
- [ ] **P7.6** Verify browser journeys, accessibility, responsive behavior, and UI performance.

## Implementation vs verification

Current working-tree implementation includes model metadata/version endpoints, deployment selection/history, caller-supplied metric threshold records, database readiness, deployment optimistic locking, and migrations 0002–0004. Existing work was preserved and regression-tested on temporary SQLite. All working-tree changes remain uncommitted. The only committed application feature is the earlier registry scaffold in commit `fceb4fd`; the earlier repository setup is commit `b7d7578`.

The current implementation is not a production MLOps service: it does not store/inspect artifacts, execute model evaluations, serve predictions, authenticate/authorize users, isolate tenants, monitor inference, configure alerts, or deploy to AWS. No frontend application exists; UI design, registry/deployment/evaluation/operations screens, frontend delivery, and browser acceptance remain unchecked required work.

## Verification evidence

- 2026-10-19 — Static repository, Git, configuration, migration, and source inspection completed. Confirmed clean project venv install via `python -m pip install -e ".[dev]"`; initial package-index DNS restriction was resolved after approval for network access.
- 2026-10-19 — `.venv\Scripts\python -m pytest -vv`: 16 passed against FastAPI 0.147.0 and Starlette 1.9.0. Coverage includes registry, evaluation, deployment transitions/history, initial insert conflicts, SQLite optimistic concurrency, readiness/OpenAPI, secret masking, production settings validation, error sanitization/header preservation, engine disposal, and migration upgrade/check/downgrade/upgrade on a temporary database.
- 2026-10-19 — Error-handler tests cover preservation of `Allow`, `WWW-Authenticate`, and `Retry-After` headers; all passed.
- 2026-10-19 — `.venv\Scripts\ruff check .`: passed. `.venv\Scripts\ruff format --check .`: passed (25 files). `.venv\Scripts\python -m pip check`: no broken requirements.
- 2026-10-19 — TestClient startup hangs in this Windows environment even with a minimal FastAPI app, so tests invoke route functions directly with isolated SQLite sessions rather than asserting full HTTP transport behavior. This is a verification limitation.
- 2026-10-19 — `asyncio.run(asyncio.sleep(0))` also hangs in the managed Windows shell, so the ASGI lifespan callback is implemented but its execution is not yet verified. The synchronous engine-disposal helper is unit tested.
- Local ignored `messqry.db` exists and was not touched. PostgreSQL and AWS behavior remain unverified.

## Decision log

- **Open before Phase 2/3:** confirm supported artifact format/runtime. Working recommendation: MLflow `pyfunc`.
- **Resolved approach:** OIDC-compatible user login plus scoped service credentials. Specific identity-provider product, detailed role/scope mapping, and credential rotation details remain open before Phase 3 integration.
- **Open before Phase 2:** confirm private S3 artifact storage. Working recommendation: S3 with checksums and scoped IAM.
- **Open before Phase 3:** confirm runtime isolation/resource limits.
- **Open before Phase 4:** confirm evaluation dataset, metric provenance, and policy override behavior.
- **Open before Phase 5/6:** confirm data retention, operating-cost guardrails, AWS region, availability, RPO, and RTO.

## Work record

- 2026-10-19 — Created the evidence-based phase roadmap and progress tracker after reviewing instructions, two-commit Git history, current diffs, source, migrations, ignored local DB presence, and installed package versions. Phase 0 remains active; no phase is complete.
- 2026-10-19 — Added isolated route and migration regression tests, development dependency/tool configuration, and verified the local baseline. Phase 0 is now complete; Phase 1 has started with its architecture proposal recorded. Identity/authentication remains the next owner decision.
- 2026-10-19 — Owner selected OIDC-compatible user login plus scoped service credentials. Phase 1 authentication strategy is recorded; provider and detailed authorization choices are deferred to Phase 3 integration.
- 2026-10-19 — Preserved headers from HTTP exceptions and added mandatory frontend design, scaffold, screen, delivery, and browser-acceptance tasks across Phases 1–7. Phase counts were updated; frontend implementation has not started.
