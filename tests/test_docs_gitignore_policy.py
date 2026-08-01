"""Regression tests for docs/ ignore policy."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git_check_ignore(
    path: str, *, no_index: bool = False
) -> subprocess.CompletedProcess[str]:
    # fork delta: `git check-ignore` never reports a TRACKED path as ignored,
    # because ignore rules do not apply to tracked files. This fork deliberately
    # force-adds AGENTS.local.md so it survives a worktree rebuild, which flips
    # the tracked-aware answer. `--no-index` asks the narrower question the
    # policy actually cares about — "does .gitignore still cover this pattern?"
    args = ["git", "check-ignore", "-q"]
    if no_index:
        args.append("--no-index")
    args.append(path)
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_new_top_level_markdown_docs_are_trackable():
    """New docs/*.md files should be visible to Git, not silently ignored."""
    assert _git_check_ignore("docs/example-new-guide.md").returncode == 1


def test_root_agents_entrypoint_is_trackable():
    """AGENTS.md is the shared repo entrypoint; local overrides stay ignored."""
    assert _git_check_ignore("AGENTS.md").returncode == 1
    assert _git_check_ignore("AGENTS.local.md", no_index=True).returncode == 0


def test_docs_scratch_files_remain_ignored():
    """The broad docs/* ignore rule should still keep arbitrary scratch files out."""
    assert _git_check_ignore("docs/local-scratch.tmp").returncode == 0


def test_local_only_ai_context_files_remain_ignored_under_docs():
    """Local AI assistant context files must stay out of commits under docs/."""
    assert _git_check_ignore("docs/AGENTS.md").returncode == 0
    assert _git_check_ignore("docs/CLAUDE.md").returncode == 0
    assert _git_check_ignore("docs/.cursorrules").returncode == 0
    assert _git_check_ignore("docs/.windsurfrules").returncode == 0
