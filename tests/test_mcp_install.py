"""
Tests for MCP task manager installation support.

Coverage:
- US1: Claude users can configure a task manager MCP server during `specify init`
- US2: Non-Claude agents skip the prompt entirely
- US3: New JSON files in mcps/task-managers/ are auto-discovered (no code change)
- Edge cases: malformed JSON, duplicate keys, empty credentials, .mcp.json as directory
- Basic Auth flow: .env file creation, gitignore update, validation, skip-if-exists
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
    _write_env_file,
    _ensure_gitignore_entry,
    _validate_prompt_value,
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
    """T009 — .mcp.json is created fresh when Jira is selected (new stdio/Basic Auth format)."""

    def test_mcp_config_created_fresh(self, tmp_path):
        target = tmp_path / "mcp-test"

        # New Jira format: stdio with ${VAR} references in env block
        jira_entry = {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-atlassian"],
            "env": {
                "JIRA_URL": "${JIRA_URL}",
                "JIRA_USERNAME": "${JIRA_USERNAME}",
                "JIRA_API_TOKEN": "${JIRA_API_TOKEN}",
            },
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
        assert entry["type"] == "stdio"
        assert entry["command"] == "uvx"
        assert entry["args"] == ["mcp-atlassian"]
        assert entry["env"]["JIRA_URL"] == "${JIRA_URL}"
        assert entry["env"]["JIRA_USERNAME"] == "${JIRA_USERNAME}"
        assert entry["env"]["JIRA_API_TOKEN"] == "${JIRA_API_TOKEN}"


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
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-atlassian"],
            "env": {
                "JIRA_URL": "${JIRA_URL}",
                "JIRA_USERNAME": "${JIRA_USERNAME}",
                "JIRA_API_TOKEN": "${JIRA_API_TOKEN}",
            },
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
        assert config["mcpServers"]["jira"]["type"] == "stdio"
        assert config["mcpServers"]["jira"]["command"] == "uvx"
        assert config["mcpServers"]["jira"]["env"]["JIRA_URL"] == "${JIRA_URL}"


# ===== Basic Auth / env_file Tests (T009–T013 in tasks.md) =====

# Shared Jira template fixture used across env_file tests
_JIRA_BASIC_AUTH_TEMPLATE = {
    "display_name": "Jira",
    "prompts": [
        {"key": "url", "label": "Jira workspace URL", "default": None, "validate": "url"},
        {"key": "username", "label": "Atlassian account email address", "default": None, "validate": "email"},
        {"key": "token", "label": "Jira API token (generate at id.atlassian.com/manage-profile/security/api-tokens)", "default": None},
    ],
    "env_file": {
        "vars": {"url": "JIRA_URL", "username": "JIRA_USERNAME", "token": "JIRA_API_TOKEN"},
        "gitignore": True,
    },
    "server_entry": {
        "type": "stdio",
        "command": "uvx",
        "args": ["mcp-atlassian"],
        "env": {
            "JIRA_URL": "${JIRA_URL}",
            "JIRA_USERNAME": "${JIRA_USERNAME}",
            "JIRA_API_TOKEN": "${JIRA_API_TOKEN}",
        },
    },
}


def _run_jira_setup(target, url, username, token, tracker, console):
    """Run _prompt_mcp_task_manager with Jira basic-auth template and provided credentials."""
    from rich.prompt import Prompt
    import sys

    prompt_responses = iter([url, username, token])
    with patch("specify_cli._get_mcp_templates", return_value={"jira": _JIRA_BASIC_AUTH_TEMPLATE}), \
         patch("specify_cli.select_with_arrows", return_value="jira"), \
         patch.object(Prompt, "ask", side_effect=lambda *a, **kw: next(prompt_responses)), \
         patch.object(sys.stdin, "isatty", return_value=True):
        _prompt_mcp_task_manager(target, "claude", console, tracker)


class TestJiraEnvFileCreatedFresh:
    """T009 — .env is created fresh with all three Jira credential vars."""

    def test_env_file_created_with_jira_vars(self, tmp_path, tracker, console):
        target = tmp_path / "jira-env-fresh"
        target.mkdir()

        _run_jira_setup(target, "https://test.atlassian.net", "user@example.com", "mytoken", tracker, console)

        env_path = target / ".env"
        assert env_path.exists(), ".env should be created"
        content = env_path.read_text()
        assert "JIRA_URL=https://test.atlassian.net" in content
        assert "JIRA_USERNAME=user@example.com" in content
        assert "JIRA_API_TOKEN=mytoken" in content

    def test_mcp_json_keeps_var_references(self, tmp_path, tracker, console):
        target = tmp_path / "jira-mcp-refs"
        target.mkdir()

        _run_jira_setup(target, "https://test.atlassian.net", "user@example.com", "mytoken", tracker, console)

        config = json.loads((target / ".mcp.json").read_text())
        entry = config["mcpServers"]["jira"]
        assert entry["env"]["JIRA_URL"] == "${JIRA_URL}"
        assert entry["env"]["JIRA_USERNAME"] == "${JIRA_USERNAME}"
        assert entry["env"]["JIRA_API_TOKEN"] == "${JIRA_API_TOKEN}"


class TestJiraEnvFileSkipsExistingVars:
    """T010 — existing vars in .env are never overwritten; missing ones are appended."""

    def test_existing_var_preserved_missing_appended(self, tmp_path, tracker, console):
        target = tmp_path / "jira-env-skip"
        target.mkdir()
        (target / ".env").write_text("JIRA_URL=https://old.atlassian.net\n")

        _run_jira_setup(target, "https://new.atlassian.net", "user@example.com", "mytoken", tracker, console)

        content = (target / ".env").read_text()
        assert "JIRA_URL=https://old.atlassian.net" in content, "Existing JIRA_URL must not be overwritten"
        assert "JIRA_URL=https://new.atlassian.net" not in content, "New URL must not replace old"
        assert "JIRA_USERNAME=user@example.com" in content
        assert "JIRA_API_TOKEN=mytoken" in content


class TestGitignoreUpdatedWithEnvEntry:
    """T011 — .gitignore is created/updated to include .env after Jira setup."""

    def test_gitignore_created_with_env(self, tmp_path, tracker, console):
        target = tmp_path / "jira-gitignore-create"
        target.mkdir()

        _run_jira_setup(target, "https://test.atlassian.net", "user@example.com", "tok", tracker, console)

        gitignore = target / ".gitignore"
        assert gitignore.exists(), ".gitignore should be created"
        assert ".env" in gitignore.read_text().splitlines()

    def test_gitignore_appended_when_exists(self, tmp_path, tracker, console):
        target = tmp_path / "jira-gitignore-append"
        target.mkdir()
        (target / ".gitignore").write_text("*.log\n")

        _run_jira_setup(target, "https://test.atlassian.net", "user@example.com", "tok", tracker, console)

        lines = (target / ".gitignore").read_text().splitlines()
        assert "*.log" in lines, "Existing .gitignore content must be preserved"
        assert ".env" in lines


class TestGitignoreEntryIdempotent:
    """T012 — running Jira setup twice does not duplicate .env in .gitignore."""

    def test_env_entry_not_duplicated(self, tmp_path, console):
        target = tmp_path / "jira-gitignore-idem"
        target.mkdir()

        # Call _ensure_gitignore_entry twice
        _ensure_gitignore_entry(target, ".env")
        _ensure_gitignore_entry(target, ".env")

        lines = (target / ".gitignore").read_text().splitlines()
        assert lines.count(".env") == 1, ".env must appear exactly once"

    def test_pre_existing_env_entry_not_duplicated(self, tmp_path, tracker, console):
        target = tmp_path / "jira-gitignore-pre"
        target.mkdir()
        (target / ".gitignore").write_text(".env\n*.log\n")

        _run_jira_setup(target, "https://test.atlassian.net", "user@example.com", "tok", tracker, console)

        lines = (target / ".gitignore").read_text().splitlines()
        assert lines.count(".env") == 1, ".env must appear exactly once even when pre-existing"


class TestNotionDirectSubstitutionUnchanged:
    """T013 — Notion (no env_file) still resolves credentials directly into .mcp.json."""

    def test_notion_uses_direct_substitution(self, tmp_path, tracker, console):
        target = tmp_path / "notion-compat"
        target.mkdir()

        notion_template = {
            "display_name": "Notion",
            "prompts": [
                {"key": "url", "label": "Notion MCP URL", "default": "https://mcp.notion.com/mcp"},
                {"key": "token", "label": "Notion API key", "default": None},
            ],
            "server_entry": {
                "type": "http",
                "url": "${url}",
                "headers": {"Authorization": "Bearer ${token}"},
            },
        }

        from rich.prompt import Prompt
        import sys

        prompt_responses = iter(["https://mcp.notion.com/mcp", "notion-secret"])
        with patch("specify_cli._get_mcp_templates", return_value={"notion": notion_template}), \
             patch("specify_cli.select_with_arrows", return_value="notion"), \
             patch.object(Prompt, "ask", side_effect=lambda *a, **kw: next(prompt_responses)), \
             patch.object(sys.stdin, "isatty", return_value=True):
            _prompt_mcp_task_manager(target, "claude", console, tracker)

        config = json.loads((target / ".mcp.json").read_text())
        entry = config["mcpServers"]["notion"]
        assert entry["url"] == "https://mcp.notion.com/mcp", "URL should be resolved directly"
        assert entry["headers"]["Authorization"] == "Bearer notion-secret", "Token should be resolved"
        assert not (target / ".env").exists(), "No .env should be created for Notion"


# ===== US2: Prompt Label Guidance Tests =====

class TestJiraPromptLabelsContainGuidance:
    """T014 — Jira prompt labels include guidance on where to get credentials."""

    def test_token_label_references_api_token_url(self):
        templates = _get_mcp_templates()
        assert "jira" in templates, "jira template must be discoverable"
        jira = templates["jira"]
        token_prompt = next((p for p in jira["prompts"] if p["key"] == "token"), None)
        assert token_prompt is not None
        label = token_prompt["label"].lower()
        assert "id.atlassian.com" in label or "api-tokens" in label, \
            "Token prompt must reference where to generate API tokens"

    def test_username_label_mentions_email(self):
        templates = _get_mcp_templates()
        jira = templates["jira"]
        username_prompt = next((p for p in jira["prompts"] if p["key"] == "username"), None)
        assert username_prompt is not None
        assert "email" in username_prompt["label"].lower(), \
            "Username prompt must mention 'email' to guide the user"


# ===== US3: Skip If Jira Already Configured =====

class TestMcpSkipIfKeyAlreadyExists:
    """T016 — if .mcp.json already has the selected key, setup is skipped silently."""

    def test_skip_when_jira_already_in_mcp_json(self, tmp_path, tracker, console):
        target = tmp_path / "jira-skip-existing"
        target.mkdir()

        existing_entry = {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-atlassian"],
            "env": {"JIRA_URL": "${JIRA_URL}", "JIRA_USERNAME": "${JIRA_USERNAME}", "JIRA_API_TOKEN": "${JIRA_API_TOKEN}"},
        }
        original_config = {"mcpServers": {"jira": existing_entry}}
        original_text = json.dumps(original_config)
        (target / ".mcp.json").write_text(original_text)

        from rich.prompt import Prompt
        import sys

        mock_prompt = MagicMock()
        with patch("specify_cli._get_mcp_templates", return_value={"jira": _JIRA_BASIC_AUTH_TEMPLATE}), \
             patch("specify_cli.select_with_arrows", return_value="jira"), \
             patch.object(Prompt, "ask", side_effect=mock_prompt), \
             patch.object(sys.stdin, "isatty", return_value=True):
            _prompt_mcp_task_manager(target, "claude", console, tracker)

        mock_prompt.assert_not_called(), "Prompt.ask must not be called when entry already exists"
        assert (target / ".mcp.json").read_text() == original_text, \
            ".mcp.json must be byte-for-byte unchanged"


# ===== Validation Helper Tests =====

class TestValidatePromptValue:
    """Unit tests for _validate_prompt_value helper."""

    def test_valid_https_url(self):
        ok, msg = _validate_prompt_value("https://test.atlassian.net", "url")
        assert ok is True
        assert msg == ""

    def test_invalid_url_missing_scheme(self):
        ok, msg = _validate_prompt_value("test.atlassian.net", "url")
        assert ok is False
        assert "https://" in msg

    def test_http_url_rejected(self):
        ok, msg = _validate_prompt_value("http://test.atlassian.net", "url")
        assert ok is False

    def test_valid_email(self):
        ok, msg = _validate_prompt_value("user@example.com", "email")
        assert ok is True

    def test_email_with_plus(self):
        ok, msg = _validate_prompt_value("user+tag@example.com", "email")
        assert ok is True

    def test_invalid_email_no_at(self):
        ok, msg = _validate_prompt_value("notanemail.com", "email")
        assert ok is False

    def test_invalid_email_no_domain_dot(self):
        ok, msg = _validate_prompt_value("user@nodot", "email")
        assert ok is False

    def test_unknown_rule_passes(self):
        ok, msg = _validate_prompt_value("anything", "unknown_rule")
        assert ok is True
