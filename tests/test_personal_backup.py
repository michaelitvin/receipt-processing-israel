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
    a README.md (GitHub front page) plus personal files at their project-relative paths."""
    remote = tmp_path / "private-remote.git"
    git("init", "--bare", str(remote), cwd=tmp_path, env=env)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", cwd=seed, env=env)
    (seed / "README.md").write_text("# private backup readme\n")
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
