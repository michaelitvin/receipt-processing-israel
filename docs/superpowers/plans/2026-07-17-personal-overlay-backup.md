# Personal Files Overlay Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Version-track and auto-backup the gitignored `*.personal.*` files in place, via a second git-dir (`.git-personal/`) over the same working tree, pushing to the existing private repo.

**Architecture:** A private "overlay" git-dir shares the project working tree; its `info/exclude` tracks only `*.personal.*` files. The private repo's own front-page/notes file is named `README.personal.md` (not `README.md`), so it is just another `*.personal.*` file — it materializes into the worktree, is editable in place, and never collides with the public repo's `README.md`. No sparse-checkout or skip-worktree exclusion is needed. A stdlib-Python script (`tools/personal_backup.py`) implements `backup` (stage → commit → detached async push) and `setup` (clone + configure + restore). Two triggers call `backup`: a tracked sh post-commit shim (active only after `setup` sets `core.hooksPath`) and a Claude Code `PostToolUse` hook.

> **DESIGN REVISION (2026-07-18, during execution):** The original plan hid the private repo's `README.md` from the shared worktree via `core.sparseCheckout`. Execution proved that mechanism has a data-loss bug — a second `setup` deletes the real public `README.md` from the working tree (git's sparse-checkout refuses to skip a pre-existing conflicting file on the first run, then removes it on the second). The private "front page" README is therefore renamed to `README.personal.md`, eliminating the collision and the entire exclusion mechanism (no `core.sparseCheckout`, no `info/sparse-checkout`, no skip-worktree, no `SPARSE_EXCLUDED` constant). Trade-off accepted by the repo owner: GitHub does not auto-render `README.personal.md` as the private repo's landing page (only exact `readme[.ext]` names render), so the private repo shows a file list instead. Tasks 1, 2, 4, 7, 8, 9 below are revised accordingly; where a task's original code/text conflicts with this note, this note governs.

**Tech Stack:** Python 3.13 stdlib only, POSIX sh, git, pytest (integration tests against real local git repos — no network).

Spec: `docs/superpowers/specs/2026-07-17-personal-overlay-backup-design.md`

## Global Constraints

- `tools/personal_backup.py` uses ONLY the Python standard library.
- No PowerShell, no Windows-only tooling. Shell code is POSIX sh; Python handles platform differences internally.
- No personal content (PII, personal facts) in ANY public-repo file, including docs, tests, and this plan's artifacts. File *names* like `AUDIT_KNOWLEDGE.personal.md` are already public and fine to mention.
- Git config writes are per-clone only (plain `git config`, never `--global` or `--system`).
- `backup` (and both hooks) ALWAYS exit 0 — failures are logged to `.git-personal/backup.log`, never break a commit or a Claude session.
- Private remote: `git@github.com:michaelitvin/receipt-processing-israel-personal.git`, branch `main`.
- Everything tracked in the private repo matches `*.personal.*` (including `README.personal.md`); there is no worktree-exclusion mechanism.
- Tests are hermetic: local bare repos as remotes, `GIT_CONFIG_GLOBAL` pointed at a temp gitconfig (`autocrlf=false`, test identity), no network, no dependence on the developer's real config.
- Test command: `uv run pytest tests/test_personal_backup.py -v` (run the full `uv run pytest tests/` before each commit).

---

### Task 1: Script skeleton + `backup` no-op safety + test harness

