"""
Tests for MCP task manager installation support.

Coverage:
- US1: Claude users can configure a task manager MCP server during `specify init`
- US2: Non-Claude agents skip the prompt entirely
- US3: New JSON files in mcps/task-managers/ are auto-discovered (no code change)
- Edge cases: malformed JSON, duplicate keys, empty credentials, .mcp.json as directory
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from specify_cli import (
    app,
    _get_mcp_templates,
    _write_mcp_config,
    _prompt_mcp_task_manager,
    StepTracker,
)
from rich.console import Console


# ===== Shared helpers =====

def _make_fake_download(project_path, *args, **kwargs):
    """Simulate download_and_extract_template: create project dir."""
    project_path.mkdir(parents=True, exist_ok=True)


def _base_patches():
    """Return list of (target, value) for standard init() no-ops."""
    return [
        ("specify_cli.download_and_extract_template", _make_fake_download),
        ("specify_cli.ensure_executable_scripts", lambda *a, **kw: None),
        ("specify_cli.ensure_constitution_from_template", lambda *a, **kw: None),
        ("specify_cli.is_git_repo", lambda *a, **kw: False),
        ("specify_cli.shutil.which", lambda cmd: "/usr/bin/git"),
    ]


def _run_init(target, extra_args=None, extra_patches=None):
    """Invoke `specify init <target> --ai claude --script sh --no-git` with patches."""
    runner = CliRunner()
    args = ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
    if extra_args:
        args += extra_args

    patches = _base_patches() + (extra_patches or [])
    ctx_managers = [patch(t, side_effect=v) if callable(v) else patch(t, return_value=v)
                    for t, v in patches]

    with _enter_all(ctx_managers):
        return runner.invoke(app, args)


class _enter_all:
    """Context manager that enters a list of context managers."""

    def __init__(self, cms):
        self._cms = cms
        self._entered = []

    def __enter__(self):
        for cm in self._cms:
            self._entered.append(cm.__enter__())
        return self._entered

    def __exit__(self, *args):
        for cm in reversed(self._cms):
            cm.__exit__(*args)


@pytest.fixture
def console():
    return Console(quiet=True)


@pytest.fixture
def tracker():
    t = StepTracker("Test")
    t.add("mcp-setup", "MCP task manager")
    return t


# ===== US1: Tests for install task manager MCP =====

class TestMcpConfigCreatedFresh:
    """T009 — .mcp.json is created fresh when Jira is selected."""

    def test_mcp_config_created_fresh(self, tmp_path):
        target = tmp_path / "mcp-test"

        jira_entry = {
            "type": "http",
            "url": "https://test.atlassian.net/mcp",
            "headers": {"Authorization": "Bearer test-token"},
        }

        def mock_mcp(project_path, selected_ai, console, tracker):
            _write_mcp_config(project_path, "jira", jira_entry, console)
            tracker.complete("mcp-setup", "jira configured")

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        config_path = target / ".mcp.json"
        assert config_path.exists(), ".mcp.json should have been created"
        config = json.loads(config_path.read_text())
        assert "mcpServers" in config
        assert "jira" in config["mcpServers"]
        entry = config["mcpServers"]["jira"]
        assert entry["type"] == "http"
        assert entry["url"] == "https://test.atlassian.net/mcp"
        assert entry["headers"]["Authorization"] == "Bearer test-token"


class TestMcpNoneSelectionSkipsConfig:
    """T010 — selecting None does not create .mcp.json."""

    def test_mcp_none_selection_skips_config(self, tmp_path):
        target = tmp_path / "mcp-test-none"

        def mock_mcp_none(project_path, selected_ai, console, tracker):
            tracker.skip("mcp-setup", "none selected")

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp_none), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        assert not (target / ".mcp.json").exists(), ".mcp.json should NOT be created when None is selected"


class TestMcpConfigMergePreservesEntries:
    """T011 — existing mcpServers entries are preserved during merge."""

    def test_mcp_config_merge_preserves_entries(self, tmp_path):
        target = tmp_path / "mcp-merge"

        notion_entry = {
            "type": "http",
            "url": "https://mcp.notion.com/mcp",
            "headers": {"Authorization": "Bearer notion-token"},
        }

        def mock_mcp_notion(project_path, selected_ai, console, tracker):
            # Pre-create .mcp.json with an existing entry
            project_path.mkdir(parents=True, exist_ok=True)
            existing_config = {"mcpServers": {"existing-tool": {"type": "stdio"}}}
            (project_path / ".mcp.json").write_text(json.dumps(existing_config))
            # Now add Notion (should merge, not overwrite)
            _write_mcp_config(project_path, "notion", notion_entry, console)
            tracker.complete("mcp-setup", "notion configured")

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp_notion), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        config_path = target / ".mcp.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "existing-tool" in config["mcpServers"], "Existing entry should be preserved"
        assert "notion" in config["mcpServers"], "New Notion entry should be added"
        assert config["mcpServers"]["notion"]["url"] == "https://mcp.notion.com/mcp"


class TestMcpDotMcpJsonIsDirAborts:
    """T012 — if .mcp.json exists as a directory, init exits with code 1."""

    def test_mcp_dotmcpjson_is_dir_aborts(self, tmp_path):
        target = tmp_path / "mcp-dir-conflict"

        jira_entry = {"type": "http", "url": "https://test.example.com/mcp", "headers": {}}

        def mock_mcp_dir_conflict(project_path, selected_ai, console, tracker):
            # Create .mcp.json as a directory instead of a file
            project_path.mkdir(parents=True, exist_ok=True)
            mcp_path = project_path / ".mcp.json"
            mcp_path.mkdir()
            # Attempt to write config — should detect .mcp.json is a directory and abort
            _write_mcp_config(project_path, "jira", jira_entry, console)
            tracker.complete("mcp-setup", "jira configured")  # Should not reach here

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp_dir_conflict), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 1, "Should exit with code 1 when .mcp.json is a directory"
        mcp_path = target / ".mcp.json"
        assert mcp_path.is_dir(), ".mcp.json directory should remain unchanged"


class TestMcpDotClaudeConfigUntouched:
    """US2 — pre-existing .claude/config.json is not modified by the new flow."""

    def test_mcp_dotclaude_config_untouched(self, tmp_path):
        target = tmp_path / "mcp-claude-untouched"

        sentinel_content = json.dumps(
            {"mcpServers": {"sentinel-tool": {"type": "stdio", "command": "sentinel"}}}
        )

        jira_entry = {
            "type": "http",
            "url": "https://test.atlassian.net/mcp",
            "headers": {"Authorization": "Bearer test-token"},
        }

        def mock_mcp(project_path, selected_ai, console, tracker):
            # Pre-create .claude/config.json with sentinel content
            claude_dir = project_path / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "config.json").write_text(sentinel_content)
            # Write MCP config — should go to .mcp.json, not touch .claude/config.json
            _write_mcp_config(project_path, "jira", jira_entry, console)
            tracker.complete("mcp-setup", "jira configured")

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        # .mcp.json should have the new Jira entry
        mcp_path = target / ".mcp.json"
        assert mcp_path.exists(), ".mcp.json should have been created"
        mcp_config = json.loads(mcp_path.read_text())
        assert "jira" in mcp_config["mcpServers"]
        # .claude/config.json should be byte-for-byte unchanged
        claude_config_path = target / ".claude" / "config.json"
        assert claude_config_path.exists(), ".claude/config.json should still exist"
        assert claude_config_path.read_text() == sentinel_content, \
            ".claude/config.json must be byte-for-byte identical to the pre-created content"


# ===== US2: Tests for skip prompt for non-Claude agents =====

class TestMcpPromptSkippedForNonClaude:
    """T016 — explicit non-Claude agents never trigger the MCP prompt."""

    def test_mcp_prompt_skipped_for_explicit_non_claude(self, tmp_path):
        target = tmp_path / "mcp-gemini"

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager") as mock_mcp, \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "gemini", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        mock_mcp.assert_not_called(), "_prompt_mcp_task_manager should NOT be called for gemini"
        assert not (target / ".mcp.json").exists()


class TestMcpPromptSkippedForInteractiveNonClaude:
    """T017 — interactive non-Claude selection does not trigger the MCP prompt."""

    def test_mcp_prompt_skipped_for_interactive_non_claude(self, tmp_path):
        target = tmp_path / "mcp-interactive-gemini"

        runner = CliRunner()
        with patch("specify_cli.select_with_arrows", return_value="gemini"), \
             patch("specify_cli._prompt_mcp_task_manager") as mock_mcp, \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            # No --ai flag: interactive agent selection mocked to return "gemini"
            result = runner.invoke(
                app, ["init", str(target), "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        mock_mcp.assert_not_called(), "_prompt_mcp_task_manager should NOT be called for gemini"
        assert not (target / ".mcp.json").exists()


# ===== US3: Tests for dynamic task manager discovery =====

class TestMcpPromptShownForClaude:
    """T020 — select_with_arrows is called with all discovered template names plus None."""

    def test_mcp_prompt_shown_for_claude(self, tmp_path, tracker, console):
        target = tmp_path / "mcp-prompt-check"
        target.mkdir()

        fake_templates = {
            "jira": {"display_name": "Jira", "prompts": [], "server_entry": {}},
            "notion": {"display_name": "Notion", "prompts": [], "server_entry": {}},
        }

        captured_options = {}

        def mock_select(options, *args, **kwargs):
            captured_options.update(options)
            return "None"  # user selects None to skip

        import sys
        with patch("specify_cli._get_mcp_templates", return_value=fake_templates), \
             patch("specify_cli.select_with_arrows", side_effect=mock_select), \
             patch.object(sys.stdin, "isatty", return_value=True):
            _prompt_mcp_task_manager(target, "claude", console, tracker)

        assert "None" in captured_options, "'None' option should always be present"
        assert "jira" in captured_options, "'jira' should be in options"
        assert "notion" in captured_options, "'notion' should be in options"


class TestMcpTemplatesDiscoverable:
    """T021 — adding a new JSON file to mcps/task-managers/ auto-adds it as an option."""

    def test_mcp_templates_discoverable(self, tmp_path):
        # Create a fake core_pack directory structure
        fake_core = tmp_path / "core_pack"
        templates_dir = fake_core / "mcps" / "task-managers"
        templates_dir.mkdir(parents=True)

        # Copy the real jira.json and add a new extra.json
        extra_template = {
            "display_name": "Extra Tool",
            "prompts": [{"key": "token", "label": "API token", "default": None}],
            "server_entry": {"type": "http", "url": "https://extra.example.com", "headers": {}},
        }
        (templates_dir / "extra.json").write_text(json.dumps(extra_template))

        with patch("specify_cli._locate_core_pack", return_value=fake_core):
            result = _get_mcp_templates()

        assert "extra" in result, "extra.json should be discoverable without code changes"
        assert result["extra"]["display_name"] == "Extra Tool"


class TestMcpEmptyTemplatesSkipsPrompt:
    """T022 — when no templates exist, prompt is skipped and no .mcp.json is created."""

    def test_mcp_empty_templates_skips_prompt(self, tmp_path, tracker, console):
        target = tmp_path / "mcp-empty"
        target.mkdir()

        mock_select = MagicMock()

        import sys
        with patch("specify_cli._get_mcp_templates", return_value={}), \
             patch("specify_cli.select_with_arrows", side_effect=mock_select), \
             patch.object(sys.stdin, "isatty", return_value=True):
            _prompt_mcp_task_manager(target, "claude", console, tracker)

        mock_select.assert_not_called(), "No selection prompt when templates are empty"
        assert not (target / ".mcp.json").exists()


# ===== Edge Case Tests =====

class TestMcpConfigMalformedJsonAborts:
    """T025 — malformed .mcp.json causes exit code 1 without modifying the file."""

    def test_mcp_config_malformed_json_aborts(self, tmp_path):
        target = tmp_path / "mcp-malformed"

        jira_entry = {"type": "http", "url": "https://test.example.com", "headers": {}}

        def mock_mcp_malformed(project_path, selected_ai, console, tracker):
            # Create malformed .mcp.json
            project_path.mkdir(parents=True, exist_ok=True)
            (project_path / ".mcp.json").write_text("{broken json content")
            # Attempt to write — should detect malformed JSON and abort
            _write_mcp_config(project_path, "jira", jira_entry, console)
            tracker.complete("mcp-setup", "configured")  # Should not reach here

        runner = CliRunner()
        with patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp_malformed), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--ai", "claude", "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 1, "Should exit with code 1 for malformed JSON"
        mcp_path = target / ".mcp.json"
        assert mcp_path.exists()
        assert mcp_path.read_text() == "{broken json content", "File should be unchanged"


class TestMcpConfigDuplicateKeyWarns:
    """T026 — duplicate key triggers a warning and overwrite confirmation."""

    def test_mcp_config_duplicate_key_warns_and_overwrites_when_confirmed(self, tmp_path, console):
        target = tmp_path / "mcp-duplicate"
        target.mkdir(parents=True)
        existing = {"mcpServers": {"jira": {"type": "http", "url": "https://old.example.com", "headers": {}}}}
        (target / ".mcp.json").write_text(json.dumps(existing))

        new_entry = {"type": "http", "url": "https://new.example.com", "headers": {}}

        from rich.prompt import Confirm
        with patch.object(Confirm, "ask", return_value=True):
            _write_mcp_config(target, "jira", new_entry, console)

        config = json.loads((target / ".mcp.json").read_text())
        assert config["mcpServers"]["jira"]["url"] == "https://new.example.com"

    def test_mcp_config_duplicate_key_skipped_when_declined(self, tmp_path, console):
        target = tmp_path / "mcp-duplicate-decline"
        target.mkdir(parents=True)
        existing = {"mcpServers": {"jira": {"type": "http", "url": "https://old.example.com", "headers": {}}}}
        (target / ".mcp.json").write_text(json.dumps(existing))

        new_entry = {"type": "http", "url": "https://new.example.com", "headers": {}}

        from rich.prompt import Confirm
        with patch.object(Confirm, "ask", return_value=False):
            _write_mcp_config(target, "jira", new_entry, console)

        config = json.loads((target / ".mcp.json").read_text())
        assert config["mcpServers"]["jira"]["url"] == "https://old.example.com", "Old entry should be preserved"


class TestMcpEmptyCredentialsRejected:
    """T027 — empty input for required fields triggers a re-prompt."""

    def test_mcp_empty_credentials_rejected_then_valid(self, tmp_path, tracker, console):
        target = tmp_path / "mcp-empty-creds"
        target.mkdir()

        # One template with a required field (default=None)
        fake_templates = {
            "jira": {
                "display_name": "Jira",
                "prompts": [
                    {"key": "url", "label": "Jira URL", "default": None},
                    {"key": "token", "label": "API token", "default": None},
                ],
                "server_entry": {
                    "type": "http",
                    "url": "${url}",
                    "headers": {"Authorization": "Bearer ${token}"},
                },
            }
        }

        from rich.prompt import Prompt
        # First call for url: empty, second call: valid; token: valid
        prompt_responses = iter(["", "https://test.example.com/mcp", "my-token"])

        import sys
        with patch("specify_cli._get_mcp_templates", return_value=fake_templates), \
             patch("specify_cli.select_with_arrows", return_value="jira"), \
             patch.object(Prompt, "ask", side_effect=lambda *a, **kw: next(prompt_responses)), \
             patch.object(sys.stdin, "isatty", return_value=True):
            _prompt_mcp_task_manager(target, "claude", console, tracker)

        config_path = target / ".mcp.json"
        assert config_path.exists(), ".mcp.json should be created after valid credentials"
        config = json.loads(config_path.read_text())
        assert config["mcpServers"]["jira"]["url"] == "https://test.example.com/mcp"
        assert config["mcpServers"]["jira"]["headers"]["Authorization"] == "Bearer my-token"


class TestMcpPromptShownForInteractiveClaude:
    """T028 — interactive Claude selection triggers the MCP prompt (SC-005)."""

    def test_mcp_prompt_shown_for_interactive_claude(self, tmp_path):
        target = tmp_path / "mcp-interactive-claude"

        jira_entry = {
            "type": "http",
            "url": "https://test.atlassian.net/mcp",
            "headers": {"Authorization": "Bearer test-token"},
        }

        def mock_mcp(project_path, selected_ai, console, tracker):
            _write_mcp_config(project_path, "jira", jira_entry, console)
            tracker.complete("mcp-setup", "jira configured")

        runner = CliRunner()
        # No --ai flag: select_with_arrows returns "claude" for agent selection
        with patch("specify_cli.select_with_arrows", return_value="claude"), \
             patch("specify_cli._prompt_mcp_task_manager", side_effect=mock_mcp), \
             patch("specify_cli.download_and_extract_template", side_effect=_make_fake_download), \
             patch("specify_cli.ensure_executable_scripts"), \
             patch("specify_cli.ensure_constitution_from_template"), \
             patch("specify_cli.is_git_repo", return_value=False), \
             patch("specify_cli.shutil.which", return_value="/usr/bin/git"):
            result = runner.invoke(
                app, ["init", str(target), "--script", "sh", "--no-git"]
            )

        assert result.exit_code == 0, result.output
        config_path = target / ".mcp.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert config["mcpServers"]["jira"]["type"] == "http"
        assert config["mcpServers"]["jira"]["url"] == "https://test.atlassian.net/mcp"
        assert config["mcpServers"]["jira"]["headers"]["Authorization"] == "Bearer test-token"
