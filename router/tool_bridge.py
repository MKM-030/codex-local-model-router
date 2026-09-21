"""Request-scoped translation of Codex Responses tools. Never executes tools."""

from __future__ import annotations
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


class BridgeError(ValueError):
    def __init__(
        self, message: str, code: str = "tool_bridge_error", status: int = 400
    ):
        super().__init__(message)
        self.code, self.status = code, status


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Binding:
    kind: str
    namespace: str | None
    name: str
    alias: str


def alias_for(kind: str, namespace: str | None, name: str) -> str:
    if (
        kind == "function"
        and not namespace
        and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
        and not name.startswith("tb_")
    ):
        return name
    key = dumps([kind, namespace, name])
    readable = re.sub(
        r"[^A-Za-z0-9_-]", "_", ((namespace + "__") if namespace else "") + name
    )
    return "tb_" + readable[:43] + "_" + hashlib.sha256(key.encode()).hexdigest()[:16]


class ToolBridge:
    """One instance per HTTP request; no shared cross-session call-id state."""

    def __init__(self) -> None:
        self.bindings: dict[str, Binding] = {}
        self.custom_items: dict[str, Binding] = {}
        self.argument_buffers: dict[str, str] = {}
        self.emitted_custom: set[str] = set()
        self.sequence = 0

    def register(self, kind: str, namespace: str | None, name: str) -> Binding:
        if kind not in ("function", "custom") or not isinstance(name, str) or not name:
            raise BridgeError("Malformed tool definition")
        alias = alias_for(kind, namespace, name)
        b = Binding(kind, namespace, name, alias)
        old = self.bindings.get(alias)
        if old is not None and old != b:
            raise BridgeError("Tool alias collision; request rejected")
        self.bindings[alias] = b
        return b

    def flatten(
        self, tools: list[dict], namespace: str | None = None, ns_description: str = ""
    ) -> list[dict]:
        flat: list[dict] = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise BridgeError("Tool definitions must be objects")
            kind = tool.get("type")
            if kind == "namespace":
                name = tool.get("name")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(tool.get("tools"), list)
                ):
                    raise BridgeError("Invalid namespace tool")
                if namespace:
                    raise BridgeError(
                        "Nested namespaces are not supported by this Codex bridge"
                    )
                flat.extend(
                    self.flatten(tool["tools"], name, tool.get("description") or "")
                )
                continue
            if kind not in ("function", "custom"):
                hint = (
                    " Enable features.standalone_web_search=true and supports_standalone_web_search=true to expose client-executed web.run."
                    if kind in ("web_search", "web_search_preview")
                    else ""
                )
                raise BridgeError(
                    f"Hosted/unsupported tool type {kind!r} cannot run on this local endpoint.{hint}",
                    "unsupported_tool_type",
                )
            b = self.register(kind, namespace, tool.get("name"))
            qualified = f"{namespace}.{b.name}" if namespace else b.name
            description = tool.get("description") or ""
            if namespace:
                description = f"Codex tool {qualified}. {ns_description}\n{description}"
            if kind == "function":
                item = copy.deepcopy(tool)
                item["name"] = b.alias
                item["description"] = description
                item.pop("namespace", None)
                item.pop("defer_loading", None)
                item.pop("output_schema", None)
            else:
                item = {
                    "type": "function",
                    "name": b.alias,
                    "description": description
                    + "\nPass the complete raw tool input as the JSON string field input. Codex executes and validates it. Do not wrap it in Markdown fences.",
                    "strict": False,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "Exact raw input for " + qualified,
                            }
                        },
                        "required": ["input"],
                        "additionalProperties": False,
                    },
                }
            flat.append(item)
        names = [t["name"] for t in flat]
        if len(names) != len(set(names)):
            raise BridgeError("Duplicate tool definitions in request")
        return flat

    def to_local_item(self, item: dict) -> dict:
        out = copy.deepcopy(item)
        kind = out.get("type")
        if kind in ("function_call", "custom_tool_call"):
            original_kind = "custom" if kind == "custom_tool_call" else "function"
            b = self.register(original_kind, out.get("namespace"), out.get("name"))
            out["name"] = b.alias
            out.pop("namespace", None)
            if original_kind == "custom":
                if not isinstance(out.get("input"), str):
                    raise BridgeError("Custom tool history input is not a string")
                out["type"] = "function_call"
                out["arguments"] = dumps({"input": out.pop("input")})
        elif kind == "custom_tool_call_output":
            out["type"] = "function_call_output"
            out.pop("name", None)
            out.pop("namespace", None)
        elif kind == "function_call_output":
            out.pop("namespace", None)
            out.pop("name", None)
        elif kind in ("tool_search_call", "tool_search_output"):
            raise BridgeError(
                "Dynamic tool-search history requires a new local session or explicit history migration; it is not silently dropped",
                "unsupported_history",
            )
        out.pop("encrypted_function_args", None)
        out.pop("internal_chat_message_metadata_passthrough", None)
        return out

    def prepare(self, payload: dict) -> dict:
        out = copy.deepcopy(payload)
        if isinstance(out.get("input"), list):
            out["input"] = [
                self.to_local_item(i) if isinstance(i, dict) else i
                for i in out["input"]
            ]
        tools = out.get("tools", [])
        if not isinstance(tools, list):
            raise BridgeError("tools must be an array")
        out["tools"] = self.flatten(tools)
        choice = out.get("tool_choice")
        if isinstance(choice, dict):

            def adapt(c: dict) -> dict:
                c = copy.deepcopy(c)
                if c.get("type") in ("function", "custom"):
                    b = self.register(c["type"], c.get("namespace"), c.get("name"))
                    c.update(type="function", name=b.alias)
                    c.pop("namespace", None)
                elif c.get("type") == "allowed_tools":
                    c["tools"] = [adapt(t) for t in c.get("tools", [])]
                else:
                    raise BridgeError("Unsupported tool_choice object")
                return c

            out["tool_choice"] = adapt(choice)
        if not out["tools"] and out.get("tool_choice") == "required":
            raise BridgeError("tool_choice=required but there are no supported tools")
        return out

    @staticmethod
    def raw_input(arguments: Any) -> str:
        try:
            obj = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (ValueError, TypeError) as exc:
            raise BridgeError(
                "Local model emitted invalid JSON for a custom-tool wrapper",
                "invalid_custom_arguments",
                502,
            ) from exc
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("input"), str)
            or set(obj) != {"input"}
        ):
            raise BridgeError(
                "Custom-tool wrapper must contain exactly one string field: input",
                "invalid_custom_arguments",
                502,
            )
        return obj["input"]

    def restore_item(self, item: dict, *, partial: bool = False) -> dict:
        out = copy.deepcopy(item)
        if out.get("type") != "function_call":
            return out
        b = self.bindings.get(out.get("name"))
        if not b:
            if str(out.get("name", "")).startswith("tb_"):
                raise BridgeError(
                    "Local model returned an unknown tool alias",
                    "unknown_tool_alias",
                    502,
                )
            return out
        out["name"] = b.name
        if b.namespace:
            out["namespace"] = b.namespace
        else:
            out.pop("namespace", None)
        if b.kind == "custom":
            arguments = out.pop("arguments", "")
            out["type"] = "custom_tool_call"
            out["input"] = "" if partial else self.raw_input(arguments)
        return out

    def restore_response(self, response: dict) -> dict:
        out = copy.deepcopy(response)
        if isinstance(out.get("output"), list):
            out["output"] = [
                self.restore_item(i) if isinstance(i, dict) else i
                for i in out["output"]
            ]
        return out

    @staticmethod
    def event_key(event: dict) -> str:
        item = event.get("item") or {}
        return str(
            event.get("item_id")
            or item.get("id")
            or ("index:" + str(event.get("output_index", -1)))
        )

    def custom_finish_events(self, event: dict, arguments: str) -> list[dict]:
        key = self.event_key(event)
        if key in self.emitted_custom:
            return []
        raw = self.raw_input(arguments)
        self.emitted_custom.add(key)
        base = {
            k: event[k]
            for k in ("response_id", "item_id", "output_index")
            if k in event
        }
        if "item_id" not in base and (event.get("item") or {}).get("id"):
            base["item_id"] = event["item"]["id"]
        return [
            dict(base, type="response.custom_tool_call_input.delta", delta=raw),
            dict(base, type="response.custom_tool_call_input.done", input=raw),
        ]

    def transform_event(self, event: dict) -> list[dict]:
        out = copy.deepcopy(event)
        kind = out.get("type", "")
        key = self.event_key(out)
        result: list[dict]
        if kind == "response.output_item.added":
            item = out.get("item", {})
            b = (
                self.bindings.get(item.get("name"))
                if item.get("type") == "function_call"
                else None
            )
            if b and b.kind == "custom":
                self.custom_items[key] = b
                self.argument_buffers[key] = item.get("arguments") or ""
            out["item"] = self.restore_item(item, partial=True)
            result = [out]
        elif (
            kind == "response.function_call_arguments.delta"
            and key in self.custom_items
        ):
            self.argument_buffers[key] = self.argument_buffers.get(key, "") + out.get(
                "delta", ""
            )
            result = []
        elif (
            kind == "response.function_call_arguments.done" and key in self.custom_items
        ):
            args = out.get("arguments", self.argument_buffers.get(key, ""))
            result = self.custom_finish_events(out, args)
        elif kind == "response.output_item.done":
            item = out.get("item", {})
            b = (
                self.bindings.get(item.get("name"))
                if item.get("type") == "function_call"
                else None
            )
            result = []
            if b and b.kind == "custom":
                result.extend(
                    self.custom_finish_events(
                        out, item.get("arguments", self.argument_buffers.get(key, ""))
                    )
                )
            out["item"] = self.restore_item(item)
            result.append(out)
        elif kind in ("response.completed", "response.incomplete") and isinstance(
            out.get("response"), dict
        ):
            out["response"] = self.restore_response(out["response"])
            result = [out]
        else:
            if (
                kind == "response.function_call_arguments.done"
                and out.get("name") in self.bindings
            ):
                b = self.bindings[out["name"]]
                out["name"] = b.name
                if b.namespace:
                    out["namespace"] = b.namespace
            result = [out]
        for e in result:
            if "sequence_number" in event:
                e["sequence_number"] = self.sequence
            self.sequence += 1
        return result

    def translate_sse(self, lines: Iterable[str]) -> Iterable[bytes]:
        data: list[str] = []

        def emit(parts: list[str]) -> Iterable[bytes]:
            raw = "\n".join(parts)
            if raw.strip() == "[DONE]":
                yield b"data: [DONE]\n\n"
                return
            try:
                event = json.loads(raw)
            except ValueError as exc:
                raise BridgeError(
                    "Malformed local SSE JSON", "invalid_sse", 502
                ) from exc
            if not isinstance(event, dict):
                raise BridgeError("SSE data must be an object", "invalid_sse", 502)
            for e in self.transform_event(event):
                typ = str(e.get("type", "message"))
                yield ("event: " + typ + "\ndata: " + dumps(e) + "\n\n").encode("utf-8")

        for line in lines:
            if not line:
                if data:
                    yield from emit(data)
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif line.startswith(":"):
                yield (line + "\n\n").encode("utf-8")
        if data:
            yield from emit(data)
