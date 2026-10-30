"""Comprehensive unit tests for unified logging and request dump subsystem."""

from __future__ import annotations

import io
import logging
import os
from unittest.mock import patch

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.infrastructure.utils.log_broadcast import (
    BroadcastHandler,
    DailyLogFileHandler,
)
from aistudio_api.infrastructure.utils.logger import (
    NoisyLoggerFilter,
    UnifiedFormatter,
    dump_request_exchange,
    get_dump_dir,
    get_log_level,
    get_logger,
    is_debug_env_active,
    is_dump_requests_enabled,
    set_dump_dir,
    set_dump_requests,
    set_dump_to_file,
    set_log_level,
    setup_logging,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_unified_formatter_plain_and_colored():
    plain_formatter = UnifiedFormatter(use_color=False)
    color_formatter = UnifiedFormatter(use_color=True)

    record = logging.LogRecord(
        name="aistudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="这是一条中文测试日志",
        args=(),
        exc_info=None,
    )

    plain_output = plain_formatter.format(record)
    assert "[INFO]" in plain_output
    assert "[aistudio.test]" in plain_output
    assert "这是一条中文测试日志" in plain_output

    color_output = color_formatter.format(record)
    assert "\033[32m" in color_output  # Green color code for INFO
    assert "这是一条中文测试日志" in color_output


def test_logger_namespace_hierarchy():
    logger1 = get_logger("server")
    assert logger1.name == "aistudio.server"

    logger2 = get_logger("aistudio.browser")
    assert logger2.name == "aistudio.browser"

    logger3 = get_logger("aistudio")
    assert logger3.name == "aistudio"


def _stderr_handler() -> logging.StreamHandler:
    root = logging.getLogger()
    for handler in root.handlers:
        if (
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, BroadcastHandler)
            and not isinstance(handler, DailyLogFileHandler)
        ):
            return handler
    raise AssertionError("stderr handler not mounted")


def test_setup_logging_mounts_three_sinks_idempotently():
    setup_logging("INFO")
    setup_logging("INFO")

    root = logging.getLogger()
    assert len(root.handlers) == 3
    assert sum(isinstance(h, BroadcastHandler) for h in root.handlers) == 1
    assert sum(isinstance(h, DailyLogFileHandler) for h in root.handlers) == 1
    assert _stderr_handler() is not None


def test_dynamic_log_level_switching():
    setup_logging("INFO")
    assert get_log_level() == "INFO"
    # root/aistudio 固定 DEBUG，保证广播与归档 sink 不漏日志。
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("aistudio").level == logging.DEBUG
    assert _stderr_handler().level == logging.INFO

    set_log_level("DEBUG")
    assert get_log_level() == "DEBUG"
    assert logging.getLogger("aistudio").level == logging.DEBUG
    assert _stderr_handler().level == logging.DEBUG

    set_log_level("WARNING")
    assert get_log_level() == "WARNING"
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("aistudio").level == logging.DEBUG
    assert _stderr_handler().level == logging.WARNING

    set_log_level(logging.ERROR)
    assert get_log_level() == "ERROR"
    assert logging.getLogger("aistudio").level == logging.DEBUG
    assert _stderr_handler().level == logging.ERROR

    # 恢复默认
    set_log_level("INFO")


def test_noisy_namespaces_suppressed_while_root_stays_debug():
    setup_logging("DEBUG")
    try:
        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("aistudio").level == logging.DEBUG
        for name in (
            "websockets",
            "httpx",
            "httpcore",
            "uvicorn.access",
            "playwright",
            "warp",
            "tunnel",
            "cdp",
            "aistudio.warp",
            "aistudio.tunnel",
            "aistudio.browser.cdp",
        ):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
        # get_logger("cdp") actually resolves to aistudio.cdp; its children inherit.
        assert get_logger("cdp").name == "aistudio.cdp"
        assert logging.getLogger("aistudio.cdp").getEffectiveLevel() == logging.WARNING
        assert (
            logging.getLogger("aistudio.cdp.events").getEffectiveLevel()
            == logging.WARNING
        )
        assert not logging.getLogger("aistudio.cdp").isEnabledFor(logging.DEBUG)
        # Business loggers under aistudio must stay at DEBUG.
        for name in ("aistudio.server", "aistudio.browser", "aistudio.routes.logs"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.DEBUG
    finally:
        setup_logging("INFO")


def _log_record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg="probe",
        args=(),
        exc_info=None,
    )