**Files:**
- Create: `tools/personal_backup.py`
- Test: `tests/test_personal_backup.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces (used by every later task):
  - CLI: `python tools/personal_backup.py backup [--wait] [--claude-hook]` and `... setup [--remote URL] [--force]` (subcommands wired now; `setup` body lands in Task 2, backup commit/push body in Task 4).
  - Python internals: `repo_root() -> Path | None`, `scrub_env() -> dict`, `run(args, cwd, check=True, capture=True, binary=False)`, `overlay_git(root, *args, check=True)`, constants `PRIVATE_REMOTE_URL`, `BRANCH = "main"`, `PERSONAL_GLOB = "*.personal.*"`, `OVERLAY_DIR = ".git-personal"`. (The originally-committed `SPARSE_EXCLUDED = ("README.md",)` constant is **removed** by the revised Task 2 — see the DESIGN REVISION note; it is no longer used.)
  - Test helpers in `tests/test_personal_backup.py` (module-level, reused by all later tests): `git(*args, cwd, env)`, `run_script(*args, cwd, env, stdin=None)`, fixtures `env`, `private_remote`, `project`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_personal_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: FAIL — `tools/personal_backup.py` does not exist (non-zero returncode / FileNotFoundError from subprocess).

- [ ] **Step 3: Write the skeleton implementation**

Create `tools/personal_backup.py`:

```python
#!/usr/bin/env python3
"""Backup/restore the gitignored *.personal.* files via a private overlay repo.

A second git-dir (.git-personal/) shares this project's working tree and tracks
ONLY *.personal.* files, pushing to the private backup repo. See
docs/PERSONAL_BACKUP.md for the full mechanism. Stdlib only.

Commands:
    backup [--wait] [--claude-hook]   stage/commit personal changes, async push
    setup  [--remote URL] [--force]   bootstrap the overlay on this clone
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PRIVATE_REMOTE_URL = "git@github.com:michaelitvin/receipt-processing-israel-personal.git"
BRANCH = "main"
PERSONAL_GLOB = "*.personal.*"
SPARSE_EXCLUDED = ("README.md",)  # on GitHub as the private repo's front page; never in the worktree
OVERLAY_DIR = ".git-personal"


def scrub_env() -> dict:
    # Git hooks export GIT_DIR etc.; they must not leak into overlay-repo commands.
    env = dict(os.environ)
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return env


def run(args, cwd, check=True, capture=True, binary=False):
    return subprocess.run(
        args, cwd=str(cwd), env=scrub_env(), check=check,
        capture_output=capture, text=not binary,
    )


def repo_root() -> Path | None:
    try:
        out = run(["git", "rev-parse", "--show-toplevel"], cwd=Path.cwd())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(out.stdout.strip())


def overlay_git(root: Path, *args, check=True):
    git_dir = root / OVERLAY_DIR
    return run(
        ["git", "--git-dir", str(git_dir), "--work-tree", str(root), *args],
        cwd=root, check=check,
    )


def _log(log_path: Path, message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")


def cmd_backup(root: Path, wait: bool = False) -> int:
    if root is None or not (root / OVERLAY_DIR).is_dir():
        return 0
    return 0  # commit/push implemented in a later task


def cmd_setup(root: Path, remote: str, force: bool) -> int:
    raise NotImplementedError  # implemented in a later task


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_backup = sub.add_parser("backup", help="commit personal-file changes, async push")
    p_backup.add_argument("--wait", action="store_true", help="wait for the push (tests)")
    p_backup.add_argument("--claude-hook", action="store_true",
                          help="read PostToolUse JSON on stdin; only act on personal files")
    p_setup = sub.add_parser("setup", help="bootstrap the overlay repo on this clone")
    p_setup.add_argument("--remote", default=PRIVATE_REMOTE_URL)
    p_setup.add_argument("--force", action="store_true",
                         help="overwrite local personal files that differ from the backup")
    args = parser.parse_args(argv)

    root = repo_root()
    if args.cmd == "backup":
        return cmd_backup(root, wait=args.wait)
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    return cmd_setup(root, remote=args.remote, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
```

(`--claude-hook` is parsed but not yet honored; Task 5 wires it. `fnmatch`, `json`, `shutil` imports are used by Tasks 2–5.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/personal_backup.py tests/test_personal_backup.py
git commit -m "feat: personal_backup script skeleton with no-op backup safety"
```

---

### Task 2: `setup` — clone overlay, configure, restore

**Files:**
- Modify: `tools/personal_backup.py` (replace `cmd_setup` stub; add helpers)
- Test: `tests/test_personal_backup.py`

**Interfaces:**
- Consumes: Task 1 helpers (`run`, `overlay_git`, constants) and test fixtures.
- Produces: working `setup --remote <url>`; after it runs, `.git-personal/` exists, personal files (including `README.personal.md`) are restored, `git config alias.personal` and `core.hooksPath=.githooks` are set on the public clone. Helper `_tracked_personal_files(root) -> list[str]` and `_norm(b: bytes) -> bytes` are reused by Task 3.
- **REVISED (see DESIGN REVISION note):** No `core.sparseCheckout`, no `info/sparse-checkout`, no skip-worktree. This step also **removes** the now-unused `SPARSE_EXCLUDED` constant added by Task 1, and `_tracked_personal_files` returns every tracked file (all are `*.personal.*`, no filtering). The Task 1 `private_remote` fixture is updated to seed `README.personal.md` (not `README.md`).

- [ ] **Step 1a: Update the `private_remote` fixture (rename the seeded README)**

In the existing `private_remote` fixture (added in Task 1), rename the seeded front-page file from `README.md` to `README.personal.md` (keep the same content). Also update the fixture's docstring wording from "a README.md (GitHub front page)" to "a README.personal.md (private front-page/notes file)":

```python
    (seed / "README.personal.md").write_text("# private backup readme\n")
    (seed / "AUDIT.personal.md").write_text("audit v1\n")
```

(The `project` fixture keeps its own public `README.md` — that file is what must stay untouched.)

- [ ] **Step 1b: Write the failing tests**

Append to `tests/test_personal_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_personal_backup.py -v -k TestSetup`
Expected: 6 FAIL (NotImplementedError → returncode 1).

- [ ] **Step 3: Implement `cmd_setup` (and delete the `SPARSE_EXCLUDED` constant)**

First delete the now-unused constant line from the top of `tools/personal_backup.py`:

```python
SPARSE_EXCLUDED = ("README.md",)  # on GitHub as the private repo's front page; never in the worktree
```

Then replace the `cmd_setup` stub with:

```python
def _tracked_personal_files(root: Path) -> list[str]:
    out = run(
        ["git", "--git-dir", str(root / OVERLAY_DIR),
         "ls-tree", "-r", "--name-only", "-z", BRANCH],
        cwd=root,
    )
    return [n for n in out.stdout.split("\0") if n]


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _blob_bytes(root: Path, rel: str) -> bytes:
    out = run(
        ["git", "--git-dir", str(root / OVERLAY_DIR), "cat-file", "-p", f"{BRANCH}:{rel}"],
        cwd=root, binary=True,
    )
    return out.stdout


def cmd_setup(root: Path, remote: str, force: bool) -> int:
    overlay = root / OVERLAY_DIR

    if not overlay.is_dir():
        tmp = root / f"{OVERLAY_DIR}-clone-tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        run(["git", "clone", "--no-checkout", remote, str(tmp)], cwd=root)
        (tmp / ".git").rename(overlay)
        shutil.rmtree(tmp)

    # Overlay repo config: worktree is the project root; track only personal files.
    # The private repo's own README is README.personal.md (a *.personal.* file), so it
    # never collides with the public README.md — no sparse-checkout/skip-worktree needed.
    overlay_git(root, "config", "core.worktree", "..")
    info = overlay / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text(f"*\n!*/\n!{PERSONAL_GLOB}\n", encoding="utf-8")

    # Public clone config (per-clone, never --global).
    run(["git", "config", "alias.personal", "!git --git-dir=.git-personal --work-tree=."],
        cwd=root)
    run(["git", "config", "core.hooksPath", ".githooks"], cwd=root)

    # Refuse to clobber local personal files that differ from the backup.
    conflicts = []
    for rel in _tracked_personal_files(root):
        path = root / rel
        if path.exists() and _norm(path.read_bytes()) != _norm(_blob_bytes(root, rel)):
            conflicts.append(rel)
    if conflicts and not force:
        print("error: local personal files differ from the backup:", file=sys.stderr)
        for rel in conflicts:
            print(f"  {rel}", file=sys.stderr)
        print("Run backup from the machine holding the latest versions first, or "
              "re-run setup with --force to overwrite local files.", file=sys.stderr)
        return 1

    overlay_git(root, "checkout", "-f", BRANCH)
    print(f"Overlay ready: {len(_tracked_personal_files(root))} personal file(s) tracked. "
          f"Use 'git personal status|log|diff'.")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: all PASS (8 total). `test_setup_leaves_public_readme_untouched`, `test_setup_leaves_both_repos_clean`, and `test_setup_is_idempotent_and_keeps_public_readme` are the load-bearing ones — if any fails, the overlay would corrupt the public worktree; do not weaken them to pass.

- [ ] **Step 5: Commit**

```bash
git add tools/personal_backup.py tests/test_personal_backup.py
git commit -m "feat: personal_backup setup - overlay clone, excludes, restore (README.personal.md, no sparse-checkout)"
```

---

### Task 3: `setup` conflict safety (`--force`)

**Files:**
- Modify: `tests/test_personal_backup.py` (tests only — Task 2 already implemented the logic; these tests pin it)

**Interfaces:**
- Consumes: `setup --remote/--force` from Task 2.
- Produces: verified contract: differing local file → exit 1 + untouched file; `--force` → remote wins.

- [ ] **Step 1: Write the tests**

Append to `tests/test_personal_backup.py` inside `class TestSetup`:

```python
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
```

- [ ] **Step 2: Run tests — expected to pass already (logic landed in Task 2)**

Run: `uv run pytest tests/test_personal_backup.py -v -k TestSetup`
Expected: all PASS. If any fail, fix `cmd_setup` (not the tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_personal_backup.py
git commit -m "test: pin setup conflict-safety contract (--force semantics)"
```

---

### Task 4: `backup` — commit + async push + guards

**Files:**
- Modify: `tools/personal_backup.py` (replace `cmd_backup` body; add `_spawn_push`)
- Test: `tests/test_personal_backup.py`

**Interfaces:**
- Consumes: Task 2's `setup` (tests bootstrap with it), `overlay_git`, `_log`.
- Produces: working `backup [--wait]`: commits `backup: personal files`, pushes `origin main` as a detached child (synchronous only under `--wait`), logs to `.git-personal/backup.log`, always exits 0. Task 5 and the hooks call `cmd_backup` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_personal_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_personal_backup.py -v -k TestBackup`
Expected: FAIL — backup is still a no-op, so no commit reaches the remote.

- [ ] **Step 3: Implement `cmd_backup` + `_spawn_push`**

In `tools/personal_backup.py`, replace `cmd_backup` with:

```python
def _spawn_push(root: Path, log_path: Path, wait: bool) -> None:
    _log(log_path, "push: starting")
    log_file = open(log_path, "ab")
    kwargs = {}
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        ["git", "--git-dir", str(root / OVERLAY_DIR), "push", "--quiet", "origin", BRANCH],
        cwd=str(root), env=scrub_env(),
        stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
        **kwargs,
    )
    log_file.close()
    if wait:
        proc.wait()


def cmd_backup(root: Path, wait: bool = False) -> int:
    if root is None or not (root / OVERLAY_DIR).is_dir():
        return 0
    log_path = root / OVERLAY_DIR / "backup.log"
    try:
        overlay_git(root, "add", "-A")
        staged = [
            p for p in overlay_git(root, "diff", "--cached", "--name-only").stdout.splitlines()
            if p
        ]
        stray = [
            p for p in staged
            if not fnmatch.fnmatch(p.replace("\\", "/").rsplit("/", 1)[-1], PERSONAL_GLOB)
        ]
        if stray:
            overlay_git(root, "reset", "--quiet", "--", *stray)
            _log(log_path, f"WARNING: unstaged non-personal paths: {', '.join(stray)}")
            staged = [p for p in staged if p not in stray]
        if staged:
            overlay_git(root, "commit", "--quiet", "-m", "backup: personal files")
            _log(log_path, f"commit: {len(staged)} file(s)")
        ahead = overlay_git(
            root, "rev-list", "--count", f"origin/{BRANCH}..{BRANCH}"
        ).stdout.strip()
        if ahead != "0":
            _spawn_push(root, log_path, wait)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        _log(log_path, f"ERROR: {exc.cmd}: {detail}")
        print(f"personal_backup: error logged to {log_path}", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: all PASS. `test_backup_after_setup_keeps_personal_readme_on_remote` guards that a no-op backup doesn't alter `README.personal.md` on the remote; if it fails, fix the staging logic in `backup`, never the test.

- [ ] **Step 5: Commit**

```bash
git add tools/personal_backup.py tests/test_personal_backup.py
git commit -m "feat: personal_backup backup - commit, guarded staging, detached async push"
```

---

### Task 5: `--claude-hook` stdin filter

**Files:**
- Modify: `tools/personal_backup.py` (add `cmd_claude_hook`; wire flag in `main`)
- Test: `tests/test_personal_backup.py`

**Interfaces:**
- Consumes: `cmd_backup` from Task 4.
- Produces: `backup --claude-hook` reads Claude Code PostToolUse JSON (`{"tool_input": {"file_path": ...}}`) from stdin; runs a backup only when the edited file matches `*.personal.*`; exits 0 on any malformed input. Task 6's `.claude/settings.json` invokes exactly `uv run python tools/personal_backup.py backup --claude-hook`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_personal_backup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_personal_backup.py -v -k TestClaudeHook`
Expected: `test_non_personal_edit_does_not_trigger_backup` FAILS (flag currently ignored, so the pending personal change gets backed up). The other two may pass incidentally — that's fine.

- [ ] **Step 3: Implement the filter**

In `tools/personal_backup.py` add:

```python
def cmd_claude_hook(root: Path, wait: bool = False) -> int:
    try:
        payload = json.load(sys.stdin)
        file_path = str(payload.get("tool_input", {}).get("file_path", ""))
    except Exception:
        return 0
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    if not fnmatch.fnmatch(name, PERSONAL_GLOB):
        return 0
    return cmd_backup(root, wait=wait)
```

and in `main()` change the backup dispatch to:

```python
    if args.cmd == "backup":
        if args.claude_hook:
            return cmd_claude_hook(root, wait=args.wait)
        return cmd_backup(root, wait=args.wait)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/personal_backup.py tests/test_personal_backup.py
git commit -m "feat: personal_backup --claude-hook filters PostToolUse events"
```

---

### Task 6: Triggers — post-commit shim, `.gitignore`, `.claude/settings.json`

**Files:**
- Create: `.githooks/post-commit`
- Create: `.claude/settings.json`
- Modify: `.gitignore` (append `.git-personal/` under the existing personal-files section)
- Test: `tests/test_personal_backup.py`

**Interfaces:**
- Consumes: `backup` CLI (Task 4), `setup` setting `core.hooksPath=.githooks` (Task 2).
- Produces: a public `git commit` on a setup clone auto-backs-up personal changes. Shim honors `PERSONAL_BACKUP_PYTHON` env override (used by tests; falls back to `.venv` python, then `python3`/`python`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_personal_backup.py`:

```python
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
```

- [ ] **Step 2: Create the shim and config files**

Create `.githooks/post-commit` (LF line endings, no BOM):

```sh
#!/bin/sh
# Auto-backup *.personal.* files to the private overlay repo (docs/PERSONAL_BACKUP.md).
# Active only on clones that ran personal_backup.py setup (it sets core.hooksPath).
# Must never fail the commit: always exits 0.
cd "$(git rev-parse --show-toplevel)" || exit 0
[ -d .git-personal ] || exit 0
PY="${PERSONAL_BACKUP_PYTHON:-}"
if [ -z "$PY" ]; then
    if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
    elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
    else PY="$(command -v python3 || command -v python)"; fi
fi
[ -n "$PY" ] && "$PY" tools/personal_backup.py backup
exit 0
```

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "uv run python tools/personal_backup.py backup --claude-hook"
          }
        ]
      }
    ]
  }
}
```

Append to `.gitignore`, directly under the existing `*.personal.*` line:

```
# Private overlay repo tracking the personal files (see docs/PERSONAL_BACKUP.md)
.git-personal/
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_personal_backup.py -v`
Expected: all PASS. If `test_public_commit_triggers_personal_backup` fails on Windows with "cannot spawn .githooks/post-commit", check the shim was written with LF endings (`git ls-files --eol .githooks/post-commit` in a scratch add, or re-save with LF).

- [ ] **Step 4: Full test suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS (existing receipt_checks/audit_batch/image_handler tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add .githooks/post-commit .claude/settings.json .gitignore tests/test_personal_backup.py
git commit -m "feat: wire personal backup triggers - post-commit shim and Claude Code hook"
```

---

### Task 7: Public documentation

**Files:**
- Create: `docs/PERSONAL_BACKUP.md`
- Modify: `CLAUDE.md` (add a section after "Important Notes")

**Interfaces:**
- Consumes: everything built in Tasks 1–6 (documents it).
- Produces: the discoverability guarantee — a fresh clone (human or Claude Code) learns the overlay exists and how to use it. NO personal content; mechanism only.

- [ ] **Step 1: Create `docs/PERSONAL_BACKUP.md`**

```markdown
# Personal Files Backup (Overlay Repo)

`*.personal.*` files hold personal context (audit knowledge, prompt additions,
recurring-vendor data). They are gitignored here — this repo is public and personal
data must never enter its history. They are instead version-tracked **in place** by a
second git repository that shares this working tree: a private "overlay" git-dir at
`.git-personal/` (itself gitignored), pushing to the private repo
`github.com/michaelitvin/receipt-processing-israel-personal`.

## How it works

A git repository is a git-dir plus a working tree; two git-dirs can share one tree.
The overlay's `info/exclude` ignores everything except `*.personal.*`, so the two
repos track disjoint file sets and both always show clean status. Plain `git`
commands always resolve to the public repo; the overlay is reached only via the
`git personal` alias (set up locally as `git --git-dir=.git-personal --work-tree=.`).

The private repo's own front-page/notes file is named `README.personal.md`, so it is
just another `*.personal.*` file — it materializes into the working tree, is editable
in place, and never collides with the public repo's `README.md`. No sparse-checkout or
skip-worktree is involved. Everything tracked privately matches `*.personal.*`. (One
trade-off: GitHub only auto-renders a landing page for a file literally named
`README.md`/`README`, so the private repo shows a file list rather than a rendered
`README.personal.md`.)

## Automatic backups

`tools/personal_backup.py backup` stages/commits personal changes and pushes in a
detached background process (a trigger never waits on the network). Push results land
in `.git-personal/backup.log`; failures are silent and retried on the next trigger.
Two triggers call it:

- `.githooks/post-commit` — after every public commit. Active only on clones that ran
  `setup` (it sets `core.hooksPath=.githooks`).
- A Claude Code `PostToolUse` hook (`.claude/settings.json`) — after Claude edits a
  `*.personal.*` file (it exits instantly for all other edits).

Both are silent no-ops when `.git-personal/` is absent, so clones without the private
repo are unaffected.

## Setup on a fresh machine

```bash
git clone git@github.com:michaelitvin/receipt-processing-israel.git
cd receipt-processing-israel
uv sync
uv run python tools/personal_backup.py setup
```

`setup` clones the private repo into `.git-personal/`, configures it, and restores the
personal files. It refuses to overwrite a local personal file that differs from the
backup unless you pass `--force` (back up the newer machine first instead).

## Day-to-day

- History/diffs: `git personal log`, `git personal diff`, `git personal status`
- Manual backup: `uv run python tools/personal_backup.py backup`
- Troubleshooting: read `.git-personal/backup.log`

## Warning

Never run `git personal clean -x`: `-x` bypasses the overlay's excludes, which makes
the ENTIRE project look like removable untracked files to it. The tooling never calls
`clean`; neither should you via the alias.
```

- [ ] **Step 2: Add the CLAUDE.md section**

Append to `CLAUDE.md` after the "Important Notes" section:

```markdown
## Personal Files Backup

The gitignored `*.personal.*` files are version-tracked in place by a private overlay
repo at `.git-personal/` (see `docs/PERSONAL_BACKUP.md`). Key facts:

- `git personal <cmd>` (alias) drives the overlay: `git personal log/diff/status`.
- Backups run automatically via `.githooks/post-commit` and a Claude Code hook in
  `.claude/settings.json`; both call `uv run python tools/personal_backup.py backup`
  and are silent no-ops if `.git-personal/` is absent.
- Fresh machine: `uv run python tools/personal_backup.py setup` restores the files.
- Never run `git personal clean -x` — it would treat the whole project as removable.
- Never commit personal content to THIS repo; the `*.personal.*` gitignore glob and
  the public/private split exist precisely to prevent that.
```

- [ ] **Step 3: Verify docs mention no personal content**

Run: `grep -iE "michael|litvin|@gmail" docs/PERSONAL_BACKUP.md` — expected: matches only the two GitHub repo URLs (`michaelitvin/...`), which are already public. No other personal facts.

- [ ] **Step 4: Commit**

```bash
git add docs/PERSONAL_BACKUP.md CLAUDE.md
git commit -m "docs: document the personal-files overlay backup"
```

---

### Task 8: Rewrite the private repo; retire the sibling clone

**Files (in `D:\code\receipt-processing-israel-personal` — the sibling clone, its last act):**
- Rename + rewrite: `README.md` → `README.personal.md`
- Delete: `sync-personal.ps1`

**Interfaces:**
- Consumes: nothing from the codebase; operates on the sibling clone.
- Produces: private repo `main` whose every tracked file matches `*.personal.*` (including the renamed `README.personal.md`) — the precondition for Task 9's `setup` (any tracked file that did NOT match `*.personal.*` would be committed into the public repo's history when it materializes, or leak; the whole-`*.personal.*` invariant is what keeps the public repo clean).

- [ ] **Step 1: Rewrite the private repo's README as `README.personal.md`**

`git mv README.md README.personal.md` (Step 2 does the actual move), then replace the full contents of `README.personal.md` with:

```markdown
# receipt-processing-israel — personal files (private)

**Private** version history and backup for the `*.personal.*` files of the public
[`receipt-processing-israel`](https://github.com/michaelitvin/receipt-processing-israel)
repo. Files live at the same relative paths they have in the project.

This repo is consumed as an **overlay**: its git-dir is cloned to `.git-personal/`
inside the public repo's working tree and tracks the personal files in place — no
second folder, no copies. This file is named `README.personal.md` (not `README.md`) so
it is one of the tracked `*.personal.*` files and never collides with the public repo's
own `README.md` in the shared working tree. Every file tracked here matches
`*.personal.*`.

Full mechanism docs live in the public repo: `docs/PERSONAL_BACKUP.md`.

## Recovery on a fresh machine

```bash
git clone git@github.com:michaelitvin/receipt-processing-israel.git
cd receipt-processing-israel
uv sync
uv run python tools/personal_backup.py setup
```

Backups are automatic afterwards (git post-commit hook + Claude Code hook). Manual
operations: `git personal log/diff/status` from the public repo checkout.
```

- [ ] **Step 2: Move the README, delete the sync script, commit, push**

```bash
cd /d/code/receipt-processing-israel-personal
git mv README.md README.personal.md   # then apply the Step 1 rewrite to README.personal.md
git rm sync-personal.ps1
git add README.personal.md
git commit -m "restructure: overlay-repo layout - README.personal.md, drop copy-based sync script"
git push
```

- [ ] **Step 3: Verify the remote tree is overlay-safe**

```bash
git ls-tree -r --name-only origin/main
```
Expected: EVERY path matches `*.personal.*` (e.g. `README.personal.md`,
`AUDIT_KNOWLEDGE.personal.md`, `recurring_vendors.personal.yaml`,
`docs/extraction-prompt/002-ADDITIONAL_INSTRUCTIONS.personal.md`) — no `README.md`, no
`sync-personal.ps1`, nothing else. This gate protects the public worktree during Task 9.

- [ ] **Step 4: Delete the sibling clone**

Confirm `git status` in the sibling clone is clean and the push in Step 2 succeeded, then:

```bash
cd /d/code
rm -rf receipt-processing-israel-personal
```

---

### Task 9: Live migration + end-to-end verification + memory update

**Files:**
- No new code. Runs `setup` on the real project; updates Claude auto-memory `personal-files-backup-repo.md` (outside the repo).

**Interfaces:**
- Consumes: everything (Tasks 1–8 complete, private repo in overlay-safe shape).
- Produces: working live system on this machine.

- [ ] **Step 1: Run setup on the real project**

```bash
cd /d/code/receipt-processing-israel
uv run python tools/personal_backup.py setup
```
Expected: `Overlay ready: 4 personal file(s) tracked.` (the three existing personal files, which already match the backup pushed earlier, plus the newly-renamed `README.personal.md`, which does not yet exist locally and is materialized fresh — no conflict errors).

- [ ] **Step 2: Verify both repos are clean and routed correctly**

```bash
git status --porcelain            # public: empty (modulo untracked local output dirs)
git personal status --porcelain   # overlay: empty
git personal log --oneline        # shows the private repo history
head -3 README.md                 # still the PUBLIC README (unchanged)
head -3 README.personal.md        # the private front-page/notes file, now materialized
```

- [ ] **Step 3: End-to-end backup test with a throwaway personal file**

```bash
echo "e2e overlay test" > zz_e2e_test.personal.md
uv run python tools/personal_backup.py backup --wait
git personal log --oneline -1     # backup: personal files
git ls-remote git@github.com:michaelitvin/receipt-processing-israel-personal.git main
git personal rev-parse main       # must equal the ls-remote hash (push landed)
rm zz_e2e_test.personal.md
uv run python tools/personal_backup.py backup --wait   # deletion committed & pushed
```

- [ ] **Step 4: Verify the live post-commit shim wiring**

```bash
git config core.hooksPath          # .githooks
sh .githooks/post-commit           # runs the real shim directly; expect exit 0, and
tail -3 .git-personal/backup.log   # no new commit (nothing changed)
```

- [ ] **Step 5: Update Claude auto-memory**

Rewrite `C:\Users\micha\.claude\projects\D--code-receipt-processing-israel\memory\personal-files-backup-repo.md` (and its `MEMORY.md` index line) to describe the overlay: `.git-personal/` git-dir over the project tree, `git personal` alias, auto-backup hooks, `setup` for recovery, sibling clone gone, `sync-personal.ps1` gone. Keep the SSH-not-HTTPS note.

- [ ] **Step 6: Final full test run and push**

```bash
uv run pytest tests/
git push          # publish the feature commits (script, hooks, docs, plan)
```
Expected: all tests pass; public push succeeds; `git personal status` still clean.
