# MessQry AI progress

## Current status

- **Current phase:** Phase 1 — Architecture and production foundations (in progress)
- **Current task:** P1.3 (backend) — Verify ASGI lifespan shutdown in Linux CI.
- **Phase count:** 1 of 8 phases complete. Phase 0 is complete; no claim of production readiness is made.
- **Phase 0 task count:** 6 of 6 required tasks complete.
- **Phase 1 task count:** 9 of 11 required tasks complete; the Overview, API integration, and responsive shell have passed desktop/mobile review. CI is implemented and local dependency audits are clean, but the first workflow run remains pending.
- **Blockers:** P1.3 lifespan verification and P1.5 hosted CI are pending the first GitHub Actions run. A focused lifespan test is added and skips on this managed Windows host because its asyncio/AnyIO runner hangs; the Linux workflow will execute it. Starting a separate Uvicorn process here also stalled before startup logs. The hosted workflow needs a commit/push, which has not been requested. The specific identity-provider product, detailed role/scope mapping, and credential rotation remain open before Phase 3 auth integration. PostgreSQL service/access is not configured; only disposable SQLite was exercised.
- **Next action:** run the GitHub Actions workflow after an authorized commit/push, then resolve any CI failures. The local Vite preview is running at `http://127.0.0.1:5173/` and the API currently shows the clean empty state.

## Progress by phase

### Phase 0 — Repository audit and stabilization (6/6 tasks complete)

- [x] **P0.1** Inventory repository contents, Git history, current branch, ignored local data, and tracked/untracked changes. **Complete:** reviewed `AGENTS.md`, Git history/status/diff, file inventory, config, source, migrations, and local Python dependency versions on 2026-10-19.
- [x] **P0.2** Verify and resolve the reported deployment-history, optimistic-concurrency, and readiness-schema findings. Regression coverage confirms event history, initial deployment insert conflicts -> 409, stale promotion conflicts -> 409 without extra history, and top-level readiness 503 JSON matching OpenAPI.
- [x] **P0.3** Add regression/integration tests for registry, evaluation, deployment, readiness, and migration behavior. Eight tests exercise route functions with isolated SQLite sessions, OpenAPI/readiness response shape, and migration lifecycle. Direct route tests are used because FastAPI TestClient hangs at startup in this Windows environment, including on a minimal app.
- [x] **P0.4** Make local setup reproducible with declared development dependencies and consistent lint/format configuration. Added the `dev` extra, pytest settings, Ruff rules, and documented clean-environment install/check commands.
- [x] **P0.5** Run checks against a clean disposable SQLite database and record actual results and remaining defects. Results: 8 passed; Alembic upgrade/check/downgrade/upgrade passed; Ruff check and format check passed; `pip check` passed. The ignored `messqry.db` was not used.
- [x] **P0.6** Record UI direction, required workflows/screens, and frontend state. Approved written design direction and navigation were captured; repository inspection found no frontend app or supplied reference image/code files.

**Phase acceptance:** satisfied for the local SQLite scope and verified findings. PostgreSQL-specific behavior, HTTP transport behavior, and production behavior remain unverified and are later-phase work.

### Phase 1 — Architecture and production foundations (9/11 tasks complete)

