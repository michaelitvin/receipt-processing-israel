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
        # The overlay shares the public repo's worktree, whose own .gitignore ignores
        # *.personal.* — and a worktree .gitignore OUTRANKS our info/exclude negation in
        # git's ignore precedence, so a plain `add -A` silently skips BRAND-NEW personal
        # files. Stage tracked personal edits/deletions with -u, then force-add any new
        # personal files (enumerated + bounded by the glob so we never sweep in .venv etc.).
        overlay_git(root, "add", "-u")
        new_personal = [
            p for p in overlay_git(
                root, "ls-files", "-o", "-i", "--exclude-standard", "-z",
                "--", ":(glob)**/*.personal.*",
            ).stdout.split("\0")
            if p
        ]
        if new_personal:
            overlay_git(root, "add", "-f", "--", *new_personal)
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
    except Exception as exc:  # a backup must NEVER break a commit or a Claude session
        _log(log_path, f"ERROR: {exc!r}")
        print(f"personal_backup: error logged to {log_path}", file=sys.stderr)
    return 0


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
        if args.claude_hook:
            return cmd_claude_hook(root, wait=args.wait)
        return cmd_backup(root, wait=args.wait)
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 1
    return cmd_setup(root, remote=args.remote, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
