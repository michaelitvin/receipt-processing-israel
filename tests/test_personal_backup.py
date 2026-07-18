"""Integration tests for tools/personal_backup.py (overlay backup of *.personal.* files).

Hermetic: local bare repos as remotes, temp global gitconfig, no network.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "personal_backup.py"


def git(*args, cwd, env):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True, text=True
    )


def run_script(*args, cwd, env, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd), env=env, input=stdin, capture_output=True, text=True,
    )


@pytest.fixture
def env(tmp_path):
    cfg = tmp_path / "gitconfig"
    cfg.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n"
        "[core]\n\tautocrlf = false\n"
        "[init]\n\tdefaultBranch = main\n"
    )
    e = dict(os.environ)
    e.update({"GIT_CONFIG_GLOBAL": str(cfg), "GIT_CONFIG_NOSYSTEM": "1"})
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(var, None)
    return e


@pytest.fixture
def private_remote(tmp_path, env):
    """Bare repo standing in for the private GitHub repo, seeded like the real one:
    a README.personal.md (private front-page/notes file) plus personal files at their
    project-relative paths."""
    remote = tmp_path / "private-remote.git"
    git("init", "--bare", str(remote), cwd=tmp_path, env=env)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", cwd=seed, env=env)
    (seed / "README.personal.md").write_text("# private backup readme\n")
    (seed / "AUDIT.personal.md").write_text("audit v1\n")
    nested = seed / "docs" / "sub"
    nested.mkdir(parents=True)
    (nested / "NOTES.personal.md").write_text("notes v1\n")
    git("add", "-A", cwd=seed, env=env)
    git("commit", "-m", "seed", cwd=seed, env=env)
    git("push", str(remote), "main", cwd=seed, env=env)
    return remote


@pytest.fixture
def project(tmp_path, env):
    """Stand-in for the public repo clone: own README, code, gitignore."""
    proj = tmp_path / "project"
    proj.mkdir()
    git("init", cwd=proj, env=env)
    (proj / "README.md").write_text("# public readme\n")
    (proj / "app.py").write_text("print('hi')\n")
    (proj / ".gitignore").write_text("*.personal.*\n.git-personal/\n")
    git("add", "-A", cwd=proj, env=env)
    git("commit", "-m", "init", cwd=proj, env=env)
    return proj


class TestBackupNoOp:
    def test_backup_without_overlay_is_silent_noop(self, project, env):
        r = run_script("backup", cwd=project, env=env)
        assert r.returncode == 0
        assert r.stdout == ""
        assert not (project / ".git-personal").exists()

    def test_backup_outside_git_repo_is_silent_noop(self, tmp_path, env):
        plain = tmp_path / "plain"
        plain.mkdir()
        r = run_script("backup", cwd=plain, env=env)
        assert r.returncode == 0


def overlay_status(proj, env):
    return git("--git-dir", str(proj / ".git-personal"), "--work-tree", str(proj),
               "status", "--porcelain", cwd=proj, env=env).stdout


class TestSetup:
    def test_setup_restores_personal_files_fresh_machine(self, project, private_remote, env):
        r = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert r.returncode == 0, r.stderr
        assert (project / "AUDIT.personal.md").read_text() == "audit v1\n"
        assert (project / "docs" / "sub" / "NOTES.personal.md").read_text() == "notes v1\n"

    def test_setup_restores_personal_readme(self, project, private_remote, env):
        run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert (project / "README.personal.md").read_text() == "# private backup readme\n"

    def test_setup_leaves_public_readme_untouched(self, project, private_remote, env):
        run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert (project / "README.md").read_text() == "# public readme\n"

    def test_setup_leaves_both_repos_clean(self, project, private_remote, env):
        run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert git("status", "--porcelain", cwd=project, env=env).stdout == ""
        assert overlay_status(project, env) == ""

    def test_setup_sets_alias_and_hookspath(self, project, private_remote, env):
        run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        alias = git("config", "alias.personal", cwd=project, env=env).stdout.strip()
        assert alias == "!git --git-dir=.git-personal --work-tree=."
        hooks = git("config", "core.hooksPath", cwd=project, env=env).stdout.strip()
        assert hooks == ".githooks"

    def test_setup_is_idempotent_and_keeps_public_readme(self, project, private_remote, env):
        # Two setups must both succeed, leave the overlay clean, and never delete or
        # modify the public README.md (guards the sparse-checkout data-loss bug the
        # skip-worktree/README.personal.md redesign fixes).
        r1 = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        r2 = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert r1.returncode == 0 and r2.returncode == 0, r2.stderr
        assert overlay_status(project, env) == ""
        assert (project / "README.md").read_text() == "# public readme\n"
        assert git("status", "--porcelain", cwd=project, env=env).stdout == ""

    def test_setup_refuses_to_overwrite_differing_local_file(self, project, private_remote, env):
        (project / "AUDIT.personal.md").write_text("local newer edits\n")
        r = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert r.returncode == 1
        assert "AUDIT.personal.md" in r.stderr
        assert (project / "AUDIT.personal.md").read_text() == "local newer edits\n"

    def test_setup_force_overwrites_with_backup_version(self, project, private_remote, env):
        (project / "AUDIT.personal.md").write_text("local newer edits\n")
        r = run_script("setup", "--remote", str(private_remote), "--force",
                       cwd=project, env=env)
        assert r.returncode == 0, r.stderr
        assert (project / "AUDIT.personal.md").read_text() == "audit v1\n"

    def test_setup_accepts_identical_local_files(self, project, private_remote, env):
        (project / "AUDIT.personal.md").write_text("audit v1\n")
        r = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
        assert r.returncode == 0, r.stderr


def remote_head_subject(remote, env):
    return git("--git-dir", str(remote), "log", "-1", "--format=%s",
               cwd=remote.parent, env=env).stdout.strip()


def remote_commit_count(remote, env):
    return int(git("--git-dir", str(remote), "rev-list", "--count", "main",
                   cwd=remote.parent, env=env).stdout.strip())


def remote_file(remote, rel, env):
    return git("--git-dir", str(remote), "show", f"main:{rel}",
               cwd=remote.parent, env=env).stdout


@pytest.fixture
def overlay_project(project, private_remote, env):
    r = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
    assert r.returncode == 0, r.stderr
    return project


class TestBackup:
    def test_backup_commits_and_pushes_changed_personal_file(
        self, overlay_project, private_remote, env
    ):
        (overlay_project / "AUDIT.personal.md").write_text("audit v2\n")
        r = run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert r.returncode == 0, r.stderr
        assert remote_head_subject(private_remote, env) == "backup: personal files"
        assert remote_file(private_remote, "AUDIT.personal.md", env) == "audit v2\n"

    def test_backup_picks_up_new_personal_file(self, overlay_project, private_remote, env):
        (overlay_project / "NEW.personal.yaml").write_text("k: v\n")
        run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert remote_file(private_remote, "NEW.personal.yaml", env) == "k: v\n"

    def test_backup_picks_up_new_nested_personal_file(self, overlay_project, private_remote, env):
        nested = overlay_project / "docs" / "newsub"
        nested.mkdir(parents=True)
        (nested / "X.personal.md").write_text("nested new\n")
        run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert remote_file(private_remote, "docs/newsub/X.personal.md", env) == "nested new\n"

    def test_backup_without_changes_creates_no_commit(
        self, overlay_project, private_remote, env
    ):
        before = remote_commit_count(private_remote, env)
        r = run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert r.returncode == 0, r.stderr
        assert remote_commit_count(private_remote, env) == before

    def test_backup_after_setup_keeps_personal_readme_on_remote(
        self, overlay_project, private_remote, env
    ):
        # README.personal.md is a normal materialized personal file; a no-op backup
        # must not stage a spurious change/deletion that alters it on the remote.
        run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert remote_file(private_remote, "README.personal.md", env) == "# private backup readme\n"

    def test_backup_unstages_non_personal_paths(self, overlay_project, private_remote, env):
        (overlay_project / "stray.txt").write_text("not personal\n")
        git("--git-dir", str(overlay_project / ".git-personal"),
            "--work-tree", str(overlay_project),
            "add", "-f", "stray.txt", cwd=overlay_project, env=env)
        (overlay_project / "AUDIT.personal.md").write_text("audit v3\n")
        run_script("backup", "--wait", cwd=overlay_project, env=env)
        files = git("--git-dir", str(private_remote), "ls-tree", "-r", "--name-only", "main",
                    cwd=private_remote.parent, env=env).stdout.splitlines()
        assert "stray.txt" not in files
        assert remote_file(private_remote, "AUDIT.personal.md", env) == "audit v3\n"

    def test_backup_writes_log(self, overlay_project, env):
        (overlay_project / "AUDIT.personal.md").write_text("audit v4\n")
        run_script("backup", "--wait", cwd=overlay_project, env=env)
        assert (overlay_project / ".git-personal" / "backup.log").exists()


class TestClaudeHook:
    def test_non_personal_edit_does_not_trigger_backup(
        self, overlay_project, private_remote, env
    ):
        (overlay_project / "AUDIT.personal.md").write_text("pending change\n")
        payload = json.dumps({"tool_input": {"file_path": str(overlay_project / "app.py")}})
        r = run_script("backup", "--claude-hook", "--wait",
                       cwd=overlay_project, env=env, stdin=payload)
        assert r.returncode == 0
        assert remote_file(private_remote, "AUDIT.personal.md", env) == "audit v1\n"

    def test_personal_edit_triggers_backup(self, overlay_project, private_remote, env):
        (overlay_project / "AUDIT.personal.md").write_text("edited by claude\n")
        path = str(overlay_project / "AUDIT.personal.md").replace("/", "\\")  # windows-style
        payload = json.dumps({"tool_input": {"file_path": path}})
        r = run_script("backup", "--claude-hook", "--wait",
                       cwd=overlay_project, env=env, stdin=payload)
        assert r.returncode == 0
        assert remote_file(private_remote, "AUDIT.personal.md", env) == "edited by claude\n"

    def test_malformed_stdin_exits_zero(self, overlay_project, env):
        r = run_script("backup", "--claude-hook", cwd=overlay_project, env=env,
                       stdin="not json at all")
        assert r.returncode == 0


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def hooked_project(project, private_remote, env):
    """Project with the real shim + script installed, overlay set up (which activates
    core.hooksPath), and the test's python forced via PERSONAL_BACKUP_PYTHON."""
    tools = project / "tools"
    tools.mkdir()
    (tools / "personal_backup.py").write_bytes(SCRIPT.read_bytes())
    hooks = project / ".githooks"
    hooks.mkdir()
    (hooks / "post-commit").write_bytes((REPO_ROOT / ".githooks" / "post-commit").read_bytes())
    r = run_script("setup", "--remote", str(private_remote), cwd=project, env=env)
    assert r.returncode == 0, r.stderr
    env = dict(env)
    env["PERSONAL_BACKUP_PYTHON"] = sys.executable
    return project, env


class TestPostCommitHook:
    def test_public_commit_triggers_personal_backup(self, hooked_project, env):
        project, hook_env = hooked_project
        (project / "AUDIT.personal.md").write_text("changed alongside code\n")
        (project / "app.py").write_text("print('v2')\n")
        git("add", "app.py", cwd=project, env=hook_env)
        git("commit", "-m", "public change", cwd=project, env=hook_env)
        subject = git("--git-dir", str(project / ".git-personal"), "log", "-1", "--format=%s",
                      cwd=project, env=env).stdout.strip()
        assert subject == "backup: personal files"

    def test_hook_is_noop_without_overlay(self, project, env):
        # Same shim installed, hooksPath forced, but no .git-personal: commit must succeed.
        hooks = project / ".githooks"
        hooks.mkdir()
        (hooks / "post-commit").write_bytes((REPO_ROOT / ".githooks" / "post-commit").read_bytes())
        git("config", "core.hooksPath", ".githooks", cwd=project, env=env)
        (project / "app.py").write_text("print('v3')\n")
        git("add", "app.py", cwd=project, env=env)
        git("commit", "-m", "no overlay", cwd=project, env=env)
        assert not (project / ".git-personal").exists()