- [x] **P1.1** Record first-release architecture, trust boundaries, and supported deployment model. Candidate architecture and unresolved choices are documented in `ROADMAP.md`; owner approval is still pending.
- [x] **P1.2** Record the first-release authentication pattern and principal types. Owner selected OIDC-compatible user login plus scoped service credentials; specific provider, detailed role/scope mapping, and credential rotation are Phase 3 integration decisions.
- [ ] **P1.3 (backend)** Harden configuration validation, secret handling, database lifecycle, and structured errors. Configuration validation, masking, error handling, and engine disposal unit coverage pass. **Implemented; verification pending:** focused ASGI lifespan test is skipped on managed Windows due to its async-runner hang and is scheduled to execute in Linux CI.
- [x] **P1.4 (API)** Define versioned API contracts, pagination/error conventions, and compatibility policy. **Complete:** documented v1 compatibility, future cursor pagination, and safe error conventions in `docs/API.md`; existing `/api/v1` and error envelope match the policy.
- [ ] **P1.5 (delivery)** Add lint/type/security checks and CI. **Implemented; verification pending:** GitHub Actions runs backend tests/Ruff/pip-audit and frontend typecheck/tests/build/npm audit. Local suites and both dependency audits pass; the hosted workflow has not run.
- [x] **P1.6 (operations)** Document schema migration, rollback, and environment-specific configuration. **Complete:** README now covers revision inspection, safe local downgrade, forward-only production migration guidance, database target configuration, and backup rehearsal limits.
- [x] **P1.7 (frontend)** Define frontend flows, navigation, visual direction, accessibility/responsive baseline, and stack. Owner-approved direction specifies React, TypeScript, Vite, Tailwind-compatible styling, six product sections, reduced motion, and no invented operational metrics.
- [x] **P1.8 (frontend)** Scaffold React/TypeScript/Vite, Tailwind design tokens, API client/configuration, lint/test/build scripts, and local workflow. Clean `npm ci` and `npm run lint`, `npm test`, `npm run build` pass from `frontend/`; npm reports zero vulnerabilities after upgrading Vitest to 5.0.5.
- [x] **P1.9 (frontend)** Build responsive application shell, navigation, and accessible reusable components. **Complete:** reviewed desktop and mobile layouts in the open browser; verified mobile drawer semantics, focus entering the active navigation item, Escape returning focus to the menu button, and the closed drawer becoming inert.
- [x] **P1.10 (frontend)** Connect documented API data and shared loading/empty/error/success/unavailable states. **Complete:** browser preview loaded the real API's empty state, a temporary test model/version, and returned to empty state after cleanup; the retry/error state was also observed during initial database setup. UI state regression tests pass.
- [x] **P1.11 (frontend)** Deliver the Overview with registry/deployment summaries, available recent activity, lifecycle explainer, and honest telemetry availability. **Complete:** explicit environment configuration, typed activity outcome, compact black/orange surfaces, aligned shared 12-column panel grid, restrained hover feedback, and reduced decorative motion are in place. Browser-reviewed at 1280px desktop and 674px mobile widths; no horizontal overflow. Verification: `npm run lint`, `npm test -- --reporter=dot` (7 passed), `npm run build`, and `git diff --check` passed.

### Phase 2 — Model registry and artifact management (0/6 tasks complete)

- [ ] **P2.1 (backend)** Finalize model/version metadata and immutability rules for the supported package format.
- [ ] **P2.2 (backend)** Implement private artifact storage abstraction and S3 backend.
- [ ] **P2.3 (backend)** Validate manifests, checksums, size limits, and permitted artifact locations.
- [ ] **P2.4 (backend)** Add ownership/authorization checks to model and version operations.
- [ ] **P2.5 (backend)** Add lifecycle, access-control, corruption, and migration regression tests.
- [ ] **P2.6 (frontend)** Build registry screens for model list/search, registration forms, artifact upload/status workflows, version details/history, and empty/loading/error/success states.

### Phase 3 — Secure model serving and deployment lifecycle (0/8 tasks complete)

- [ ] **P3.1 (backend)** Implement the approved serving adapter and prediction API contract.
- [ ] **P3.2 (runtime)** Isolate model workloads and enforce resource/network/filesystem/time limits.
- [ ] **P3.3 (backend/security)** Add authenticated deployment controls and authorization by model/environment.
- [ ] **P3.4 (backend)** Complete deployment transitions, optimistic concurrency, idempotency, and immutable audit history.
- [ ] **P3.5 (runtime)** Add runtime health, graceful shutdown, and failed-start rollback behavior.
- [ ] **P3.6 (backend)** Add integration, concurrency, abuse, and failure-injection tests.
- [ ] **P3.7 (frontend)** Build deployment screens for environment state, selected version, promotion/rollback confirmations, and audit history.
- [ ] **P3.8 (frontend)** Build sign-in, session-expiry, access-denied, and scoped service-credential guidance states for OIDC-compatible login.

### Phase 4 — Model evaluation and promotion policies (0/5 tasks complete)

- [ ] **P4.1 (backend/product)** Define evaluation datasets, evaluator versions, metric semantics, and provenance.
- [ ] **P4.2 (backend)** Store reproducible evaluation runs and threshold policies.
- [ ] **P4.3 (backend/security)** Enforce evaluation policies on production promotion with audited overrides.
- [ ] **P4.4 (backend)** Add boundary, reproducibility, and policy-bypass tests.
- [ ] **P4.5 (frontend)** Build evaluation screens for inputs, version comparisons, metric/threshold results, provenance, and promotion eligibility.

### Phase 5 — Observability and operational controls (0/6 tasks complete)

