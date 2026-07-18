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
