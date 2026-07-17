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
