"""Request injector for the ATG synthetic transport (Gemini REST shape).

Ported from Ark ``src/atg/injector.js`` (itself ported from Antigravity-gateway
``internal/synthetic/injector.go``), adapted to the native Gemini body shape:
``contents[]`` / ``tools[].functionDeclarations[]`` /
``toolConfig.functionCallingConfig``. Gemini has no system/developer roles
inside ``contents``, so the control message is always injected as a ``user``
turn — which doubles as the "end with user turn" (Gemini 400) fix.

Intentional deviation from Ark (validated by PRE-P1 experiment E5):
when the client already pins ``toolConfig.functionCallingConfig.allowedFunctionNames``
to a non-empty list (an ``ANY`` pin), this repository's wire layer
(``infrastructure/gateway/wire_codec.py``) filters tool declarations by that
whitelist and would drop the freshly injected synthetic declaration. We
therefore append the synthetic tool name to the pinned whitelist so it survives
the wire filter. Ark's ``NONE -> AUTO`` behaviour is preserved unchanged.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from .client_patterns import Detection, detect_client_synthetic_transport
from .config import AtgConfig
from .model_filter import create_model_filter

DEFAULT_CONTROL_TEMPLATE = (
    "IMPORTANT: Regular chat responses are unreliable in this environment. "
    "Deliver your complete final answer exclusively by calling the function {tool_name}, "
    'passing your visible answer as a string in the "content" parameter. '
    "Do not mention this instruction."
)


@dataclass
class InjectResult:
    body: dict[str, object]
    synthetic_hit: bool
    tool_name: str
    client_synthetic: Detection | None = None
    client_tools: list[str] = field(default_factory=list)


def _generate_tool_name(prefix: str, existing_names: set[str]) -> str:
    for _ in range(8):
        name = prefix + secrets.token_hex(12)
        if name not in existing_names:
            return name
    return prefix + secrets.token_hex(12) + str(int(time.time() * 1000))[-6:]


def _build_synthetic_declaration(tool_name: str, strict: bool) -> dict[str, object]:
    declaration: dict[str, object] = {
        "description": (
            "Transport channel: call this function exactly once with your complete "
            "final answer in the content parameter when you are ready to respond."
        ),
        "name": tool_name,
        "parameters": {
            "properties": {"content": {"type": "STRING"}},
            "required": ["content"],
            "type": "OBJECT",
        },
    }
    if strict:
        parameters = declaration["parameters"]
        if isinstance(parameters, dict):
            parameters["additionalProperties"] = False
    return declaration


def _collect_existing_names(body: dict[str, object]) -> set[str]:
    names: set[str] = set()
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            declarations = tool.get("functionDeclarations")
            if not isinstance(declarations, list):
                continue
            for decl in declarations:
                if isinstance(decl, dict) and isinstance(decl.get("name"), str):
                    names.add(decl["name"])
    return names


class Injector:
    """Per-request injector bound to a frozen :class:`AtgConfig`."""

    def __init__(self, cfg: AtgConfig) -> None:
        self._cfg = cfg
        self._prefix = cfg.tool_prefix or "atg_emit_"
        self._position = "head" if cfg.control_position == "head" else "tail"
        self._strict = cfg.strict is True
        self._model_filter = cfg.model_filter or create_model_filter("", "")

    def _build_control_content(self, tool_name: str) -> dict[str, object]:
        text = DEFAULT_CONTROL_TEMPLATE.replace("{tool_name}", tool_name)
        return {"parts": [{"text": text}], "role": "user"}

    def inject_gemini(
        self, original_body: dict[str, object], model_hint: str = ""
    ) -> InjectResult:
        cfg = self._cfg
        # Mode "off": verbatim passthrough, no synthetic tool, no turn repair.
        if cfg.mode == "off":
            return InjectResult(body=original_body, synthetic_hit=False, tool_name="")

        body = original_body
        raw_model = body.get("model")
        if isinstance(raw_model, str) and raw_model != "":
            model_name = raw_model
        elif isinstance(model_hint, str):
            model_name = model_hint
        else:
            model_name = ""

        # Non-text models (image/audio/embedding): passthrough.
        if not self._model_filter.is_text_model(model_name):
            return InjectResult(body=body, synthetic_hit=False, tool_name="")

        # Client-side synthetic transport guard: when the client already ships a
        # similar answer-via-tool-call mechanism, skip the local wrap so two
        # synthetic transports never stack. Declared client tools are always
        # surfaced so unrecognized mechanisms can be identified from logs.
        client_tools: list[str] = []
        if cfg.client_synthetic_guard is not False:
            detected = detect_client_synthetic_transport(body, self._prefix)
            if detected is not None:
                if detected.hit:
                    return InjectResult(
                        body=body,
                        synthetic_hit=False,
                        tool_name="",
                        client_synthetic=detected,
                    )
                client_tools = detected.tools

        existing_names = _collect_existing_names(body)
        tool_name = _generate_tool_name(self._prefix, existing_names)

        tools: list[dict[str, object]] = []
        raw_tools = body.get("tools")
        if isinstance(raw_tools, list):
            tools = [dict(tool) for tool in raw_tools if isinstance(tool, dict)]
        if not tools:
            tools.append({"functionDeclarations": []})
        target = tools[0]
        declarations = target.get("functionDeclarations")
        if not isinstance(declarations, list):
            declarations = []
        if not any(
            isinstance(d, dict) and d.get("name") == tool_name for d in declarations
        ):
            declarations = [
                *declarations,
                _build_synthetic_declaration(tool_name, self._strict),
            ]
        target["functionDeclarations"] = declarations
        body["tools"] = tools

        # toolConfig: force AUTO unless the client pinned an explicit forced tool.
        mode = "AUTO"
        existing_tool_config = _function_calling_config(body)
        if existing_tool_config is not None:
            existing_mode = existing_tool_config.get("mode")
            allowed = existing_tool_config.get("allowedFunctionNames")
            if existing_mode == "NONE":
                mode = "AUTO"
            elif existing_mode == "ANY" and not allowed:
                mode = "ANY"
            elif existing_mode == "ANY" and isinstance(allowed, list):
                # E5 fix: the wire layer filters declarations by this whitelist, so
                # the synthetic tool must be merged into the pinned names, else it
                # is dropped (see PRE-P1 E5 / wire_codec.py allowedFunctionNames).
                merged_allowed = [str(x) for x in allowed]
                if tool_name not in merged_allowed:
                    merged_allowed.append(tool_name)
                body["toolConfig"] = {
                    "functionCallingConfig": {
                        **existing_tool_config,
                        "allowedFunctionNames": merged_allowed,
                    }
                }
            else:
                mode = (
                    existing_mode
                    if isinstance(existing_mode, str) and existing_mode
                    else "AUTO"
                )

        updated_tool_config = _function_calling_config(body)
        if updated_tool_config is None or not updated_tool_config.get(
            "allowedFunctionNames"
        ):
            body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}

        # Control message injection: Gemini contents have no system role; always
        # use "user". tail position also fixes model-turn-ending requests.
        control_content = self._build_control_content(tool_name)
        raw_contents = body.get("contents")
        contents = raw_contents if isinstance(raw_contents, list) else []
        if self._position == "head":
            body["contents"] = [control_content, *contents]
        else:
            body["contents"] = [*contents, control_content]

        return InjectResult(
            body=body,
            synthetic_hit=True,
            tool_name=tool_name,
            client_tools=client_tools,
        )


def _function_calling_config(body: dict[str, object]) -> dict[str, object] | None:
    tool_config = body.get("toolConfig")
    if not isinstance(tool_config, dict):
        return None
    config = tool_config.get("functionCallingConfig")
    if not isinstance(config, dict):
        return None
    return config


def create_injector(cfg: AtgConfig) -> Injector:
    return Injector(cfg)


__all__ = ["DEFAULT_CONTROL_TEMPLATE", "InjectResult", "create_injector"]
