# Codex Local Model Router

Use a local model in the **Codex / ChatGPT Desktop Work model picker** while keeping your usual OpenAI cloud models available.

A small Windows loopback router sends each inference request to the selected model's backend. Its tool compatibility bridge translates Codex functions, namespaced MCP tools and custom tools such as `apply_patch` for compatible local servers. Codex still executes the tools and enforces its permissions.

> Unofficial integration. This does not replace the hosted ChatGPT service, bypass account limits, or make every ChatGPT feature available locally.

## Requirements

**To install:** Windows, Python **3.11+** with `pythonw.exe`, and a compatible Codex Desktop/CLI installation. The installer installs the Python dependencies `httpx` and `zstandard` when needed. Downloading the files and missing dependencies requires internet access.

**To use local inference later:** a local model server supporting `GET /v1/models` and `POST /v1/responses`. An endpoint that only supports `/v1/chat/completions` is not sufficient.

**Your model server does not have to be running during installation.** You can install the integration now and set up or start the server later. The installer does not download model weights or start, stop, or reconfigure your model server.

## Fresh installation

### 1. Get the installer

Clone the repository, or use **Code → Download ZIP**, extract it and open PowerShell in that folder:

```powershell
git clone https://github.com/MKM-030/codex-local-model-router.git
cd codex-local-model-router
```

Finish any active Codex tasks before changing its configuration. No administrator privileges or running local model are required.

<details>
<summary>Download only the installer instead of cloning</summary>

```powershell
$installer = Join-Path $env:TEMP "Install-CodexLocalModel.ps1"
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/MKM-030/codex-local-model-router/main/Install-CodexLocalModel.ps1" -OutFile $installer
# Inspect the downloaded script before running it.
& $installer
```

Supporting files are downloaded from the source revision specified inside the installer.

</details>

### 2. Install with your model settings

Read the scripts, then install with the defaults:

```powershell
.\Install-CodexLocalModel.ps1
```

The app is **not** restarted automatically. The default configuration is:

| Setting | Default |
| --- | --- |
| Model ID | `Qwen3.8-Flash-Next` |
| Display name | `Qwen3.8 Flash Next - Local` |
| Local model API | `http://127.0.0.1:8826/v1` |
| Router API | `http://127.0.0.1:8831/v1` |
| Context window | `262144` tokens |
| Reasoning levels | `low`, `medium`, `xhigh` |

For another compatible model, pass its settings **instead of** running the default install:

```powershell
.\Install-CodexLocalModel.ps1 `
    -ModelId "my-local-model" `
    -DisplayName "My Local Model" `
    -LocalBaseUrl "http://127.0.0.1:9000/v1" `
    -ContextWindow 131072 `
    -DefaultReasoning low `
    -SupportedReasoning low,medium
```

The ID must match the model your server serves. Context size and reasoning levels must match that server and its chat template; the installer does not change the server's settings. Router and model server must use different ports.

### 3. Start your model when you are ready

Start your model server separately at the configured local API address. Once it has loaded, restart Codex Desktop after active tasks finish, open a new Work chat and select your local model. Existing OpenAI models remain available with your normal account authorization.

There is **no need to reinstall** when the model server becomes available. Keep the router running for both local and cloud sessions. Keep the model server running for local sessions.

## Check the installation

```powershell
# Router is running; does not require a model server.
Invoke-RestMethod "http://127.0.0.1:8831/health"

# Model server is ready; run this after starting your model.
Invoke-RestMethod "http://127.0.0.1:8831/ready"
```

`/health` can return **200** while `/ready` returns **503**. With no model running, that is an installed router waiting for its backend, not a failed installation. No local inference request falls back to a cloud model.

An optional server check during installation is available with `-CheckLocalServer`. `-RunInferenceProbe` also requests a short model response once the endpoint is available and the installer has started the router. Unavailable, loading, authentication-protected or mismatched endpoints produce a warning, not an installation failure. Configuration, dependency, file-write and router-startup errors still stop the installer.

For diagnostics after installation:

```powershell
& "$HOME\.codex\local-model-router\scripts\Test-CodexLocalModel.ps1" -RunInferenceProbe
```

## Tools, skills and web search

The bridge translates normal functions, namespaced functions, custom tools and their multi-turn results, including streamed responses. Skill, app and plugin usage instructions are enabled for the local model. Installed tools, model capability, authorization and Codex approvals still determine what actually works.

**Web search is optional and external.** To enable it at installation, add `-AllowCloudSearch -SearchModel "YOUR_AUTHORIZED_CLOUD_MODEL_ID"`. Codex sends search commands **and recent conversation context** to its cloud search backend. Local inference remains local. Search is disabled in the router unless you opt in.

Hosted image generation, hosted code interpreter, native hosted `web_search`, `file_search`, dynamic tool-search history and local vision are not implemented by this bridge. See [architecture](docs/ARCHITECTURE.md) and [validation](docs/VALIDATION.md) for the exact scope.

## What changes on your computer

Files are installed under `$HOME\.codex\local-model-router`. The installer backs up `config.toml`, adds the router provider, generates the model catalog and creates a per-user Windows-login startup entry. It preserves unrelated Codex settings and records rollback state.

The router binds to loopback only. Local inference receives only allowlisted headers, not your ChatGPT credentials. Cloud requests still require your existing authorization. Logs contain routing metadata, not prompts, tool arguments or credentials.

Use `-SkipAutostart` to install the files and configuration without starting the router or creating its login startup entry. `-RestartChatGPT` explicitly closes/reopens the app; use it only after active work is finished.

## Uninstall

Finish active Codex tasks, then run:

```powershell
& "$HOME\.codex\local-model-router\Uninstall-CodexLocalModel.ps1"
```

Restart Codex Desktop afterward. The uninstaller restores the saved model/provider settings and removes this installation's router files and startup entry. It does not uninstall your local model server.

[Maintenance and updates](docs/MAINTENANCE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [License](LICENSE)