def test_noisy_logger_filter_drops_low_level_noise_only():
    noisy_filter = NoisyLoggerFilter()
    for name in ("aistudio.cdp", "aistudio.browser.cdp", "aistudio.warp"):
        assert noisy_filter.filter(_log_record(name, logging.DEBUG)) is False
        assert noisy_filter.filter(_log_record(name, logging.INFO)) is False
    assert noisy_filter.filter(_log_record("aistudio.cdp", logging.WARNING)) is True
    assert noisy_filter.filter(_log_record("aistudio.server", logging.DEBUG)) is True
    assert noisy_filter.filter(_log_record("aistudio.browser", logging.DEBUG)) is True


def test_debug_env_var_triggers_dump_without_changing_log_level():
    """验证 DEBUG 环境变量仅用于触发 dump 请求，绝不影响日志输出级别。"""
    set_log_level("INFO")
    initial_level = get_log_level()

    # 1. 模拟 DEBUG="1"
    with patch.dict(os.environ, {"DEBUG": "1"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        # 严格断言：日志级别绝不能被 DEBUG 环境变量修改为 DEBUG！
        assert get_log_level() == initial_level
        assert get_log_level() == "INFO"

    # 2. 模拟 DEBUG="true"
    with patch.dict(os.environ, {"DEBUG": "true"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        assert get_log_level() == "INFO"

    # 3. 模拟 DEBUG="dump"
    with patch.dict(os.environ, {"DEBUG": "dump"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        assert get_log_level() == "INFO"

    # 4. 模拟未设置 DEBUG 且未设置其他环境变量
    env_clean = dict(os.environ)
    env_clean.pop("DEBUG", None)
    env_clean.pop("AISTUDIO_DEBUG", None)
    env_clean.pop("AISTUDIO_DUMP_REQUESTS", None)
    with patch.dict(os.environ, env_clean, clear=True):
        set_dump_requests(False)
        assert is_debug_env_active() is False
        assert is_dump_requests_enabled() is False
        assert get_log_level() == "INFO"


def test_dump_request_exchange_output():
    """验证请求详细转储报文格式与中文标识。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(UnifiedFormatter(use_color=False))
    dump_lgr = get_logger("dump")
    dump_lgr.addHandler(handler)
    dump_lgr.setLevel(logging.INFO)

    try:
        dump_request_exchange(
            req_id="req_test123",
            method="POST",
            url="/v1beta/models/gemini-3.7-flash:generateContent",
            client="127.0.0.1:54321",
            headers={
                "authorization": "Bearer secret1234567890",
                "user-agent": "test-client",
            },
            query_params={"key": "xyz"},
            body_text='{"contents": [{"role": "user", "parts": [{"text": "你好"}]}]}',
            status_code=200,
            elapsed_ms=123.45,
            response_headers={"content-type": "application/json"},
            response_text='{"candidates": [{"content": {"parts": [{"text": "你好！有什么我可以帮你的？"}]}}]}',
            is_stream=False,
        )
        output = stream.getvalue()

        assert (
            "==================== [请求报文转储: req_test123] ===================="
            in output
        )
        assert "客户端:       127.0.0.1:54321" in output
        assert (
            "请求接口:     POST /v1beta/models/gemini-3.7-flash:generateContent"
            in output
        )
        assert "Bearer ***" in output
        assert "secret1234567890" not in output
        assert "你好" in output
        assert (
            "-------------------- [响应报文转储: req_test123] --------------------"
            in output
        )
        assert "响应状态:     HTTP 200" in output
        assert "处理耗时:     123.45 ms" in output
        assert "你好！有什么我可以帮你的？" in output
        assert (
            "==================== [转储结束: req_test123] ===================="
            in output
        )
    finally:
        dump_lgr.removeHandler(handler)


def test_dump_console_text_masks_key_based_credentials(monkeypatch):
    """Console dump text must not leak key-based credentials either."""
    monkeypatch.setattr(
        "aistudio_api.infrastructure.utils.logger.is_dump_to_file_enabled",
        lambda: False,
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(UnifiedFormatter(use_color=False))
    dump_lgr = get_logger("dump")
    dump_lgr.addHandler(handler)
    dump_lgr.setLevel(logging.INFO)

    try:
        dump_request_exchange(
            req_id="req_secret_probe",
            method="POST",
            url="/v1beta/models/test:generateContent",
            client="127.0.0.1:1",
            headers={},
            query_params={},
            body_text=(
                '{"password": "body-secret", "nested": {"secret": "s3"},'
                ' "credentials": "cred", "text": "keep-body"}'
            ),
            status_code=200,
            elapsed_ms=1.0,
            response_text='{"token": "tok-value", "text": "keep-resp"}',
            is_stream=False,
        )
        output = stream.getvalue()

        assert "body-secret" not in output
        assert '"secret": "***"' in output
        assert '"credentials": "***"' in output
        assert "tok-value" not in output
        assert "keep-body" in output
        assert "keep-resp" in output
    finally:
        dump_lgr.removeHandler(handler)


@pytest.mark.asyncio
async def test_get_config_includes_logging_fields():
    """验证 GET /config 包含 log_level, dump_requests, debug_env_active 字段。"""
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "log_level" in data
        assert "dump_requests" in data
        assert "debug_env_active" in data
        assert isinstance(data["dump_requests"], bool)
        assert isinstance(data["debug_env_active"], bool)


@pytest.mark.asyncio
async def test_update_logging_config_endpoint(tmp_path):
    """验证 PUT /config/logging 能即时更新并持久化配置。"""
    test_yaml = tmp_path / "config.yaml"
    test_yaml.write_text("api_keys: []\n", encoding="utf-8")

    with patch(
        "aistudio_api.infrastructure.gateway.model_defaults._resolve_config_path",
        return_value=test_yaml,
    ):
        async with _client() as client:
            resp = await client.put(
                "/config/logging",
                json={"level": "WARNING", "dump_requests": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["log_level"] == "WARNING"
            assert data["dump_requests"] is True

            # 验证运行时已被即时修改
            assert get_log_level() == "WARNING"
            assert is_dump_requests_enabled() is True

            # 验证持久化到了 YAML
            saved_content = test_yaml.read_text(encoding="utf-8")
            assert "logging:" in saved_content
            assert "level: WARNING" in saved_content
            assert "dump_requests: true" in saved_content

            # 恢复设置
            await client.put(
                "/config/logging", json={"level": "INFO", "dump_requests": False}
            )
            assert get_log_level() == "INFO"


@pytest.mark.asyncio
async def test_middleware_request_dump_and_access_log():
    """验证中间件在 dump_requests=True 时正确拦截并完整响应。"""
    set_dump_requests(True)
    try:
        async with _client() as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
    finally:
        set_dump_requests(False)


def test_dump_request_to_file_by_req_id_only_writes_safe_fields(tmp_path):
    """验证按请求 ID 落盘的 JSON 只含 safe_* 字段，绝无原始报文/凭据。"""
    import json

    from aistudio_api.infrastructure.utils.logger import is_dump_to_file_enabled

    dump_test_dir = tmp_path / "test_dumps"
    set_dump_dir(dump_test_dir)
    set_dump_to_file(True)

    forbidden_keys = {
        "headers",
        "query_params",
        "body",
        "raw_body",
        "response_headers",
        "response_body",
        "raw_response_body",
    }

    try:
        assert is_dump_to_file_enabled() is True
        assert get_dump_dir() == dump_test_dir

        test_req_id = "req_f96e327d"
        dump_request_exchange(
            req_id=test_req_id,
            method="POST",
            url="/v1beta/models/gemini-2.5-flash:generateContent?key=AIzaSyLeakedKeyValue1234567890",
            client="127.0.0.1:45678",
            headers={
                "content-type": "application/json",
                "authorization": "Bearer super-secret-token-123456",
                "x-goog-api-key": "secret_key",
                "cookie": "SAPISID=abc123; theme=dark",
            },
            query_params={"alt": "sse", "key": "AIzaSyLeakedKeyValue1234567890"},
            body_text='{"contents": [{"role": "user", "parts": [{"text": "测试抗截断"}]}], "tools": [{"functionDeclarations": [{"name": "reply_fn"}]}], "key": "AIzaSyBodyLeakValue1234567890", "password": "body-secret"}',
            status_code=200,
            elapsed_ms=88.5,
            response_headers={"content-type": "application/json"},
            response_text='{"candidates": [{"content": {"parts": [{"text": "成功"}]}}]}',
            is_stream=False,
        )

        target_dump_file = dump_test_dir / f"{test_req_id}.json"
        latest_dump_file = dump_test_dir / "latest_request.json"

        assert target_dump_file.exists()
        assert latest_dump_file.exists()

        for path in (target_dump_file, latest_dump_file):
            raw_text = path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            assert forbidden_keys.isdisjoint(data)
            assert "safe_headers" in data
            assert "safe_query_params" in data
            assert "safe_body" in data
            assert "safe_response_headers" in data
            assert "safe_response_body" in data
            assert "super-secret-token-123456" not in raw_text
            assert "secret_key" not in raw_text
            assert "abc123" not in raw_text
            assert "AIzaSyLeakedKeyValue1234567890" not in raw_text
            assert "AIzaSyBodyLeakValue1234567890" not in raw_text
            assert "body-secret" not in raw_text

        data = json.loads(target_dump_file.read_text(encoding="utf-8"))
        assert data["req_id"] == test_req_id
        assert data["method"] == "POST"
        assert data["url"] == "/v1beta/models/gemini-2.5-flash:generateContent?key=***"
        assert data["client"] == "127.0.0.1:45678"
        assert data["safe_headers"]["content-type"] == "application/json"
        assert data["safe_headers"]["authorization"] == "Bearer ***"
        assert data["safe_headers"]["x-goog-api-key"] == "***"
        assert data["safe_query_params"] == {"alt": "sse", "key": "***"}
        assert data["status_code"] == 200
        assert data["elapsed_ms"] == 88.5
        assert data["is_stream"] is False
        assert data["safe_body"]["tools"][0]["functionDeclarations"][0]["name"] == (
            "reply_fn"
        )
        assert "测试抗截断" in data["safe_body"]["contents"][0]["parts"][0]["text"]
        assert (
            data["safe_response_body"]["candidates"][0]["content"]["parts"][0]["text"]
            == "成功"
        )
    finally:
        set_dump_to_file(None)
        set_dump_dir(None)


def test_default_and_relative_dump_dirs_resolve_under_data_dir(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AISTUDIO_DATA_DIR", str(data_dir))
    monkeypatch.delenv("AISTUDIO_DUMP_DIR", raising=False)
    set_dump_dir(None)
    set_dump_to_file(None)

    assert get_dump_dir() == (data_dir / "dumps").resolve()

    set_dump_dir("dumps")
    try:
        assert get_dump_dir() == (data_dir / "dumps").resolve()
    finally:
        set_dump_dir(None)


def test_instance_client_raw_dump_only_writes_safe_fields(monkeypatch, tmp_path):
    """实例级 raw dump 只保留 safe_* 字段，且默认/相对目录落在 data 下。"""
    import json
    from typing import cast

    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway.instance_client import (
        InstanceRequestClient,
    )
    from aistudio_api.infrastructure.gateway.session_protocol import RequestSession

    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(settings, "dump_raw_response", True)
    monkeypatch.setattr(settings, "dump_raw_response_dir", str(raw_dir))

    client = InstanceRequestClient(
        cast(RequestSession, object()),
        account_id="acc-1",
        tier="Free",
        surface="chat",
    )
    client._dump_raw_exchange(
        kind="generate_content",
        model="gemini-3.7-flash",
        capture_prompt="Authorization: Bearer super-secret-token",
        modified_body='{"key": "AIzaSyRawBodyLeak1234567890", "text": "hello"}',
        raw_response="Cookie: SAPISID=raw-cookie-value",
    )

    files = list(raw_dir.glob("aistudio_*.json"))
    assert len(files) == 1
    raw_text = files[0].read_text(encoding="utf-8")
    data = json.loads(raw_text)
    assert data["kind"] == "generate_content"
    assert data["instance"] == "acc-1"
    assert data["surface"] == "chat"
    assert data["model"] == "gemini-3.7-flash"
    assert "safe_prompt" in data
    assert data["safe_request_body"]["text"] == "hello"
    assert "safe_response" in data
    for key in ("capture_prompt", "modified_body", "raw_response"):
        assert key not in data
    assert "super-secret-token" not in raw_text
    assert "AIzaSyRawBodyLeak1234567890" not in raw_text
    assert "raw-cookie-value" not in raw_text


def test_instance_client_raw_dump_sanitizes_malicious_model(monkeypatch, tmp_path):
    """桩模型名不得带路径分隔符或 `..`，dump 文件必须留在 dump 目录内。"""
    from typing import cast

    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway.instance_client import (
        InstanceRequestClient,
    )
    from aistudio_api.infrastructure.gateway.session_protocol import RequestSession

    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(settings, "dump_raw_response", True)
    monkeypatch.setattr(settings, "dump_raw_response_dir", str(raw_dir))

    client = InstanceRequestClient(
        cast(RequestSession, object()),
        account_id="acc-1",
        tier="Free",
        surface="chat",
    )
    client._dump_raw_exchange(
        kind="generate_content",
        model="..\\evil/../x",
        capture_prompt="prompt",
        modified_body='{"text": "hello"}',
        raw_response="response",
    )

    written = [path for path in raw_dir.rglob("*") if path.is_file()]
    assert len(written) == 1
    dumped = written[0]
    assert dumped.parent == raw_dir
    name = dumped.name
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name
    assert "evil" in name


def test_ai_studio_client_raw_dump_only_writes_safe_fields(monkeypatch, tmp_path):
    """N-14: control-client raw dump 只写 safe_* 字段且模型文件名已净化。"""
    import json

    from aistudio_api.config import settings
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    raw_dir = tmp_path / "raw"
    monkeypatch.delenv("AISTUDIO_DUMP_RAW_RESPONSE_DIR", raising=False)
    monkeypatch.setattr(settings, "dump_raw_response", True)
    monkeypatch.setattr(settings, "dump_raw_response_dir", str(raw_dir))

    client = AIStudioClient(port=9222)
    client._dump_raw_exchange(
        kind="generate_content",
        model="..\\evil/../models/gemini-3.7-flash",
        capture_prompt="Authorization: Bearer super-secret-token",
        modified_body='{"key": "AIzaSyRawBodyLeak1234567890", "text": "hello"}',
        raw_response="Cookie: SAPISID=raw-cookie-value",
    )

    files = list(raw_dir.glob("aistudio_*.json"))
    assert len(files) == 1
    dumped = files[0]
    assert dumped.parent == raw_dir
    assert "/" not in dumped.name
    assert "\\" not in dumped.name
    assert ".." not in dumped.name

    raw_text = dumped.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    assert data["kind"] == "generate_content"
    assert data["safe_request_body"]["text"] == "hello"
    assert set(data) == {
        "kind",
        "model",
        "safe_prompt",
        "safe_request_body",
        "safe_response",
    }
    for key in ("capture_prompt", "modified_body", "raw_response", "headers", "body"):
        assert key not in data
    assert "super-secret-token" not in raw_text
    assert "AIzaSyRawBodyLeak1234567890" not in raw_text
    assert "raw-cookie-value" not in raw_text
