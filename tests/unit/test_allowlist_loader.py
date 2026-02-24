"""Tests for AllowlistLoader and AllowlistEntry — E-009-S-001.

Tests:
  - load() from YAML file
  - load_from_config() from Config object
  - Invalid YAML handling (keeps prior allowlist)
  - Missing file handling (returns 0, empty allowlist)
  - AllowlistEntry fields
  - scope: upstream_path warning
  - Thread-safe get_entries()

Story: E-009-S-001
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from app.allowlist.loader import AllowlistEntry, AllowlistLoader, _parse_allowlist_raw

# ─── AllowlistEntry tests ──────────────────────────────────────────────────────


class TestAllowlistEntry:
    def test_required_field_rule_id(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        assert entry.rule_id == "CREDENTIAL_DETECTED"

    def test_defaults(self):
        entry = AllowlistEntry(rule_id="CREDENTIAL_DETECTED")
        assert entry.note is None
        assert entry.pattern is None
        assert entry.scope == "global"

    def test_all_fields(self):
        entry = AllowlistEntry(
            rule_id="CREDENTIAL_DETECTED",
            note="Test key in CI",
            pattern="test_.*key",
            scope="global",
        )
        assert entry.rule_id == "CREDENTIAL_DETECTED"
        assert entry.note == "Test key in CI"
        assert entry.pattern == "test_.*key"
        assert entry.scope == "global"

    def test_upstream_path_scope_allowed(self):
        # scope: upstream_path is a valid parse value (AC-S001-08)
        entry = AllowlistEntry(rule_id="X", scope="upstream_path")
        assert entry.scope == "upstream_path"


# ─── _parse_allowlist_raw tests ───────────────────────────────────────────────


class TestParseAllowlistRaw:
    def test_none_returns_empty(self):
        assert _parse_allowlist_raw(None) == []

    def test_empty_list_returns_empty(self):
        assert _parse_allowlist_raw([]) == []

    def test_dict_with_allowlist_key(self):
        raw = {"version": 1, "allowlist": [{"rule_id": "CREDENTIAL_DETECTED"}]}
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 1
        assert entries[0].rule_id == "CREDENTIAL_DETECTED"

    def test_direct_list(self):
        raw = [{"rule_id": "CREDENTIAL_DETECTED"}, {"rule_id": "PROMPT_INJECTION"}]
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 2

    def test_entry_with_note_and_pattern(self):
        raw = [{"rule_id": "CREDENTIAL_DETECTED", "note": "CI test key", "pattern": "sk-test-"}]
        entries = _parse_allowlist_raw(raw)
        assert entries[0].note == "CI test key"
        assert entries[0].pattern == "sk-test-"

    def test_entry_missing_rule_id_skipped(self):
        raw = [{"note": "no rule_id here"}, {"rule_id": "VALID"}]
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 1
        assert entries[0].rule_id == "VALID"

    def test_invalid_entry_type_skipped(self):
        raw = ["not a dict", {"rule_id": "VALID"}]
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 1

    def test_upstream_path_scope_parsed_without_error(self, capsys):
        # scope: upstream_path is parsed without error and entry is created (AC-S001-08)
        raw = [{"rule_id": "CREDENTIAL_DETECTED", "scope": "upstream_path"}]
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 1
        assert entries[0].scope == "upstream_path"
        # A warning is logged to stdout (structlog JSON format)
        captured = capsys.readouterr()
        assert "upstream_path" in captured.out

    def test_invalid_scope_treated_as_global(self):
        raw = [{"rule_id": "CREDENTIAL_DETECTED", "scope": "unknown_scope"}]
        entries = _parse_allowlist_raw(raw)
        assert entries[0].scope == "global"

    def test_invalid_regex_pattern_discarded(self):
        # Invalid re2 pattern → pattern set to None (not skipped, just no pattern)
        raw = [{"rule_id": "CREDENTIAL_DETECTED", "pattern": "[invalid(re2"}]
        entries = _parse_allowlist_raw(raw)
        assert len(entries) == 1
        assert entries[0].pattern is None

    def test_valid_regex_pattern_preserved(self):
        raw = [{"rule_id": "CREDENTIAL_DETECTED", "pattern": "sk-test-[a-z]+"}]
        entries = _parse_allowlist_raw(raw)
        assert entries[0].pattern == "sk-test-[a-z]+"

    def test_empty_allowlist_key_in_dict(self):
        raw = {"version": 1, "allowlist": []}
        assert _parse_allowlist_raw(raw) == []

    def test_null_allowlist_key_returns_empty(self):
        raw = {"version": 1, "allowlist": None}
        assert _parse_allowlist_raw(raw) == []


# ─── AllowlistLoader.load() tests ─────────────────────────────────────────────


class TestAllowlistLoaderLoad:
    def _write_yaml(self, tmp_path, content: Any) -> str:
        path = os.path.join(tmp_path, "allowlist.yaml")
        with open(path, "w") as f:
            yaml.dump(content, f)
        return path

    def test_load_missing_file_returns_zero(self, tmp_path):
        loader = AllowlistLoader()
        count = loader.load(os.path.join(str(tmp_path), "nonexistent.yaml"))
        assert count == 0
        assert loader.get_entries() == []

    def test_load_valid_file_returns_count(self, tmp_path):
        path = self._write_yaml(
            str(tmp_path),
            {"version": 1, "allowlist": [{"rule_id": "CREDENTIAL_DETECTED"}]},
        )
        loader = AllowlistLoader()
        count = loader.load(path)
        assert count == 1
        assert loader.get_entries()[0].rule_id == "CREDENTIAL_DETECTED"

    def test_load_two_entries(self, tmp_path):
        path = self._write_yaml(
            str(tmp_path),
            {"allowlist": [{"rule_id": "A"}, {"rule_id": "B"}]},
        )
        loader = AllowlistLoader()
        count = loader.load(path)
        assert count == 2

    def test_load_invalid_yaml_keeps_prior(self, tmp_path):
        # First load valid
        valid_path = self._write_yaml(
            str(tmp_path), {"allowlist": [{"rule_id": "PRIOR"}]}
        )
        loader = AllowlistLoader()
        loader.load(valid_path)
        assert len(loader.get_entries()) == 1

        # Write invalid YAML
        invalid_path = os.path.join(str(tmp_path), "bad.yaml")
        with open(invalid_path, "w") as f:
            f.write("key: [unclosed bracket\n")

        count = loader.load(invalid_path)
        assert count == -1  # Signal: error occurred
        # Prior entries preserved (AC-S001-03)
        assert loader.get_entries()[0].rule_id == "PRIOR"

    def test_load_empty_file_returns_zero(self, tmp_path):
        path = os.path.join(str(tmp_path), "empty.yaml")
        with open(path, "w") as f:
            f.write("")
        loader = AllowlistLoader()
        count = loader.load(path)
        assert count == 0
        assert loader.get_entries() == []

    def test_get_entries_returns_copy(self, tmp_path):
        path = self._write_yaml(
            str(tmp_path), {"allowlist": [{"rule_id": "CREDENTIAL_DETECTED"}]}
        )
        loader = AllowlistLoader()
        loader.load(path)
        entries1 = loader.get_entries()
        entries1.clear()
        entries2 = loader.get_entries()
        assert len(entries2) == 1  # Internal state not affected

    def test_load_direct_list_format(self, tmp_path):
        path = os.path.join(str(tmp_path), "list.yaml")
        with open(path, "w") as f:
            yaml.dump([{"rule_id": "CREDENTIAL_DETECTED", "note": "CI keys"}], f)
        loader = AllowlistLoader()
        count = loader.load(path)
        assert count == 1
        assert loader.get_entries()[0].note == "CI keys"

    def test_load_from_config_with_no_allowlist_entries(self):
        class FakeConfig:
            allowlist_entries = None

        loader = AllowlistLoader()
        count = loader.load_from_config(FakeConfig())
        assert count == 0
        assert loader.get_entries() == []

    def test_load_from_config_with_entries(self):
        class FakeConfig:
            allowlist_entries = [{"rule_id": "CREDENTIAL_DETECTED"}]

        loader = AllowlistLoader()
        count = loader.load_from_config(FakeConfig())
        assert count == 1

    def test_load_entry_with_all_fields(self, tmp_path):
        path = self._write_yaml(
            str(tmp_path),
            {
                "allowlist": [
                    {
                        "rule_id": "CREDENTIAL_DETECTED",
                        "note": "test note",
                        "pattern": "sk-test-",
                        "scope": "global",
                    }
                ]
            },
        )
        loader = AllowlistLoader()
        loader.load(path)
        entry = loader.get_entries()[0]
        assert entry.rule_id == "CREDENTIAL_DETECTED"
        assert entry.note == "test note"
        assert entry.pattern == "sk-test-"
        assert entry.scope == "global"
