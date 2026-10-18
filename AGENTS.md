# MessQry AI — Codex instructions

## Project direction

MessQry AI is intended to help teams register, deploy, monitor, evaluate, and safely update machine-learning models. The initial product should prioritize a dependable, understandable model lifecycle over broad automation. The proposed stack is Python, FastAPI, Docker, MLflow, PostgreSQL, and AWS; treat it as a direction, not an existing implementation.

## Model roles

- The main Codex thread is the coordinator: clarify scope, plan work, make decisions, integrate results, and report back. The project config selects `gpt-6.1-sol` for this role.
- When delegation materially helps, use the project-scoped `luna_worker` agent for bounded implementation, exploration, or review tasks. It is configured to use `gpt-6-luna`.
- Keep delegated tasks narrow and independent. Avoid parallel edits to the same files. The coordinator owns final integration and verification.
- Do not intentionally select a different model for delegated work unless the user asks to change this setup. These are project defaults and instructions, not an access-control boundary; explicit app or spawn overrides can supersede them.

## Working agreements

- Inspect the relevant files and existing conventions before changing code. This repository may still be a blank scaffold; do not assume planned components already exist.
- Keep changes focused on the requested outcome. Preserve existing behavior unless the user asks to change it.
- For product and architecture decisions, distinguish confirmed requirements from assumptions. Prefer a small, useful first release over implementing the entire long-term SaaS vision at once.
- Do not add dependencies, infrastructure, tests, or unrelated documentation without a clear need for the requested work.
- Do not run tests or other verification commands unless the user asks for verification. If verification is requested, report exactly what ran and its result.
- Never commit, push, deploy, publish, or write to an external service unless the user explicitly asks.
- Keep credentials, tokens, model artifacts, and customer data out of source control and logs.
