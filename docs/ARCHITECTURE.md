# Architecture: v0.2

The local model produces tool calls; Codex executes them. The router only adapts the wire protocol and routes authorized requests.

## Request-scoped tool map

Each HTTP request has a new `ToolBridge`. Aliases derive from tool kind, namespace and original name, with a readable prefix and a SHA-256 suffix. Names are bounded to 64 characters; duplicates and collisions reject the request. No process-global tool-call map mixes concurrent sessions.

Namespaced functions retain their parameter schema. Custom tools become functions with exactly one `input` string. The custom grammar is not enforced by the model endpoint; Codex's existing tool handler receives and validates the restored raw input. A tool that expects a grammar still requires valid model output.

Conversation history is adapted on every request, including `custom_tool_call_output`, original namespace fields and call IDs. Return paths translate JSON output arrays and SSE item/argument/completion events. Custom JSON string fragments are buffered to prevent emitting escaped/incomplete input as executable tool text. Tool execution, secrets and approval decisions remain in Codex.

## Routing boundary

Raw HTTP framing is validated, then supported content encodings are decoded, then JSON and model IDs are validated. Only exact configured local IDs/aliases are routed locally. Known cloud catalog IDs and explicit cloud-family prefixes are cloud-bound. Unknown/malformed requests never fall back to the cloud.

Cloud inference preserves the original request body and tool format. Local inference is adapted to functions. An unavailable local server produces a local transport error, not a cloud retry. Hosted compaction/token routes reject local model IDs.

Local upstreams are restricted to loopback. Cloud credential forwarding is pinned to the ChatGPT Codex origin. Local headers use an allowlist; a separate local API key may be provided with `apiKeyEnv`, never by reusing the ChatGPT bearer token. Redirects are not followed.

## Standalone search

`features.standalone_web_search=true` plus the provider capability exposes `web.run`. The bridge translates that namespace; Codex executes the search and calls `/alpha/search`. This is a separate external service, not local inference.

The router requires `allowCloudSearch=true` and an authorized `searchModel`. When Codex supplies a local model ID on this external endpoint, only that endpoint's model tag is replaced. Codex's search request also includes recent conversation input; this is documented and opt-in. This does not turn cloud search into an offline capability or bypass account authorization.

## Skills and hosted tools

Local catalogs enable skill, app and plugin usage instructions. This helps the model discover/use available skills; it does not install missing software or prove that the model follows instructions reliably. A skill using an unavailable tool still cannot complete.

Native hosted `web_search`, file search, image generation, computer execution and dynamic tool-search protocol entries are not arbitrarily converted to invented functions. Unsupported representations fail clearly. Client-executed tools with supported function/custom schemas can cross the bridge, subject to actual client capabilities.

## Operational behavior

`/health` identifies the running build and PID. Metadata-only rotating logs show route decisions without storing prompts, outputs or credentials. Request cancellation closes the upstream stream. Exclusive Windows binding prevents two router processes from silently sharing the same port. No application bundle is patched and no session database is rewritten.

## Sources

- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Responses function/custom calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Codex request compression tests](https://github.com/openai/codex/blob/main/codex-rs/core/tests/suite/request_compression.rs)
- [Codex standalone search tool](https://github.com/openai/codex/blob/main/codex-rs/ext/web-search/src/tool.rs)
- [Codex search endpoint](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/search.rs)

References describe upstream behavior; compatibility is limited to versions actually tested here.
