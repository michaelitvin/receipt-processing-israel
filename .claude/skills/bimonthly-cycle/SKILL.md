---
name: bimonthly-cycle
description: Run the bi-monthly Israeli receipt cycle - extract receipts with OpenAI, audit the extraction batch against source documents, hand off for human review, consolidate to iCount format, and reflect. Use when the user wants to run the bi-monthly cycle, extract a folder of receipts, audit an extraction batch xlsx, or consolidate reviewed batches.
---

# Bi-Monthly Receipt Cycle

Five phases. Announce the current phase. Never skip the audit.
All commands run from the repo root with `PYTHONIOENCODING=utf-8`.

**Corrections log:** the moment the user corrects ANYTHING (a value, a category,
a process step), append one line to `corrections.md` in the session scratchpad:
`- [phase] what the user corrected, and what we had wrong`. Phase 5 consumes it.

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
   recurring receipt (NetCom, AcmeMobile, water, electricity, ...) was never
   collected; tell the user to locate it before consolidating.
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

1. `uv run python receipt_consolidator.py BATCH [BATCH2 ...]`
2. Verify the output XLS: row count equals audited sheets minus non-expense
   ones the user removed; receipts with non-deductible line items import the
   deductible portion only (the consolidator logs these); receipt files copied
   with standardized names.
3. Remind the user: non-expense sheets (red tab) must be deleted from the xlsx
   BEFORE consolidation, or removed from the iCount file after - confirm which
   happened.

## Phase 5 - Reflect

Walk `corrections.md` from the scratchpad plus anything you remember and
propose routed updates, each requiring explicit user approval:

- Personal facts (billing entities, vendor quirks, known anomalies) →
  `AUDIT_KNOWLEDGE.personal.md`
- General process lessons → this SKILL.md (edit it)
- Extraction steering (categories, deductibility, vendor rules) →
  `docs/extraction-prompt/002-ADDITIONAL_INSTRUCTIONS.personal.md`

Nothing is written without approval. Do not auto-commit SKILL.md edits -
show the diff and let the user decide.

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
