"""Hermetic test environment fixtures isolating test runs from local config."""

from __future__ import annotations

import pytest

_TEST_DEFAULT_CONFIG = """
api_keys: []
security:
  api_allow_anonymous: false
instances:
  - count: 1
    surface: chat
    min_tier: Free
  - count: 1
    surface: chat
    min_tier: Pro
max_contexts: 4
model_defaults:
  drop_unsupported_params: false
  profiles:
    - name: image_models
      match:
        contains:
          - image
      is_image_model: true
      disable_safety_settings: true
    - name: gemma_models
      match:
        prefixes:
          - gemma-
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5
    - name: gemini_models
      match:
        prefixes:
          - gemini-
      safety_settings:
        Harassment: 5
        Hate: 5
        Sexually Explicit: 5
        Dangerous Content: 5
  models: {}
paid_model_rules:
  ultra_required:
    contains: ["veo", "ultra"]
    required_tiers: ["Ultra", "Ultra_20X"]
  pro_required:
    contains: ["argon", "gemini-4", "imagen"]
    required_tiers: ["Pro", "Ultra", "Ultra_20X"]
rotator:
  max_retries: 5
  cooldowns:
    rate_limited_short: 60
    rate_limited_drip: 300
    rate_limited_drip_extended: 600
    auth_error: 300
tier_quotas:
  Free:
    flash_models:
      match:
        contains: ["flash"]
      daily_limit: 100
    pro_models:
      match:
        contains: ["pro"]
      daily_limit: 10
    default_limit: 20
  Pro:
    argon_models:
      match:
        contains: ["argon", "gemini-4"]
      daily_limit: 50
    flash_models:
      match:
        contains: ["flash"]
      daily_limit: 150
    pro_models:
      match:
        contains: ["pro"]
      daily_limit: 50
    image_models:
      match:
        contains: ["image"]
      daily_limit: 50
    default_limit: 100
  Ultra:
    argon_models:
      match:
        contains: ["argon", "gemini-4"]
      daily_limit: 250
    flash_models:
      match:
        contains: ["flash"]
      daily_limit: 1500
    pro_models:
      match:
        contains: ["pro"]
      daily_limit: 250
    image_models:
      match:
        contains: ["image"]
      daily_limit: 250
    default_limit: 500
  overrides: {}
account_presets:
  - id: preset_all
    name: 全部启用 (最大并发)
    description: 激活所有可用账号，提供最充沛的并发与负载池
    action: enable_all
    is_system: true
  - id: preset_paid_only
    name: 纯高阶付费池 (Pro/Ultra)
    description: 仅激活 Pro 与 Ultra 等级账号，全力保障付费模型调用
    action: filter_tier
    target_tiers: ["Pro", "Ultra"]
    is_system: true
  - id: preset_free_only
    name: 纯免费节能池 (Free)
    description: 仅激活 Free 等级账号，用于日常普通模型测试
    action: filter_tier
    target_tiers: ["Free"]
    is_system: true
  - id: preset_production
    name: 生产专属集群 (Production)
    description: 仅激活 group 标记为 production 的核心业务账号
    action: filter_group
    target_groups: ["production"]
    is_system: true
"""


@pytest.fixture(scope="session")
def _shared_test_env(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("test_env")
    config_file = base_dir / "test_isolated_config.yaml"
    config_file.write_text(_TEST_DEFAULT_CONFIG, encoding="utf-8")
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "accounts").mkdir(exist_ok=True)
    return config_file, data_dir


@pytest.fixture(autouse=True)
def isolate_test_config(monkeypatch, _shared_test_env):
    """Isolate all test runs from repository-level config.yaml modifications and runtime state files."""
    from aistudio_api.config import settings
    from aistudio_api.domain.model_access import clear_paid_models
    from aistudio_api.infrastructure.gateway.model_discovery import model_discovery

    config_file, data_dir = _shared_test_env
    stats_file = data_dir / "stats.json"
    rotator_file = data_dir / "rotator_state.json"
    model_cache_file = data_dir / "models_cache.json"
    accounts_dir = data_dir / "accounts"

    monkeypatch.setenv("AISTUDIO_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AISTUDIO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AISTUDIO_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.setenv("AISTUDIO_STATS_FILE", str(stats_file))
    monkeypatch.setenv("AISTUDIO_MODEL_CACHE_FILE", str(model_cache_file))
    monkeypatch.setenv("AISTUDIO_ROTATOR_STATE_FILE", str(rotator_file))
    monkeypatch.setenv("AISTUDIO_RETRY_SAME_DELAY_SEC", "0")
    monkeypatch.setenv("AISTUDIO_SWITCH_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(settings, "model_cache_file", str(model_cache_file))

    saved_models = model_discovery._cached_models
    saved_fetched_at = model_discovery._last_fetched_at
    saved_source = model_discovery.last_source
    saved_error = model_discovery.last_error
    model_discovery._cached_models = []
    model_discovery._last_fetched_at = 0.0
    model_discovery.last_source = "fallback"
    model_discovery.last_error = None
    clear_paid_models()
    yield
    model_discovery._cached_models = saved_models
    model_discovery._last_fetched_at = saved_fetched_at
    model_discovery.last_source = saved_source
    model_discovery.last_error = saved_error
    clear_paid_models()
    if stats_file.exists():
        stats_file.unlink()
    if rotator_file.exists():
        rotator_file.unlink()
    if model_cache_file.exists():
        model_cache_file.unlink()
