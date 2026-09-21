# Codex Local Model Router

**Unofficial Windows compatibility bridge for local Responses-API models in the Codex/ChatGPT Desktop Work model picker.** This is not a replacement for the hosted ChatGPT service and does not grant access to additional tools, accounts, or plans.

## Version 0.2: tool compatibility and fail-closed routing

The router keeps one Codex provider while selecting the actual inference endpoint by model ID. Local inference requests are translated; cloud inference requests retain their original tool protocol.

```text
Codex Desktop / CLI
        |
  loopback router :8831
        |
        +-- configured local model --> tool bridge --> llama-server :8826
        |
        +-- known cloud model ---------------------> ChatGPT Codex backend

Tool calls return to Codex for execution and approval.
The router does not execute shell commands, patches, MCP tools, or plugins.
```

### Supported local tool shapes

| Codex representation | Local model sees | Codex receives back |
| --- | --- | --- |
| `function` | JSON function | Normal function call |
| `namespace` containing functions | Stable, collision-checked function aliases | Original namespace and function name |
| `custom`, including `apply_patch` and freeform tool input | JSON function with one string field, `input` | `custom_tool_call` with the exact raw input |
| Custom and namespaced tool history | Matching function calls/results | Original Codex representation remains in Codex |
| Client-executed `web.run` | Namespaced function through the bridge | Codex executes standalone search |

Both JSON and SSE responses are supported. Custom-tool JSON argument fragments are buffered until valid, then emitted as raw custom-tool input; normal text and function streaming remains incremental. Skill/app/plugin usage instructions are enabled in local metadata. A model must still select the right tools, follow skills and produce valid arguments.

**Not supported:** translating hosted tool implementations such as native `web_search`, `file_search`, hosted code interpreter or image generation into local services. Unsupported tool types and dynamic tool-search history fail explicitly instead of silently disappearing. The bridge does not add vision, bypass approvals, or make every ChatGPT feature available to a local model.

## Important v0.1 routing fix

v0.1 inspected a raw body as JSON and defaulted to the cloud when parsing failed. A compressed local request could therefore reach OpenAI and produce:

```text
The '<local model>' model is not supported when using Codex with a ChatGPT account.
```

v0.2 decodes identity, gzip, deflate and Zstandard request bodies, including chunked HTTP framing, **before routing**. Invalid bodies, missing/unknown model IDs, unsupported local endpoints and compression errors are rejected locally. There is no cloud-inference fallback when a local request fails.

## Requirements

Windows, Python **3.11+**, a compatible Codex Desktop/CLI installation, and an already running local server supporting `GET /v1/models` and `POST /v1/responses`. A chat-completions-only endpoint is insufficient. Runtime dependencies are `httpx` and `zstandard`.

The original setup used Qwen3.8-Flash-Next via Strix Alloy/llama-server, Windows 11, Desktop 26.915.4065.0 and Codex 0.155.0-alpha.9.2. These are compatibility observations, not a guarantee for other releases. See [validation](docs/VALIDATION.md).

## Upgrade an existing installation

Download or clone this release, inspect the scripts, then run:

```powershell
.\Update-CodexToolBridge.ps1
```

The updater recognizes the v0.1 installer layout and the original root-level `~/.codex/hybrid-model-router.py` layout. It backs up existing files, adds the bridge, enables standalone tool mode, refreshes the cache and restarts only router processes belonging to that installation. It does not restart Codex or the model runtime automatically. Restart Codex Desktop after active tasks have finished so running sessions reload tool/feature metadata.

## Fresh installation

From the release checkout:

```powershell
.\Install-CodexLocalModel.ps1
```

Defaults are Qwen3.8-Flash-Next, local API `http://127.0.0.1:8826/v1`, router port `8831`, context `262144`, and the tested template's reasoning levels `low`, `medium`, `xhigh`.

Another compatible local model:

```powershell
.\Install-CodexLocalModel.ps1 -ModelId "my-local-model" -DisplayName "My Local Model" -LocalBaseUrl "http://127.0.0.1:9000/v1" -ContextWindow 131072 -DefaultReasoning low -SupportedReasoning low,medium
```

