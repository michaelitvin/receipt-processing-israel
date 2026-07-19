---
name: bimonthly-cycle
description: Run the bi-monthly Israeli receipt cycle - extract receipts with OpenAI, audit the extraction batch against source documents, hand off for human review, consolidate to iCount format, and reflect. Use when the user wants to run the bi-monthly cycle, extract a folder of receipts, audit an extraction batch xlsx, or consolidate reviewed batches.
---

# Bi-Monthly Receipt Cycle

Eight phases. Announce the current phase. Never skip the audit.
All commands run from the repo root with `PYTHONIOENCODING=utf-8`.

**Corrections log:** the moment the user corrects ANYTHING (a value, a category,
a process step), append one line to `corrections.md` in the session scratchpad:
`- [phase] what the user corrected, and what we had wrong`. Phase 8 consumes it.

**Personal context:** read `AUDIT_KNOWLEDGE.personal.md` (repo root, untracked)
before Phase 2. It lists known-OK anomalies and business context. If it is
missing, ask the user whether to recreate it.

## Phase 1 - Extract

1. Ask for the raw-docs folder and the reporting period (YYYY-MM) if not given.
2. `uv run python receipt_extractor.py "<folder>" --period <YYYY-MM>`
3. Confirm from the summary that every file processed; extraction failures get
   an empty batch for manual entry - list them for the user.
4. Red-tabbed sheets were flagged by generation-time sanity checks; they get
   priority attention in Phase 2.

## Phase 2 - Audit

The audit exists because it is INDEPENDENT of extraction. Never weaken that:

- **HARD RULE: never hand-type extracted values into agent prompts.** Use
  `agent-prompts` output verbatim. (Hand-typed values once fabricated three
  false bugs that survived until a backup comparison exposed them.)
- **HARD RULE: back up before editing, `verify` after.** `apply-fixes` does
  both; do not edit the workbook with ad-hoc scripts.
- Agents transcribe the image FIRST, then compare - the prompts enforce this;
  do not reorder.

Steps (`BATCH` = the batch xlsx path):

