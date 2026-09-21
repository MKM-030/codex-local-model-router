# Architecture

## Problem

Codex Desktop can load custom providers and custom model metadata, but the current Desktop model-selection flow effectively uses one configured provider for the selected model.

That creates a routing conflict:

- OpenAI cloud models need the ChatGPT Codex backend.
- A local model needs a loopback OpenAI-compatible endpoint.
- One global provider cannot point to both endpoints.

## Bridge design

The project configures Codex with one custom provider:

~~~text
model_provider = "hybrid_router"
base_url = "http://127.0.0.1:8831/v1"
~~~

The router then chooses the real upstream based on the model field in each Responses request.
## Request routing

For configured local model IDs:

~~~text
POST /v1/responses
model = local-model-id
    -> local base URL
~~~

For every other model:

~~~text
POST /v1/responses
model = cloud-model-id
    -> https://chatgpt.com/backend-api/codex/responses
~~~

The same mechanism works for streaming and non-streaming Responses API traffic because the router forwards response bytes incrementally.

## Model catalog

Codex requests the provider's model catalog.

The router forwards that request to the authenticated ChatGPT Codex backend, parses the returned catalog, and appends the local catalog entries from local-model-catalog.json.

This preserves the current cloud-model list while making the local model visible in the same picker.
## Authentication boundary

Codex sends its existing ChatGPT authentication headers to the configured provider.

Cloud-bound requests pass through normally.

Local-bound requests explicitly drop authentication/account headers before leaving the router. The local model server therefore never receives the ChatGPT bearer token or account identifier.

## Why not pretend to be the built-in OpenAI provider?

The implementation tested a provider named OpenAI with a base URL ending in /backend-api/codex so Codex would enable first-party-only code paths.

That changed the request behavior and resulted in Cloudflare 403 responses when those first-party assumptions were proxied through localhost.

The working configuration therefore remains an explicit custom provider.

## Consequence

Some internal Codex features check for the literal built-in provider identity rather than only checking API compatibility. Those features may not enable under hybrid_router.

Normal cloud Responses inference and local Responses inference were both tested successfully.
## Config writes

Selecting a default model in the Desktop UI invokes Codex config/batchWrite and persists model plus model_reasoning_effort into config.toml.

The local model metadata therefore must advertise reasoning effort values accepted by the model's own chat template.

The original Qwen template accepted:

- low
- medium
- xhigh

Advertising minimal caused the local server to return HTTP 500.

## Files installed

The Windows installer places these files in ~/.codex/local-model-router:

- hybrid-model-router.py
- router-config.json
- local-model-catalog.json
- install-state.json

It also adds the hybrid_router provider block to ~/.codex/config.toml and creates a per-user Startup VBS that launches the router with pythonw.exe.