- [ ] **P5.1 (backend)** Add structured correlated logs and sensitive-field redaction.
- [ ] **P5.2 (backend/operations)** Record request, latency, error, runtime-health, resource, and cost signals.
- [ ] **P5.3 (backend/ML)** Define data-quality and drift signals for the supported model contract.
- [ ] **P5.4 (backend/operations)** Add alerts, retention controls, and cost limits.
- [ ] **P5.5 (backend/operations)** Add dashboards/runbooks and telemetry/alert/retention tests.
- [ ] **P5.6 (frontend)** Build operator dashboards for health, latency, errors, drift, cost, alerts, and stale/empty data states with labeled units/time ranges and pointer/keyboard chart inspection.

### Phase 6 — AWS deployment and delivery (0/6 tasks complete)

- [ ] **P6.1 (infrastructure/security)** Add infrastructure as code for isolated staging/production resources and least-privilege IAM.
- [ ] **P6.2 (delivery/security)** Build and scan pinned application/runtime images.
- [ ] **P6.3 (infrastructure/security)** Add managed secrets, rotation, backups, and recovery controls.
- [ ] **P6.4 (delivery)** Add CI/CD with migration ordering, staged rollout, health gates, and rollback artifacts.
- [ ] **P6.5 (operations)** Document and locally validate deployment/rollback workflows.
- [ ] **P6.6 (frontend)** Package and deliver the frontend with isolated environment configuration, safe cache rules, and browser security headers.

### Phase 7 — Production readiness and release (0/6 tasks complete)

- [ ] **P7.1 (security)** Complete security/privacy review, threat model, dependency scan, and authorization audit.
- [ ] **P7.2 (operations)** Verify load, concurrency, latency, resources, and graceful degradation against agreed targets.
- [ ] **P7.3 (operations)** Exercise backup restoration, migration recovery, and deployment/incident recovery.
- [ ] **P7.4 (operations)** Complete user/operator documentation, known limitations, ownership, and runbooks.
- [ ] **P7.5 (release)** Review evidence, release blockers, rollback plan, and explicit approval criteria.
- [ ] **P7.6 (frontend)** Verify browser journeys, accessibility, responsive behavior, and UI performance.

## Implementation vs verification

Current implementation includes model metadata/version endpoints, deployment selection/history, caller-supplied metric threshold records, database readiness, deployment optimistic locking, and migrations 0002–0004, committed with the roadmap in `2ae9f4e`. The current frontend and its documentation updates are uncommitted. Repository setup is in commit `b7d7578` and the original registry scaffold is in `fceb4fd`.

