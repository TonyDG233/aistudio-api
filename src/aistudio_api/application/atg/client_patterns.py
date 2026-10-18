"""Client-side synthetic transport detector.

Ported from Ark ``src/atg/clientPatterns.js``. Scans an incoming native Gemini
request for signs that the CLIENT already ships a similar answer-via-tool-call
bypass mechanism (Tavern anti-truncation extensions and the like). When
detected, the local ATG wrap is skipped for that request so two synthetic
transports never stack.

Signals, extracted from tool declarations first and foremost:
  1. a declared tool whose name uses the local synthetic prefix (atg_emit_*)
  2. a declared tool with answer/response intent in its description plus a
     content-like STRING parameter
  3. a tool force-pinned via toolConfig.functionCallingConfig.allowedFunctionNames
     that also carries the content-like shape
  4. history evidence: the same content-shaped tool already called by the model
     and answered via functionResponse inside body.contents
  5. control-instruction phrasing ("deliver your complete final answer ...")
     anywhere in systemInstruction or the recent text parts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTENT_PARAM_NAMES = frozenset(
    {
        "answer",
        "body",
        "content",
        "final",
        "message",
        "output",
        "reply",
        "response",
        "text",
    }
)

DECLARATION_DESC_PATTERN = re.compile(
    r"(complete|final|entire|whole)[\s\S]{0,60}?(answer|response|reply|output)"
    r"|(call|invoke|use)[\s\S]{0,50}?(function|tool)[\s\S]{0,60}?(content|answer|response)"
    r"|(answer|response|reply)[\s\S]{0,40}?via[\s\S]{0,20}?(tool|function)",
    re.IGNORECASE,
)

TOOL_NAME_PATTERN = re.compile(
    r"(emit|deliver|transport|bypass|relay|output|respond|reply|answer|say|speak|send|write|final)",
    re.IGNORECASE,
)

INSTRUCTION_PATTERN = re.compile(
    r"(deliver|provide|give|output|write|put)[\s\S]{0,80}?(complete|final|entire|whole)[\s\S]{0,40}?(answer|response|reply|text)"
    r"|(call|invoke)[\s\S]{0,80}?(function|tool)[\s\S]{0,80}?(content|answer)",
    re.IGNORECASE,
)

MAX_SCANNED_PARTS = 60
MAX_SCANNED_TEXT = 4000


@dataclass
class Detection:
    """Result of the client-synthetic scan (Ark's plain object)."""

    hit: bool
    name: str = ""
    reason: str = ""
    tools: list[str] = field(default_factory=list)


def _collect_declarations(body: dict[str, object]) -> list[dict[str, object]]:
    declarations: list[dict[str, object]] = []
    tools = body.get("tools")
    if not isinstance(tools, list):
        return declarations
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        raw_decls = tool.get("functionDeclarations")
        if not isinstance(raw_decls, list):
            continue
        for declaration in raw_decls:
            if isinstance(declaration, dict) and isinstance(
                declaration.get("name"), str
            ):
                declarations.append(declaration)
    return declarations


def _declaration_shape(declaration: dict[str, object]) -> tuple[bool, bool, str, bool]:
    params = declaration.get("parameters")
    properties: list[str] = []
    required: list[str] = []
    if isinstance(params, dict):
        raw_props = params.get("properties")
        if isinstance(raw_props, dict):
            properties = [str(name) for name in raw_props]
        raw_required = params.get("required")
        if isinstance(raw_required, list):
            required = [str(name) for name in raw_required]
    content_params = [
        name for name in properties if name.lower() in CONTENT_PARAM_NAMES
    ]
    content_param = len(content_params) > 0
    content_param_required = any(name in required for name in content_params)
    description = declaration.get("description")
    description = description if isinstance(description, str) else ""
    name = declaration.get("name")
    name_hit = bool(isinstance(name, str) and TOOL_NAME_PATTERN.search(name))
    return content_param, content_param_required, description, name_hit


def detect_client_synthetic_transport(
    body: object, prefix: str = "atg_emit_"
) -> Detection | None:
    """Return a :class:`Detection` (hit or declared-tools) or ``None``."""
    if not isinstance(body, dict):
        return None

    declarations = _collect_declarations(body)
    tool_names = [
        str(d.get("name")) for d in declarations if isinstance(d.get("name"), str)
    ][:8]

    def make_hit(name: str, reason: str) -> Detection:
        return Detection(hit=True, name=name or "", reason=reason, tools=tool_names)

    # 1. Local synthetic prefix declared by the client itself.
    for declaration in declarations:
        name = declaration.get("name")
        if isinstance(name, str) and name.startswith(prefix):
            return make_hit(name, "prefixed-name")

    forced_names: list[str] = []
    tool_config = body.get("toolConfig")
    if isinstance(tool_config, dict):
        config = tool_config.get("functionCallingConfig")
        if isinstance(config, dict):
            raw_allowed = config.get("allowedFunctionNames")
            if isinstance(raw_allowed, list):
                forced_names = [str(x) for x in raw_allowed]

    # 2/3. Answer-intent declaration or a forced content-shaped tool.
    for declaration in declarations:
        content_param, _req, description, name_hit = _declaration_shape(declaration)
        desc_hit = len(description) > 0 and DECLARATION_DESC_PATTERN.search(description)
        name = declaration.get("name")
        forced_hit = isinstance(name, str) and name in forced_names
        if content_param and desc_hit:
            return make_hit(str(name or ""), "declaration-intent")
        if content_param and name_hit and forced_hit:
            return make_hit(str(name or ""), "forced-content-tool")

    # 4. History evidence: the content-shaped tool was already called and answered
    #    inside contents (the client runs its own synthetic loop).
    contents = body.get("contents")
    if isinstance(contents, list) and declarations:
        called_names: set[str] = set()
        answered_names: set[str] = set()
        for content in contents:
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                fc = part.get("functionCall")
                if isinstance(fc, dict) and isinstance(fc.get("name"), str):
                    called_names.add(fc["name"])
                fr = part.get("functionResponse")
                if isinstance(fr, dict) and isinstance(fr.get("name"), str):
                    answered_names.add(fr["name"])
        for declaration in declarations:
            name = declaration.get("name")
            if not isinstance(name, str):
                continue
            if name not in called_names or name not in answered_names:
                continue
            content_param, _req, _desc, name_hit = _declaration_shape(declaration)
            if content_param and name_hit:
                return make_hit(name, "history-loop")

    # 5. Control-instruction phrasing in system instruction or recent text parts.
    texts: list[str] = []
    system_instruction = body.get("systemInstruction")
    if isinstance(system_instruction, dict):
        si_parts = system_instruction.get("parts")
        if isinstance(si_parts, list):
            for part in si_parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    if isinstance(contents, list):
        scanned = 0
        for content in contents:
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                scanned += 1
                if scanned > MAX_SCANNED_PARTS:
                    break
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    scanned_chars = 0
    for text in texts:
        if scanned_chars >= MAX_SCANNED_TEXT:
            break
        scanned_chars += len(text)
        if INSTRUCTION_PATTERN.search(text):
            return make_hit("", "instruction-pattern")

    if declarations:
        return Detection(
            hit=False, name="", reason="declared-tools-present", tools=tool_names
        )
    return None


__all__ = [
    "CONTENT_PARAM_NAMES",
    "Detection",
    "detect_client_synthetic_transport",
]
