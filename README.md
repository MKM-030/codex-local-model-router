# Codex Local Model Router

Add an OpenAI-compatible **local model** to the ChatGPT/Codex Desktop model picker on Windows while keeping the normal OpenAI cloud models available.

> **Unofficial workaround.** This project is not an OpenAI product and relies on current Codex Desktop configuration/provider behavior. App updates can change the internals it depends on.

## What this solves

Codex supports custom model providers, but the Desktop model picker does not currently provide a clean per-model provider selector. If you point the global provider at a local server, cloud models break; if you keep the OpenAI provider, a local model cannot simply use another endpoint.

This project installs a small loopback router that exposes one provider to Codex and routes requests by the selected model:

~~~text
ChatGPT / Codex Desktop
          |
          v
127.0.0.1:8831
Hybrid model router
     |           |
     |           +--> OpenAI cloud models -> ChatGPT Codex backend
     |
     +--> Local model -> your OpenAI-compatible server
                         (for example 127.0.0.1:8826/v1)
~~~

The router also merges the local model metadata into Codex's /models response, so it appears in the normal new-chat model picker.
## Security behavior

Cloud requests keep the ChatGPT authentication headers Codex already sends.

Before a request is forwarded to a local model server, the router strips these sensitive headers:

- Authorization
- chatgpt-account-id
- Cookie
- x-openai-actor-authorization

The router listens on 127.0.0.1 only by default.

## Requirements

- Windows 11
- ChatGPT/Codex Desktop
- Python 3 with pythonw.exe
- A local server implementing the **OpenAI Responses API**
  - GET /v1/models
  - POST /v1/responses
- The local model must already be loaded/running before installation

A server that implements only /v1/chat/completions is not sufficient for this setup.

## Quick install: Qwen3.8 Flash Next

The defaults match the setup this project was originally validated with:

- Model ID: Qwen3.8-Flash-Next
- Local API: http://127.0.0.1:8826/v1
- Context: 262144
- Reasoning: low, medium, xhigh
- Router: http://127.0.0.1:8831/v1
Run PowerShell:

~~~powershell
$installer = Join-Path $env:TEMP "Install-CodexLocalModel.ps1"
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MKM-030/codex-local-model-router/main/Install-CodexLocalModel.ps1" -OutFile $installer
& $installer -RestartChatGPT
~~~

Or clone the repository and run:

~~~powershell
.\Install-CodexLocalModel.ps1 -RestartChatGPT
~~~

After ChatGPT Desktop restarts, open a new chat and select **Qwen3.8 Flash Next - Local**.

## Install another local model

~~~powershell
.\Install-CodexLocalModel.ps1 -ModelId "my-local-model" -DisplayName "My Local Model" -LocalBaseUrl "http://127.0.0.1:9000/v1" -ContextWindow 131072 -DefaultReasoning low -SupportedReasoning low,medium -RestartChatGPT
~~~

The model ID must be the same ID your local /v1/responses endpoint accepts.

Reasoning levels are model/template-specific. Do not advertise a reasoning effort your local chat template rejects.
## What the installer changes

The installer:

1. verifies the local /v1/models endpoint;
2. finds Python and installs httpx if needed;
3. copies the router to ~/.codex/local-model-router/;
4. generates router-config.json and local-model-catalog.json;
5. backs up ~/.codex/config.toml;
6. preserves your existing Codex config and changes only the active model_provider;
7. adds the model_providers.hybrid_router provider block;
8. creates a hidden Windows-login startup entry for the router;
9. removes models_cache.json so Codex refreshes the catalog;
10. optionally restarts ChatGPT Desktop.

It does **not** replace your MCP, plugin, project, sandbox, or other Codex settings.

The previous top-level model, reasoning setting, provider, and any pre-existing hybrid_router block are recorded in install-state.json for uninstall.

## Uninstall

~~~powershell
& "$HOME\.codex\local-model-router\Uninstall-CodexLocalModel.ps1" -RestartChatGPT
~~~

Or, from a cloned repository, run .\Uninstall-CodexLocalModel.ps1.

The uninstaller restores the values saved at install time, removes the router startup entry, clears the model cache, and removes the installed router files.

A fresh backup of the config is created before uninstall as well.

## Diagnostics

~~~powershell
& "$HOME\.codex\local-model-router\scripts\Test-CodexLocalModel.ps1"
~~~

Run an actual local inference probe too:

~~~powershell
& "$HOME\.codex\local-model-router\scripts\Test-CodexLocalModel.ps1" -RunInferenceProbe
~~~

The diagnostic checks:

- whether config.toml is writable/unlocked;
- which process owns a lock when possible;
- whether the router port is listening;
- whether the local model API is reachable;
- whether the model is present in Codex's model cache;
- optionally, whether /v1/responses can complete a local request.

See [Troubleshooting](docs/TROUBLESHOOTING.md) for the issues found during the original implementation.

## Tested environment

Originally validated on **September 21, 2026** with Windows 11, ChatGPT/Codex Desktop 26.915.4065.0, Codex CLI/app-server 0.155.0-alpha.9.2, a local llama-server/Strix Alloy endpoint, and Qwen3.8-Flash-Next with a 262144-token context.
The following were tested end-to-end:

- OpenAI cloud inference through the router
- dynamic OpenAI model-catalog refresh plus local-model merge
- local Qwen /v1/responses inference through the same provider
- model selection metadata in the Codex cache
- Codex config/batchWrite for switching between cloud and local defaults
- installer and uninstaller against an isolated Codex home

## Known limitation

Because Codex sees hybrid_router as a custom provider, internal features that explicitly require the built-in provider identity openai may not activate even though normal cloud inference works. See [Architecture](docs/ARCHITECTURE.md).

This is a compatibility bridge, not an official plugin API.

## Why low / medium / xhigh?

During the original Qwen setup, Codex initially advertised minimal. The model's Jinja chat template rejected that value and returned HTTP 500:

~~~text
Unexpected reasoning effort minimal.
Supported types are xhigh (default), medium, and low.
~~~

Codex surfaced that server error as the much less useful message "high demand". Advertising only reasoning levels actually accepted by the local model fixed the issue.
## Repository layout

~~~text
Install-CodexLocalModel.ps1       Windows installer
Uninstall-CodexLocalModel.ps1     Safe rollback/uninstaller
router/hybrid-model-router.py     Request router + catalog merger
scripts/Test-CodexLocalModel.ps1  Diagnostics
scripts/Get-FileLockOwner.ps1     Windows Restart Manager lock lookup
examples/                          Example config/catalog files
docs/                              Architecture and troubleshooting
~~~

## Contributing

Issues and PRs are welcome, especially for other Responses-compatible servers, additional model templates, macOS/Linux startup helpers, and ways to reduce reliance on current Codex provider internals.

## License

MIT. See [LICENSE](LICENSE).
