"""Static layer-boundary checks (B7-01/B7-02).

Application must not depend on the HTTP API layer or FastAPI. Infrastructure
must not import application. Domain is the innermost layer and may not import
application/api/infrastructure or FastAPI. Legacy schema/service modules that
violated the boundary must stay deleted.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aistudio_api"

DOMAIN_FORBIDDEN = (
    "aistudio_api.application",
    "aistudio_api.api",
    "aistudio_api.infrastructure",
    "fastapi",
)
APPLICATION_FORBIDDEN = ("aistudio_api.api", "fastapi")
INFRASTRUCTURE_FORBIDDEN = ("aistudio_api.application",)


def _python_files(root: Path) -> Iterator[Path]:
    yield from sorted(root.rglob("*.py"))


def _resolve_import(path: Path, module: str | None, level: int) -> str:
    if not level:
        return module or ""
    rel = path.relative_to(SRC_ROOT).with_suffix("")
    package = list(rel.parts[:-1])
    keep = len(package) - (level - 1)
    if keep < 0:
        return module or ""
    parts = ["aistudio_api", *package[:keep]]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import(path, node.module, node.level)
            if base:
                yield node.lineno, base
            for alias in node.names:
                if alias.name == "*":
                    continue
                yield node.lineno, f"{base}.{alias.name}" if base else alias.name


def _violations(
    root: Path, forbidden: tuple[str, ...]
) -> list[str]:
    found: list[str] = []
    for path in _python_files(root):
        for lineno, imported in _imports(path):
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in forbidden
            ):
                relative = path.relative_to(SRC_ROOT)
                found.append(f"{relative}:{lineno}: {imported}")
    return found


def test_domain_imports_no_outer_layers() -> None:
    violations = _violations(SRC_ROOT / "domain", DOMAIN_FORBIDDEN)
    assert violations == [], "\n".join(violations)


def test_application_imports_no_api_or_fastapi() -> None:
    violations = _violations(SRC_ROOT / "application", APPLICATION_FORBIDDEN)
    assert violations == [], "\n".join(violations)


def test_infrastructure_imports_no_application() -> None:
    violations = _violations(
        SRC_ROOT / "infrastructure", INFRASTRUCTURE_FORBIDDEN
    )
    assert violations == [], "\n".join(violations)


def test_legacy_boundary_violating_modules_are_removed() -> None:
    assert not (SRC_ROOT / "application" / "api_service_gemini.py").exists()
    assert not list((SRC_ROOT / "api").glob("schemas*.py"))
