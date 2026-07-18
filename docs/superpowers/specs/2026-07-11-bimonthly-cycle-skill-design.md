# Bi-Monthly Cycle Skill — Design

**Date:** 2026-07-11
**Status:** Approved design, pending implementation plan

## Purpose

A project skill that runs the full bi-monthly receipt cycle — extract, audit,
human review, consolidate, reflect — encoding the audit process developed in
the 2026-07-11 session so each cycle catches extraction errors (OCR misreads,
failed extractions, non-expense documents) before they reach iCount, and so
lessons from each cycle accumulate instead of being relearned.

## Artifacts

| Path | Tracked | Role |
|---|---|---|
| `.claude/skills/bimonthly-cycle/SKILL.md` | yes | The process: phases, checklists, hard rules, agent prompt policy |
| `tools/audit_batch.py` | yes | Deterministic audit tooling (CLI, JSON in/out) |
| `shared/receipt_checks.py` | yes | Structural checks shared by extractor and audit tool |
| `AUDIT_KNOWLEDGE.personal.md` | no (gitignored via `*.personal.md`) | Personal audit context for Claude |
| `docs/extraction-prompt/002-ADDITIONAL_INSTRUCTIONS.personal.md` | no (existing) | Extraction steering for the OpenAI model (role unchanged) |

## Cycle phases (SKILL.md)

1. **Extract.** Ask for the raw-docs folder and reporting period. Run
   `receipt_extractor.py <folder> --period YYYY-MM`. Confirm every file
   processed; extraction-time sanity flags (red tabs) noted for the audit.
2. **Audit.** See "Audit phase" below.
3. **Human review.** Hand off to the user to review the audited xlsx in Excel
   (flagged sheets first). On resume, re-run structural checks against the
   edited file — user edits are unvalidated input.
4. **Consolidate.** Run `receipt_consolidator.py`. Verify the iCount XLS:
   row count matches remaining sheets, deductible-only amounts applied where
   line items carry non-deductible flags, receipt files copied and renamed.
5. **Reflect.** Process the session corrections log (see "Reflection") into
   proposed updates. Each update requires user approval.

## Structural checks (`shared/receipt_checks.py`)

Pure functions over plain receipt dicts, returning warning strings. Checks:
missing receipt number / date / vendor id, zero total, net+VAT ≠ total,
implausible VAT rate, date outside the bi-monthly period, duplicate receipt
numbers within the batch, duplicate vendor+total+date signatures, currency
outside {ILS, USD, EUR}, line-items sum ≠ header total.

Two call sites, one implementation:
- `receipt_extractor.py` at generation time, on in-memory results
  (`_add_review_warnings` delegates here; red tab + note rendering stays in
  `excel_generator.py`).
- `tools/audit_batch.py check`, on receipts parsed back from the xlsx — runs
  at audit start and again after human review, because audit fixes and user
  edits land between extraction and consolidation.

Extraction-time checks are self-consistency only. Anything requiring the
source document stays in the audit: independence is the point — a checker
built into the writer shares the writer's blind spots.

## Audit phase

### `tools/audit_batch.py` subcommands

- `manifest <batch.xlsx>` — dump per-sheet JSON: header fields, line items,
  hyperlink targets, image path, source PDF path.
- `check <batch.xlsx> [--period YYYY-MM]` — run shared structural checks,
  print issue list as JSON.
- `agent-prompts <batch.xlsx> [--chunk 6]` — emit ready-to-dispatch visual
  verification prompts, one per chunk of receipts, with extracted values
  injected programmatically from the manifest. Prompt template lives in the
  tool. Prompts instruct agents to read the image FIRST and report printed
  values before comparing (anti-anchoring), and include the pdf2image recipe
  for rendering later PDF pages when page 1 lacks the amounts.
- `apply-fixes <batch.xlsx> <fixes.json>` — back up to the scratchpad, then
  apply entries `{sheet, field|row, value, note}`: values to column B, orange
  bold audit note to column D, `non_expense: true` entries get a red tab.
  Image resizing (if ever needed) via anchor `ext` in EMU — never
  `img.width`/`img.height`, which openpyxl ignores on loaded workbooks.
  A `PermissionError` on save means the file is open in Excel: report that
  to the user, do not retry blindly.
- `verify <batch.xlsx>` — reload and assert integrity: one image per sheet,
  data validations present, hyperlinks intact, `CategoryList` named range,
  header arithmetic.

### Flow

1. `manifest` + `check`.
2. Dispatch parallel verification agents (general-purpose, ~6 receipts each,
   full coverage every cycle) using `agent-prompts` output verbatim.
   **Hard rule: never hand-type extracted values into agent prompts** — in the
   2026-07-11 session, hand-typed values fabricated three false "bugs".
3. Reconcile agent-reported printed values against the manifest. Consult
   `AUDIT_KNOWLEDGE.personal.md` before flagging — known anomalies (e.g.
   Google Cloud USD reverse-charge) are not findings. `UNVERIFIABLE`
   verdicts escalate to Claude reading the image directly.
4. Write `fixes.json`, run `apply-fixes`, then `verify`.
5. Report: values fixed (with evidence), items needing user judgment
   (billing-entity questions, deductibility), suspected non-expense documents.

## Personal knowledge file

`AUDIT_KNOWLEDGE.personal.md`, freeform markdown, three conventional headings:

- **Business context** — e.g. invoices billed to a second household entity belong
  to this business.
- **Vendor notes** — e.g. a foreign vendor: USD + 0% reverse-charge VAT is normal;
  a combined bill: only <the-deductible-line> deductible (extraction already knows
  this via 002; the audit checks it was honored).
- **Known non-expenses** — e.g. Section 46 certificates.

Seeded at implementation time from the 2026-07-11 session findings.
Consumer is Claude during the audit; it is never injected into the OpenAI
extraction prompt (that is 002's job).

## Reflection

- **Capture live:** whenever the user corrects anything mid-cycle (a value,
  a category, a process step), append one line to a corrections log in the
  session scratchpad immediately.
- **Reflect at end (phase 5):** walk the log and propose routed updates:
  - personal facts → `AUDIT_KNOWLEDGE.personal.md`
  - general process lessons → `SKILL.md` edit
  - extraction steering (categories, deductibility, vendor rules) →
    `002-ADDITIONAL_INSTRUCTIONS.personal.md`
- Every update is shown to the user for approval before writing. Nothing is
  auto-committed.

## Error handling

- Extraction failures: already produce failed-receipt batches; the audit
  report lists them for manual entry.
- Locked xlsx (open in Excel): detected on save; ask the user to close, retry
  once on confirmation.
- Missing images dir / Poppler: fail with a clear message naming the fix.
- Agent returns nothing or dies: re-dispatch that chunk once; then escalate
  to direct reading.

## Validation (implementation gate)

Run the finished tool against a preserved copy of the original (pre-fix)
`extraction_20260711_175223` batch, whose ground truth is known. It must:
- flag the Weezmo receipt (zero total, missing date/number),
- flag the missing AcmeMobile vendor id,
- flag the out-of-period Section 46 certificate,
- generate agent prompts whose values match the xlsx byte-for-byte,
- apply a fixes.json reproducing the manual session fixes, and pass `verify`.

## Out of scope

- No schema change for a "not an expense" document class (declined).
- No tiered/sampled audit — full visual coverage every cycle.
- vat_report.py is untouched; running it is not part of the skill.