1. `uv run python tools/audit_batch.py check BATCH --period <YYYY-MM>` and
   `... manifest BATCH` - note structural issues.
   Then `uv run python tools/audit_batch.py recurring BATCH [BATCH2 ...]` (pass
   ALL of the period's batches at once) - a non-empty `missing` list means a
   recurring receipt (mobile carrier, internet, water, electricity, ...) was never
   collected; tell the user to locate it before consolidating. If a vendor is
   wrongly reported missing (its name/id changed) or a genuinely new recurring
   vendor appears, propose an edit to `RECURRING_VENDORS.personal.yaml` per the
   caution in Phase 8 - do not edit it silently.
2. `uv run python tools/audit_batch.py agent-prompts BATCH --scratch <session scratchpad>`
   then dispatch each prompt to a general-purpose subagent (parallel, background).
3. Reconcile agent transcriptions against the manifest. Consult
   `AUDIT_KNOWLEDGE.personal.md` - known anomalies are not findings.
   `UNVERIFIABLE` verdicts: read the image yourself; re-dispatch a dead agent's
   chunk once before reading directly.
4. Write `fixes.json` (schema in `tools/audit_batch.py` docstring/tests):
   corrected values with Hebrew audit notes, `non_expense: true` for documents
   that are not expenses (certificates, confirmations - do not delete sheets).
5. `uv run python tools/audit_batch.py apply-fixes BATCH fixes.json --backup-dir <session scratchpad>`
   - exit 3 means the file is open in Excel: ask the user to close it, retry once.
6. `uv run python tools/audit_batch.py verify BATCH` must exit 0.
7. Report to the user: values fixed (with evidence), items needing their
   judgment (billing entity, deductibility), suspected non-expense documents.

## Phase 3 - Human review

Hand off: the user reviews the audited xlsx in Excel (red tabs first, then
audit notes in the הערות column). When they say they are done:
`uv run python tools/audit_batch.py check BATCH --period <YYYY-MM>` again -
their edits are unvalidated input. Surface any new issues before Phase 4.
Re-run `... recurring BATCH [BATCH2 ...]` too - confirm the `missing` list is
empty (or the user has accounted for each) before consolidating.

## Phase 4 - Consolidate

1. `uv run python receipt_consolidator.py BATCH [BATCH2 ...] --receipts-source-dir <raw-docs folder>`
   (`--receipts-source-dir` so the receipt files copy for Phase 5's image upload;
   without it the run copies 0 files and exits nonzero.)
2. Verify the output XLS: row count equals audited sheets minus non-expense
   ones the user removed; receipts with non-deductible line items import the
   deductible portion only (the consolidator logs these); receipt files copied
   with standardized names.
3. Remind the user: non-expense sheets (red tab) must be deleted from the xlsx
   BEFORE consolidation, or removed from the iCount file after - confirm which
   happened.

## Phase 5 - Import to iCount

Manual steps in the iCount web UI - hand off to the user and give them the exact
consolidation output paths (the `icount_import_*.xls` and its sibling `receipts/`
folder).

1. **Import the expenses.** מערכת → יבוא ויצוא → הוצאות, upload the
   `icount_import_*.xls` from the consolidation output.
2. **Attach a receipt image to each expense.** Open the הוצאות screen and filter
   a date range covering the period. To show only rows still missing a document,
   use a browser bookmark: `javascript:$('table tr:has(.fa-file)').hide();`. Open
   each remaining expense in its own tab, drag the matching file from the
   consolidation `receipts/` folder onto the upload field, verify the values, and
   click שמור שינויים.
   - Deductible-portion receipts intentionally record a smaller amount than their
     image shows (e.g. a half-deductible bill recorded 40.00, image totals 80.00). That mismatch
     is expected - do not "correct" the amount to match the image.

## Phase 6 - VAT report

1. Export from iCount (user, web UI):
   - Expenses: הוצאות screen → filter the period's two months → export-to-Excel → download.
   - Income: מסמכים → filter the period's two months → Excel → download.
   The user feeds both files to the session.
2. `uv run python vat_report.py -i <income.xlsx> -e <expenses.xlsx> -o ./output`
   (either file may be omitted, but not both).
   - The income-tax advance rate is read from `income_tax_advance_rate` in
     `CONFIG.personal.yaml` automatically; pass `--advance-rate` only to override it.
   - It auto-detects the reporting period(s) and splits the report per period.
     Confirm the printed "Found N reporting period(s)" matches the period you
     expect before trusting the output.

## Phase 7 - File & hand off

1. Optional: send the report to the user's CPA for verification. Every user does
   this their own way - offer, do not prescribe.
2. File with the Tax Authority (user action; the report carries the numbers for
   both). Print both lines with this period's figures filled in from the Phase 6
   report, and with the filing identity for each portal taken from the "Filing
   identities" section of `AUDIT_KNOWLEDGE.personal.md` - the two reports are not
   necessarily filed under the same person's ID, and logging into the wrong one
   wastes a trip:

   - מע"מ - https://secapp.taxes.gov.il/EmHanDoch - <payment due | refund claim>
     of ₪<vat amount> - filed under <identity>
   - מקדמות - https://secapp.taxes.gov.il/Gmmikdama - advance of ₪<advance> on
     turnover ₪<turnover> - filed under <identity>

   If the personal file has no "Filing identities" section, ask the user who each
   report is filed under and offer to record it there (Phase 8 rules apply).

## Phase 8 - Reflect

Walk `corrections.md` from the scratchpad plus anything you remember and
propose routed updates, each requiring explicit user approval:

- Personal facts (billing entities, vendor quirks, known anomalies) →
  `AUDIT_KNOWLEDGE.personal.md`
- General process lessons → this SKILL.md (edit it)
- Extraction steering (categories, deductibility, vendor rules) →
  `docs/extraction-prompt/002-ADDITIONAL_INSTRUCTIONS.personal.md`
- Recurring-vendor set (a new vendor that now bills every period, or a changed
  name/id that caused a false "missing") → `RECURRING_VENDORS.personal.yaml`
  (prefer `ids` for Israeli vendors, `keywords` for foreign ones)

Nothing is written without approval. Do not auto-commit SKILL.md edits -
show the diff and let the user decide. EXTRA CAUTION for the `*.personal.*`
files (AUDIT_KNOWLEDGE, 002-ADDITIONAL_INSTRUCTIONS, RECURRING_VENDORS): they
hold the user's curated personal context - show the exact change and get
explicit approval before writing, and never delete existing entries without
the user confirming. (A bad edit IS recoverable - they are version-tracked by
the `.git-personal` overlay, `git personal log/diff`; see docs/personal-backup.md.)

## Known traps

- openpyxl ignores `img.width`/`img.height` on loaded workbooks - image sizing
  goes through `anchor.ext` (audit_batch handles this; never bypass it).
- `PermissionError` on save = file open in Excel.
- Windows console is cp1252 - Hebrew output needs `PYTHONIOENCODING=utf-8`.
- Weezmo/EZcount/invoice4u are receipt-delivery platforms, never the vendor.
- A receipt's printed total may differ from net+VAT by one agora - that is
  receipt rounding, not an extraction error (tolerance 0.02).
- Dumping sheet rows with a hardcoded row cap once hid real line items and
  produced false "missing item" findings - always read line items to the first
  empty row (parse_batch does this correctly).
