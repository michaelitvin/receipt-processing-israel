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
