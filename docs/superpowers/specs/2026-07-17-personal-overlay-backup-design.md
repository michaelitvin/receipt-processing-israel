# Personal Files Overlay Backup — Design

Date: 2026-07-17
Status: Draft for review

## Problem

`*.personal.*` files (personal audit knowledge, extraction-prompt additions, recurring-vendor
data) are gitignored in this public repo so personal data never reaches public history. That
leaves them with no version tracking and no backup. A first attempt used a sibling clone with a
copy-based sync script; it was rejected: manual pushes, duplicated files in a disjoint folder,
and no discoverability from the public repo.

## Goals

- Personal files are version-tracked and backed up to a private GitHub repo, **in place** — no
  copies, no second folder.
- Backups are **automatic** (git post-commit hook + Claude Code hook), with **async push** so no
  trigger ever waits on the network.
- The mechanism is **discoverable**: documented in both repos, so a fresh clone (human or Claude
  Code) can find and restore the personal files.
- **Zero new leak risk**: no personal content, ever, in the public repo — including in this doc
  and all tooling.
- No PowerShell; portable sh + Python (stdlib only) exclusively.
- No system-wide state: all changes live inside the two repos (tracked files + per-clone
  `.git/config` entries).

## Non-goals

- Encryption of personal data at rest on GitHub (the private repo's access control is the
  boundary).
- Scheduled/daily backup tasks (hooks cover the edit paths in use).
- Multi-user support; this is a single-maintainer setup.

## Architecture: overlay repo (two git-dirs, one working tree)

The project folder remains the only working tree. A second git-dir at `.git-personal/`
(gitignored) tracks the same tree, pushing to the existing private repo
`github.com/michaelitvin/receipt-processing-israel-personal` (repurposed; its file paths already
mirror the project).

Isolation between the two repos:

- **Public repo**: `.gitignore` already ignores `*.personal.*`; add `.git-personal/`. Unchanged
  leak-safety story.
- **Private repo**: `.git-personal/info/exclude` contains `*` (ignore everything), `!*/`
  (descend into directories), `!*.personal.*` (track only personal files). Its index therefore
  holds only personal files; `git status` on either repo is clean and disjoint.
- **Routing**: plain `git` resolves to `.git` (public). The private repo is reached only via the
  per-clone alias `git personal` = `git --git-dir=.git-personal --work-tree=.`.
  `core.worktree=..` is set inside `.git-personal/config` so direct `--git-dir` use also works.
- **README collision**: the private repo tracks a `README.md` (its GitHub front page), but the
  public repo has its own root `README.md`. The private git-dir uses non-cone sparse-checkout
  (`/*`, `!/README.md`) so the private README exists on GitHub but never materializes in the
  working tree. Everything else the private repo tracks MUST match `*.personal.*`.
- **Sharp edge**: `git personal clean -x` would bypass the excludes and treat the entire project
  as removable. Documented as forbidden; the tooling never calls `clean`.

## Components

### `tools/personal_backup.py` (public repo, tracked, no personal content)

Python 3.13 stdlib only, run as `uv run python tools/personal_backup.py <command>`.

- `backup` — no-op (exit 0, silent) if `.git-personal/` is absent. Otherwise: stage all
  (`add -A`, excludes constrain it to personal files), commit if anything is staged
  (message `backup: personal files`), then spawn `git push` as a **detached background
  process** and exit immediately. Push output/outcome appends to `.git-personal/backup.log`;
  failures are silent and retried on the next trigger.
  - `--claude-hook` flag: read the Claude Code PostToolUse JSON from stdin first and exit 0
    immediately unless the edited file path matches `*.personal.*`.
- `setup` — bootstrap a machine: clone the private repo's git-dir into `.git-personal`
  (no worktree materialized during clone), write `info/exclude`, configure sparse-checkout,
  set `core.worktree`, set the public clone's local config (`alias.personal`,
  `core.hooksPath=.githooks`), then `checkout` to restore personal files. Restore only ever
  writes files in the private index (personal files + sparse-excluded README, which is skipped).
  Idempotent: safe to re-run; refuses to overwrite a personal file that differs from HEAD
  without `--force`.

### Hooks (both no-ops without `.git-personal/`)

- **Git post-commit**: tracked sh shim `.githooks/post-commit` that execs
  `uv run python tools/personal_backup.py backup`. Only active on clones that ran `setup`
  (which sets `core.hooksPath`); inert elsewhere. post-commit cannot block or fail the commit,
  and the async push keeps it fast.
- **Claude Code**: `.claude/settings.json` (new tracked file) gains a `PostToolUse` hook with
  matcher `Edit|Write` running `uv run python tools/personal_backup.py backup --claude-hook`.
  Costs one fast Python start per edit; exits instantly for non-personal files. One backup
  commit per Claude edit of a personal file is accepted granularity for a backup repo.

No recursion: commits into `.git-personal` trigger no public-repo hooks, and the private
git-dir has no hooks of its own.

## Documentation plan

- **Public repo** (mechanism only — never personal content, per the standing no-PII rule):
  - `docs/PERSONAL_BACKUP.md`: full mechanism description — overlay mechanics, setup on a fresh
    machine, restore, `git personal` usage, the `clean -x` warning, troubleshooting
    (`backup.log`).
  - `CLAUDE.md`: short section pointing to it, so Claude Code sessions know the overlay exists,
    that `git personal` drives it, and that hooks no-op without it.
- **Private repo**:
  - Rewritten `README.md` (replacing the sibling-clone-era one): what the repo holds, the
    overlay design, recovery walkthrough (`clone public → uv sync → setup`), link back to
    `docs/PERSONAL_BACKUP.md` in the public repo. `sync-personal.ps1` is deleted.

## Migration steps

1. Private repo: commit README rewrite + delete `sync-personal.ps1` (push from the sibling
   clone, its last act).
2. Delete the sibling clone `D:\code\receipt-processing-israel-personal`.
3. Public repo: add script, hooks shim, `.claude/settings.json`, `.gitignore` line, docs,
   CLAUDE.md section.
4. Run `setup` in the project; verify `git status` (public) and `git personal status` are both
   clean.
5. Update Claude Code auto-memory (`personal-files-backup-repo.md`) to describe the overlay
   instead of the sync script.

## Failure modes

| Situation | Behavior |
|---|---|
| No `.git-personal/` (fresh clone, other users) | hooks and `backup` exit 0 silently |
| Offline / push fails | commit persists locally; push retried on next trigger; logged |
| `uv` missing on a setup machine | hook shim fails post-commit (non-blocking); documented |
| Personal file edited outside git/Claude | picked up by the next public commit's hook |
| Accidental `git add` of personal file (public) | still impossible — gitignore unchanged |

## Testing

1. `setup` on the real project: both repos report clean status; alias works.
2. Edit a personal file → public `git commit` → verify auto-commit lands and push arrives on
   GitHub (poll `git personal log origin/main`).
3. Claude edit of a personal file → same verification; Claude edit of a normal file → verify
   no commit created.
4. Simulate fresh machine: clone public repo to a scratch dir, run `setup`, verify all three
   personal files restored byte-identical, and public `git status` stays clean.
5. Clone without running `setup`: verify committing works with no hook side effects.
