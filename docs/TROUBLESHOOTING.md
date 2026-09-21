# Troubleshooting

## "Couldn't update model setting"

The Desktop UI writes the selected model with Codex config/batchWrite.

If config.toml is locked by another process, the model can still appear in the picker but selecting it fails with a message similar to:

~~~text
Couldn’t update model setting
failed to persist config.toml
~~~

Run:

~~~powershell
.\scripts\Test-CodexLocalModel.ps1
~~~

If a lock is detected, the diagnostic attempts to report the locking PID/application using Windows Restart Manager.

One observed cause during development was Desktop Commander 0.2.51 holding config.toml open after file inspection. Restarting that worker released the lock.
## Local model shows "high demand"

Do not assume that message actually means load.

During the original Qwen test, llama-server returned HTTP 500 because the catalog advertised reasoning effort minimal while the model template accepted only low, medium, and xhigh.

Codex surfaced that local server error as:

~~~text
We’re currently experiencing high demand, which may cause temporary errors.
~~~

Check your local server logs and make sure SupportedReasoning matches the values your model template accepts.

For Qwen3.8 Flash Next in the tested setup:

~~~text
low
medium
xhigh
~~~

## Model is missing from the picker

1. Confirm the router is listening on port 8831.
2. Confirm your local endpoint is running.
3. Delete ~/.codex/models_cache.json.
4. Fully restart ChatGPT Desktop.
5. Run the diagnostic script.
## Manual GET /v1/models returns 401

A bare browser/PowerShell request to the router's /v1/models may return 401.

That can be expected: model-list requests are forwarded to the ChatGPT Codex backend, which requires the ChatGPT auth headers supplied by Codex Desktop.

Use the diagnostic script or inspect models_cache.json after restarting ChatGPT Desktop.

## Cloud models stop working

Confirm:

- model_provider is hybrid_router;
- the router is running;
- the hybrid provider base URL is http://127.0.0.1:8831/v1;
- your ChatGPT login in Desktop is still valid.

The router must be available even when selecting an OpenAI cloud model because the cloud request passes through it.

## Local server only supports chat/completions

This bridge targets the Responses API used by current Codex.

Your local server must implement POST /v1/responses. A chat/completions-only endpoint needs an additional compatibility layer before it can be used here.
## Router port already in use

Find the process:

~~~powershell
Get-NetTCPConnection -LocalPort 8831 -State Listen |
  Select-Object LocalPort,OwningProcess
~~~

Then inspect it:

~~~powershell
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" |
  Select-Object ProcessId,CommandLine
~~~

Do not kill an unrelated process. Either stop an old router instance or reinstall with a different RouterPort.

## Multiple router processes

The installer stops pythonw.exe processes only when their command line contains the exact installed hybrid-model-router.py path, then starts one fresh instance.

If you started additional copies manually, close them before troubleshooting.

## Restore manually

Every install creates a timestamped config.toml backup.

The safe first option is the included uninstaller. If the install state is damaged, close ChatGPT Desktop, make a copy of your current config, and restore the timestamped backup manually.
