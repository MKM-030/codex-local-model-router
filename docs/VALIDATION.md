# Validation — 2026-09-21

Environment: Windows 11, Codex 0.155.0-alpha.9.2, Desktop 26.915.4065.0, Python 3.12.10. Local inference: Qwen3.8-Flash-Next IQ4_NL PROJFIX, Strix Alloy llama-server, MTP depth two, 262144-token context.

## Separate evidence levels

1. **48 automated regressions passed.** Namespace/custom request translation, tool choices, history, SSE argument fragments, parallel calls, compression, chunked framing, fail-closed routing, credentials filtering, readiness and configuration preservation.
2. **Real Codex executor with deterministic mock inference passed.** A custom `apply_patch` created a disposable file. Codex read the file and a SKILL.md, cleaned up the file and executed a namespaced MCP echo. Tool results survived the next request's translated history.
3. **Real Qwen plus real Codex executor passed.** Qwen read a skill containing a random marker not present in the user prompt, created a file through custom `apply_patch`, invoked the namespaced MCP echo, read the file and removed it. All six live checks passed. Qwen first chose an inappropriate MCP resource reader, then corrected itself and used the filesystem tool. This is compatibility evidence, not perfect tool-selection reliability.
4. **Real connected services with deterministic mock inference passed.** Codex called its authorized GitHub plugin to read this public repository, then used standalone `web.run` to open example.com. Both returned real service results. This test does not claim that Qwen selected those two calls itself.
5. **Installer lifecycle passed.** Install, reinstall, update and uninstall were exercised in an isolated Codex home. Original TOML settings were restored and first-install rollback state survived reinstall.

## Reproduction

```powershell
python -m unittest discover -s tests -v
python tests\install_roundtrip.py
python tests\codex_e2e.py --codex "C:\Path\To\codex.exe" --codex-home "$HOME\.codex"
python tests\codex_live_local.py --codex "C:\Path\To\codex.exe" --codex-home "$HOME\.codex" --catalog "C:\Path\To\local-model-catalog.json"
```

The live probe runs inference and creates/removes only disposable test files. It creates a temporary Codex profile, preserves the existing config and uses the installed sandbox. The connected-services probe is separately opt-in; read its help before running it.

## Windows sandbox caveat

An entirely fresh temporary Codex home selected an unelevated sandbox on the tested machine and could not access its temporary files. Reusing the existing installed sandbox via `--codex-home` fixed the fixture setup without disabling the sandbox. Patch contents are read back and deleted inside Codex's execution context, because sandbox-created files may not be readable by the parent test process. The deterministic test fails if the actual tool results are missing; a model's final success sentence is not sufficient.

## Scope and remaining limits

This validation does not establish compatibility with every plugin, hosted tool, model or Desktop version. Hosted `web_search` is not executed by llama-server; use client-executed standalone `web.run` with authorized cloud search. Image generation, hosted code interpreter, dynamic tool-search history and local vision are not implemented by this bridge. Permission checks and account limits remain in place.

The reported user's existing thread was inspected read-only: it already selected `hybrid_router` and Qwen. No conversation database was edited, no benchmark task in that thread was rerun, and its history was not deleted.

## Final deployed transport checks

A real Zstandard-compressed request to the deployed router returned HTTP 200 from Qwen3.8-Flash-Next with `BRIDGE_ZSTD_LOCAL_OK`. A separate real Codex request for GPT-6 Astra through the same router returned `BRIDGE_V02_CLOUD_OK` and exited successfully. `/ready` reported the local backend available on port 8826. These checks used the deployed v0.2.0 files, not mock inference.

Reference documentation for the underlying contracts: [Codex configuration](https://developers.openai.com/codex/config-reference), [function calling](https://developers.openai.com/api/docs/guides/function-calling), and [skills](https://developers.openai.com/codex/skills). This project's compatibility logic and test results are independent observations, not an official OpenAI support guarantee.


## v0.2.1 display-encoding regression

50 automated tests passed on Windows PowerShell 5.1. Two new tests execute the actual JSON-read statements from the updater, uninstaller and diagnostics against Unicode payloads, with and without a UTF-8 BOM. Before the fix, all five BOM-less read sites failed; after explicit UTF-8 decoding, all passed. The installer lifecycle additionally preserves Unicode labels, descriptions and catalog filenames across two updates, applies an explicit display-name override, and restores the original TOML on uninstall. These are encoding/configuration checks, not new inference benchmarks.
