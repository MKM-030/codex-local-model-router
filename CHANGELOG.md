# Changelog

## 0.2.0 — 2026-09-21

- Translate namespaced function tools to stable, collision-checked local aliases and restore their original Codex identities.
- Wrap freeform custom tools in a JSON function and translate calls, streamed arguments and history back to Codex custom-tool items.
- Keep all tool execution, permissions and account authorization in Codex. The router never executes tool code.
- Enable skill, plugin and app usage instructions in local model metadata.
- Support client-executed standalone `web.run` with explicit cloud-search opt-in and a separately configured authorized search model.
- Decode gzip, deflate and Zstandard request bodies, including chunked HTTP framing, before choosing the upstream.
- Reject invalid requests and unknown model IDs instead of falling back to cloud inference.
- Add `/health` for router liveness and `/ready` for local upstream readiness.
- Return `local_backend_unavailable` (503) or `local_backend_timeout` (504) instead of an opaque local connection error.
- Strip non-allowlisted headers from local requests, enforce loopback binding and add metadata-only rotating diagnostics.
- Add an updater for both the original root-level layout and installer-managed layout, preserve first-install rollback state, and add regression/integration tests.

See `docs/VALIDATION.md` for the distinction between deterministic protocol tests, real Codex execution, connected cloud tools and real-model validation.

## 0.1.0

Initial model-selection router. This version did not translate custom/namespaced tools and could misroute compressed requests. Upgrade to 0.2.0 before using local agent tools.