Reasoning levels must match your model's chat template. They are not universal. The installer does not download a model or start llama-server.

To fetch the installer without cloning, download the release script, inspect it, and execute it. Supporting files are pinned to the same release tag:

```powershell
$installer = Join-Path $env:TEMP "Install-CodexLocalModel.ps1"
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MKM-030/codex-local-model-router/v0.2.0/Install-CodexLocalModel.ps1" -OutFile $installer
# Inspect the downloaded script before executing it.
& $installer
```

## Standalone web search is external, not local

The Codex feature `standalone_web_search` exposes the client-executed `web.run` function rather than the incompatible hosted `web_search` tool. Enabling this tool shape does not itself authorize cloud search in the router.

Search is **opt-in**. It sends search commands **and recent conversation context supplied by Codex** to the ChatGPT Codex search backend and requires valid cloud authorization. A separate authorized cloud model ID is used on this search endpoint, not the local model ID. Local inference is never changed to that cloud model.

```powershell
.\Update-CodexToolBridge.ps1 -AllowCloudSearch -SearchModel "YOUR_AUTHORIZED_CLOUD_MODEL_ID"
```

Fresh installation accepts the same switches. Without opt-in, a search attempt returns a clear error. Search backend availability, account eligibility and future protocol changes can still cause errors; they are not hidden by the bridge.

## Installed files and rollback

Standard installs use `~/.codex/local-model-router/`, containing the router, `tool_bridge.py`, generated config/catalog, diagnostics, uninstall script and saved rollback state. Legacy upgrades preserve their original location.

The original `config.toml` is backed up. Unrelated projects, plugins, credentials and permission settings are not replaced. The updater records only its added standalone-search setting for reversal. Repeated fresh installs preserve the original provider rollback state.

```powershell
& "$HOME\.codex\local-model-router\Uninstall-CodexLocalModel.ps1"
```

For a legacy upgrade, use the timestamped updater backup to restore its router/config/catalog and prior startup entry. Do not run the standard uninstaller against the entire `.codex` directory.

## Diagnostics

```powershell
Invoke-RestMethod http://127.0.0.1:8831/health
Invoke-RestMethod http://127.0.0.1:8831/ready
& "$HOME\.codex\local-model-router\scripts\Test-CodexLocalModel.ps1"
```

`/health` reports router liveness, version, PID and active inference requests. `/ready` additionally checks each local model endpoint and its advertised model ID; it returns 503 while a required backend is unavailable. Rotating `router-events.jsonl` logs contain routing metadata, encoding, tool count, status and duration, **not prompts, tool arguments, outputs or authentication headers**. Treat local diagnostics as private and review them before sharing.

The router binds only to loopback, uses exclusive binding on Windows, strips all non-allowlisted headers for local requests, and never forwards ChatGPT credentials to local inference. Loopback is not a complete isolation boundary: other processes running as you must still be trusted. See [troubleshooting](docs/TROUBLESHOOTING.md).

## Tests

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tests\codex_e2e.py --codex "C:\Path\To\codex.exe" --codex-home "$HOME\.codex"
```

The first suite runs protocol, routing, HTTP and configuration regressions without cloud access. The second uses a deterministic mock model with the **real Codex executor** to test custom patches, skill-file reads and a harmless namespaced MCP fixture. It is not a claim about Qwen's tool-selection quality. The fixture uses only disposable files and explicitly grants the existing Windows Codex sandbox group access to those files; it does not change production permissions.

## Limitations

This integration relies on undocumented/current Codex provider and catalog behavior. Updates can break it. Some features require the native provider identity and will not work through a custom provider. Tool authentication, approvals, installed plugins, model context limits and tool-selection reliability continue to apply. Large flattened tool catalogs consume context. Use client-side Codex compaction for local sessions: local prompts are not forwarded to hosted compaction endpoints.

MIT license. See [architecture](docs/ARCHITECTURE.md), [changelog](CHANGELOG.md), and [validation](docs/VALIDATION.md).
