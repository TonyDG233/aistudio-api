"""Public web constants must have exactly one source (B7-05)."""

from __future__ import annotations

from pathlib import Path

from aistudio_api.infrastructure.gateway import public_web_constants as constants

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
CONSTANTS_PATH = (
    SRC_ROOT
    / "aistudio_api"
    / "infrastructure"
    / "gateway"
    / "public_web_constants.py"
)


def _occurrences(token: str) -> list[Path]:
    found: list[Path] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if token in path.read_text(encoding="utf-8"):
            found.append(path)
    return found


def test_web_api_key_literal_has_one_source() -> None:
    assert _occurrences("AIzaSy") == [CONSTANTS_PATH]


def test_makersuite_service_name_has_one_source() -> None:
    assert _occurrences("MakerSuiteService") == [CONSTANTS_PATH]


def test_rpc_host_literal_has_one_source() -> None:
    assert _occurrences("alkalimakersuite") == [CONSTANTS_PATH]


def test_rpc_urls_are_built_from_one_base() -> None:
    assert constants.rpc_url("ListModels") == constants.LIST_MODELS_URL
    assert constants.LIST_MODELS_URL.startswith(constants.RPC_BASE_URL)
    assert constants.GENERATE_CONTENT_URL.startswith(constants.RPC_BASE_URL)


def test_aistudio_url_helpers_preserve_public_urls() -> None:
    assert constants.AI_STUDIO_ORIGIN == "https://aistudio.google.com"
    assert constants.AI_STUDIO_URL.endswith(
        "?model=gemini-3.7-flash&temporary=true"
    )
    assert constants.AI_STUDIO_URL_FALLBACK.endswith("?temporary=true")
    assert (
        constants.aistudio_new_chat_url(
            auth_user="3", model="gemini-3.8-flash"
        )
        == "https://aistudio.google.com/u/3/prompts/new_chat"
        "?model=gemini-3.8-flash&temporary=true"
    )
    assert (
        constants.aistudio_new_chat_url(auth_user="3", app=True)
        == "https://aistudio.google.com/u/3/app/prompts/new_chat?temporary=true"
    )
    assert (
        constants.aistudio_new_chat_url(auth_user="0", temporary=False)
        == "https://aistudio.google.com/u/0/prompts/new_chat"
    )


def test_public_web_key_is_the_public_browser_key() -> None:
    assert constants.WEB_API_KEY.startswith("AIzaSy")
    assert len(constants.WEB_API_KEY) == 39