The current implementation is not a production MLOps service: it does not store/inspect artifacts, execute model evaluations, serve predictions, authenticate/authorize users, isolate tenants, monitor inference, configure alerts, or deploy to AWS. The frontend scaffold and first Overview implementation now exist, using registry API data and explicit unavailable states for unsupported operational telemetry. Registry/deployment/evaluation/operations workflows, production delivery, and browser acceptance remain required work.

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
- 2026-10-19 — Recorded approved visual direction, six-section navigation, first-release frontend milestone, and the absence of local reference images/snippets. React/TypeScript/Vite/Tailwind direction approved; Phase 1 now has 11 required tasks.
- 2026-10-19 — Added the React/TypeScript/Vite/Tailwind console shell and Overview with API-backed registry/deployment/evaluation summaries, a lifecycle guide, and unavailable telemetry states. Added UI coverage for empty/error/API-fed states. Lint, 4 UI tests, and production build pass; browser preview inspection remains pending because of the in-app browser loopback URL policy.
- 2026-10-19 — `npm ci` completed from the checked-in lockfile and reported zero vulnerabilities. `.venv\Scripts\python -m pytest -q` passed (16 tests); `git diff --check` passed. `npm audit` requested without escalation could not reach the npm registry; `npm ci` performed its audit successfully.
- 2026-10-19 — Fixed event version attribution by resolving each event's target version ID and normalized timezone-naive SQLite timestamps to UTC before sorting/display. Made the closed mobile drawer inert and hidden; opening focuses the active nav item, Escape/close restores focus, and keyboard focus stays within the open drawer. New regression tests cover historical v1 while v3 is current, UTC/offset timestamps, and mobile focus behavior; six UI tests pass.
- 2026-10-19 — Added `docs/API.md` defining v1 compatibility, future cursor pagination, and stable safe error responses; documented local and production migration operations in README; added GitHub Actions backend/frontend checks and dependency audits. Verification: backend pytest 16 passed, Ruff check/format passed, frontend TypeScript lint passed, 6 UI tests passed, production build passed, and `git diff --check` passed. CI/pip-audit and live browser-to-API verification remain pending; API startup on 127.0.0.1:8000 reported address already in use.
- 2026-10-19 — Completed P1.9 browser review at desktop and mobile sizes. Mobile drawer opens as a dialog, receives focus on the active route, closes on Escape with focus restored, and is inert when closed.
- 2026-10-19 — Connected the open Vite preview to FastAPI using `messqry-preview-20261019.db`, a separate ignored disposable SQLite database migrated to head. Verified Overview API reads with an empty database and a temporary model/version, then removed the temporary record and restored the clean empty state. Reviewed desktop and 390px mobile Overview; mobile document width matched the viewport (390px) with no horizontal overflow. The original ignored `messqry.db` was not used.
- 2026-10-19 — Implemented explicit deployment-environment labeling, typed activity outcome, and a more neutral dashboard with shared panel columns and an 11px minimum compact type size. TypeScript lint, 7 UI tests, production build, and diff checks pass; updated mobile-width browser review found no horizontal overflow. Desktop review remains pending.
- 2026-10-19 — Applied the requested orange-and-black visual system, stronger white card borders, layered shadows, and pointer hover lift; changed the dashboard to a wider/narrower panel rhythm on desktop and corrected panel stacking under 760px. Replaced the navigation, lifecycle, metric, and status icon set with more domain-specific symbols. Browser preview at 674px confirms stacked lower panels and no horizontal overflow. Verification: TypeScript lint passed, 7 UI tests passed, production build passed, and `git diff --check` passed. Desktop-width visual review remains pending.
- 2026-10-19 — Fixed the mobile brand link so it closes the drawer and returns focus. Consolidated the competing style layers into one compact black/orange system, a shared 12-column panel grid, quieter white borders/shadows, restrained hover lift, neutral icon presentation, and static lifecycle connectors; updated the navigation/metric/lifecycle/status icons. Regression test covers brand-link drawer close/focus. Verification: frontend lint passed, 7 UI tests passed, production build and `git diff --check` passed. Browser review at 1280px confirmed aligned panel boundaries and orange active/action states; the prior 674px preview confirmed responsive stacking.
- 2026-10-19 — Restored a clearer orange brand treatment and simplified the sidebar: removed the nonfunctional workspace dropdown, decorative section badge, fake model count dot, and unauthenticated profile block; replaced them with a compact wordmark, honest workspace label, text-first status footer, and a clear orange active-navigation state. Re-reviewed at 1280px desktop and 674px mobile. `PROGRESS.md` now records P1.11 complete (9/11 Phase 1 tasks complete).
- 2026-10-19 — Completed the frontend review fixes: explicit deployment label independent of Vite mode; typed success/failure/neutral activity markers; simpler operational language and neutral surfaces with semantic colors; shared panel grid columns and minimum 11px supporting text. Added `frontend/.env.example`, documented its public build-time setting, and regression-tested a staging API label in production build mode and a passing evaluation for `failed-payment-detector`. Verification: frontend TypeScript lint passed, 7 UI tests passed, production build passed, `git diff --check` passed, and the open preview showed no horizontal overflow at a 674px viewport.
- 2026-10-19 — Added `pip-audit` to the dev extra and tightened vulnerable dependency minimums after the first Python audit found outdated local AnyIO, pytest, pip, and setuptools. Updated the environment to AnyIO 4.15.1, pytest 9.1.1, pip 26.2.1, and setuptools 84.0.0. `pip-audit --skip-editable` then reported no known vulnerabilities; `npm audit --audit-level=high` reported zero vulnerabilities. Backend tests (16), Ruff checks, pip check, TypeScript lint, six UI tests, production build, and `git diff --check` pass. Hosted GitHub Actions and explicit lifespan shutdown verification remain open.
- 2026-10-19 — Retried direct ASGI lifespan-context verification after the dependency updates; the managed Windows shell again remained blocked inside `asyncio.run` beyond 10 seconds, so the process was interrupted and P1.3 remains unchecked. The running Uvicorn preview itself completed application startup successfully.
- 2026-10-19 — Tried a focused AnyIO pytest for ASGI lifespan shutdown; it stalled in the same managed Windows async runner before completing. Removed the non-terminating test attempt. Re-ran the backend suite: 16 passed; Ruff lint passed; Ruff format check found only a trailing blank line in `tests/test_config.py`, which was formatted. P1.3 remains implemented but unverified; P1.5 hosted CI also remains pending without a requested commit/push.
- 2026-10-19 — Added a focused ASGI lifespan shutdown regression test that skips on this managed Windows host and runs on Linux CI. Local backend verification: 16 passed, 1 skipped; Ruff check and format passed; pip check passed; `git diff --check` passed. P1.3 and P1.5 remain unchecked until the hosted workflow runs; the working tree remains uncommitted.
