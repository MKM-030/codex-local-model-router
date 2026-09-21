# Reddit draft

## Title

[Windows] I got a local model into the ChatGPT/Codex Desktop model picker while keeping the normal GPT cloud models

## Body

I wanted to use my local OpenAI-compatible model directly from the normal ChatGPT/Codex Desktop new-chat model picker, without giving up GPT-6 / GPT-5.6 cloud models.

Codex already supports custom providers and custom model catalogs, but I ran into one practical problem: the Desktop model picker effectively works with one configured provider. Point that provider at localhost and the cloud models stop working; keep the OpenAI provider and the local model has nowhere to go.

So I built a small loopback routing bridge for Windows:

~~~text
ChatGPT / Codex Desktop
        |
        v
127.0.0.1:8831
        |
        +--> selected local model -> local /v1/responses endpoint
        |
        +--> everything else -> ChatGPT Codex backend
~~~
It also merges the local model metadata into Codex's model catalog, so the local model appears in the same picker as the normal OpenAI models.

Repo:

https://github.com/MKM-030/codex-local-model-router

There is a PowerShell installer, uninstaller, diagnostics, architecture notes, and the Qwen catalog I used while building it.

My tested setup:

- Windows 11
- ChatGPT/Codex Desktop 26.915.4065.0
- Codex app-server 0.155.0-alpha.9.2
- Qwen3.8-Flash-Next
- 262k context
- llama-server / Strix Alloy
- local API on http://127.0.0.1:8826/v1

The local server needs the OpenAI Responses API, especially POST /v1/responses. A chat/completions-only server is not enough for this exact setup.
One thing I cared about: Codex sends its ChatGPT auth headers to the configured provider. The router strips Authorization, chatgpt-account-id, Cookie, and x-openai-actor-authorization before forwarding anything to the local model server. Cloud-bound requests keep the normal auth headers.

A couple of debugging findings that may save somebody time:

1. Reasoning levels matter. My Qwen template accepted low / medium / xhigh, but not minimal. When minimal was advertised in the custom model metadata, llama-server returned HTTP 500 and Codex misleadingly showed a generic "high demand" error.

2. If the model appears in the picker but selecting it says "Couldn't update model setting", check whether ~/.codex/config.toml is locked. In my case another local tool had an exclusive file handle open, so Codex config/batchWrite could read the config but could not persist the selected model.

3. The router is intentionally still a custom provider. I tested making it look like the built-in OpenAI provider to preserve first-party-only Codex code paths, but that changed request behavior and produced Cloudflare 403s. Normal cloud and local inference both work through the custom-provider setup, but some internal features that explicitly require provider identity "openai" may not activate.
The installer backs up config.toml, preserves the rest of the Codex config, installs the router under ~/.codex/local-model-router, creates a per-user startup entry, clears the model cache, and has a matching uninstaller.

This is definitely an unofficial compatibility bridge and Codex Desktop updates could break it, but I figured it was useful enough to clean up and share.

If anyone tests this with LM Studio, Ollama's Responses API, vLLM, other llama.cpp builds, or another local model, I'd be interested in results/PRs.
