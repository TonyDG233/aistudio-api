"""Tests for the WP5 account-group data model in ``features.groups``.

覆盖：解析归一（合法/非法/边界）、坏项丢弃、缺省形状与热重载。
"""

from __future__ import annotations

import os
from typing import Any, cast

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_features_config,
    invalidate_config_cache,
)


def _features(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return cast("dict[str, Any]", get_features_config(config_path))


def _write(config_path: Any, body: str) -> None:
    config_path.write_text(body, encoding="utf-8")


# ========== 缺省 / 空列表 ==========


def test_groups_default_empty_without_section(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(config_path, "api_keys: []\n")
    features = _features(config_path)
    assert features["groups"] == []


def test_groups_empty_list_stays_empty(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(config_path, "features:\n  groups: []\n")
    assert _features(config_path)["groups"] == []


def test_groups_missing_key_stays_empty(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(config_path, "features:\n  build_surface: true\n")
    assert _features(config_path)["groups"] == []


# ========== 合法项：归一形状 ==========


def test_groups_valid_entries_are_normalized(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: pro
      match_tier: [PRO, " ultra ", pro, ""]
      match_emails_regex: "^pro@example[.]com$"
      allowed_surfaces: [chat, BUILD, bogus, chat]
      max_page_instances: 2
    - name: free
      match_tier: [free]
      allowed_surfaces: [build]
      max_page_instances: 3
""",
    )
    groups = _features(config_path)["groups"]
    assert groups == [
        {
            "name": "pro",
            "match_tier": ["pro", "ultra"],
            "match_emails_regex": "^pro@example[.]com$",
            "allowed_surfaces": ["chat", "build"],
            "max_page_instances": 2,
        },
        {
            "name": "free",
            "match_tier": ["free"],
            "match_emails_regex": None,
            "allowed_surfaces": ["build"],
            "max_page_instances": 3,
        },
    ]


def test_groups_defaults_for_missing_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - {}
""",
    )
    groups = _features(config_path)["groups"]
    assert groups == [
        {
            "name": "group-0",
            "match_tier": [],
            "match_emails_regex": None,
            "allowed_surfaces": [],
            "max_page_instances": 2,
        }
    ]


def test_groups_name_falls_back_to_index(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: kept
    - name: ""
    - name: "   "
""",
    )
    names = [g["name"] for g in _features(config_path)["groups"]]
    assert names == ["kept", "group-1", "group-2"]


def test_groups_email_regex_blank_becomes_none(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: a
      match_emails_regex: ""
    - name: b
      match_emails_regex: "   "
    - name: c
      match_emails_regex: "x@y"
""",
    )
    regexes = [g["match_emails_regex"] for g in _features(config_path)["groups"]]
    assert regexes == [None, None, "x@y"]


def test_groups_allowed_surfaces_unknown_filtered_and_empty_allowed(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: disabled
      allowed_surfaces: []
    - name: only_unknown
      allowed_surfaces: [gemini_app, chatgpt]
""",
    )
    groups = _features(config_path)["groups"]
    assert groups[0]["allowed_surfaces"] == []
    assert groups[1]["allowed_surfaces"] == []


# ========== 边界：max_page_instances ==========


def test_groups_max_page_instances_boundaries(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - { name: zero, max_page_instances: 0 }
    - { name: max, max_page_instances: 16 }
    - { name: over, max_page_instances: 99 }
    - { name: neg, max_page_instances: -5 }
    - { name: text, max_page_instances: "abc" }
    - { name: missing }
""",
    )
    quotas = {
        g["name"]: g["max_page_instances"] for g in _features(config_path)["groups"]
    }
    assert quotas == {
        "zero": 0,
        "max": 16,
        "over": 16,
        "neg": 0,
        "text": 2,
        "missing": 2,
    }


# ========== 非法结构：坏项丢弃不崩 ==========


def test_groups_non_list_is_dropped_to_empty(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(config_path, "features:\n  groups: not-a-list\n")
    assert _features(config_path)["groups"] == []


def test_groups_bad_entries_dropped_valid_kept(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: first
      match_tier: [pro]
    - "just-a-string"
    - 123
    - null
    - name: fourth
      match_tier: [free]
""",
    )
    groups = _features(config_path)["groups"]
    # 两个映射项保留；非映射坏项（字符串/数字/null）丢弃。
    assert [g["name"] for g in groups] == ["first", "fourth"]
    # 缺省名按原始索引生成，不因丢弃而重编号。
    assert groups[1]["match_tier"] == ["free"]


def test_groups_non_mapping_name_still_coerced(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: 12345
      match_tier: "pro"
      allowed_surfaces: chat
      max_page_instances: 4
""",
    )
    group = _features(config_path)["groups"][0]
    assert group["name"] == "12345"
    # 非列表的 match_tier / allowed_surfaces 归一为空列表（不崩）。
    assert group["match_tier"] == []
    assert group["allowed_surfaces"] == []
    assert group["max_page_instances"] == 4


# ========== 热重载 ==========


def test_groups_hot_reload_reflects_new_values(tmp_path):
    config_path = tmp_path / "hot_groups.yaml"
    _write(
        config_path,
        """
features:
  groups:
    - name: pro
      match_tier: [pro]
      allowed_surfaces: [chat]
      max_page_instances: 2
""",
    )
    invalidate_config_cache()
    before = _features(config_path)["groups"]
    assert before[0]["name"] == "pro"
    assert before[0]["match_tier"] == ["pro"]

    # 改写同一文件并显式前推 mtime，验证基于 mtime 的缓存会重新装载新内容。
    first_mtime = config_path.stat().st_mtime
    _write(
        config_path,
        """
features:
  groups:
    - name: free
      match_tier: [free, plus]
      allowed_surfaces: [build]
      max_page_instances: 3
""",
    )
    os.utime(config_path, (first_mtime + 30, first_mtime + 30))

    after = _features(config_path)["groups"]
    assert after == [
        {
            "name": "free",
            "match_tier": ["free", "plus"],
            "match_emails_regex": None,
            "allowed_surfaces": ["build"],
            "max_page_instances": 3,
        }
    ]
    invalidate_config_cache()


def test_groups_hot_reload_clears_to_empty(tmp_path):
    config_path = tmp_path / "hot_groups_clear.yaml"
    _write(
        config_path,
        "features:\n  groups:\n    - { name: pro, match_tier: [pro] }\n",
    )
    invalidate_config_cache()
    assert len(_features(config_path)["groups"]) == 1

    first_mtime = config_path.stat().st_mtime
    _write(config_path, "features:\n  groups: []\n")
    os.utime(config_path, (first_mtime + 30, first_mtime + 30))

    assert _features(config_path)["groups"] == []
    invalidate_config_cache()
