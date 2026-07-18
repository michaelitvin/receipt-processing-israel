# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a two-stage receipt processing system for Israeli tax reporting that uses OpenAI's API to extract structured data from receipts and prepare it for import into iCount accounting software.

**Stage 1**: `receipt_extractor.py` - Extracts data from receipt images/PDFs using OpenAI's Responses API, generates Excel files with embedded images for review
**Stage 2**: `receipt_consolidator.py` - Processes reviewed Excel files and consolidates them into iCount-ready XLS format (true Excel 97-2003) with organized receipt files
**VAT Report**: `vat_report.py` - Generates bi-monthly VAT report from iCount income/expenses exports with VAT calculation, split by reporting period

**Audit tooling**: `tools/audit_batch.py` - manifest/check/agent-prompts/apply-fixes/verify/recurring subcommands over extraction batch xlsx files; structural checks shared with the extractor via `shared/receipt_checks.py`. The `bimonthly-cycle` project skill orchestrates the full cycle. `AUDIT_KNOWLEDGE.personal.md` (untracked) holds personal audit context.

## Development Commands

### Environment Setup
```bash
# Install dependencies (automatically creates/manages virtual environment)
uv sync

# Configure API key
cp .env.example .env
# Edit .env and add OPENAI_API_KEY
```

### Running the System

**Stage 1 - Extract receipts:**
```bash
uv run python receipt_extractor.py /path/to/receipts/folder

# With options (--period flags receipts outside the reporting period):
uv run python receipt_extractor.py /path/to/receipts --period 2026-05 --concurrent 3 --receipts-per-file 5 --output ./output --model gpt-5-mini
```

**Stage 2 - Consolidate to iCount format:**
```bash
uv run python receipt_consolidator.py path/to/excel1.xlsx path/to/excel2.xlsx

# With custom output (--receipts-source-dir adds a fallback folder for locating originals):
uv run python receipt_consolidator.py *.xlsx --output ./consolidated --receipts-source-dir /path/to/originals
```

**VAT Report - Generate bi-monthly report:**
```bash
uv run python vat_report.py --income path/to/income.xlsx --expenses path/to/expenses.xlsx --output ./output
# Both --income and --expenses are optional (but not both).
# The income-tax advance rate comes from CONFIG.personal.yaml; --advance-rate overrides it.
```

### Dependencies
- Python 3.13+
- UV package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `pip install uv`)
- Poppler (for PDF processing): `brew install poppler` (macOS) or `sudo apt-get install poppler-utils` (Linux)
- OpenAI API key from https://platform.openai.com/

## Architecture & Key Components

### Core Processing Pipeline
1. **Image/PDF Processing** (`shared/image_handler.py`): Converts receipts to base64 for API submission
2. **OpenAI Integration** (`shared/openai_client.py`): Uses Responses API with structured JSON output, includes Jinja2 template rendering
3. **Excel Generation** (`shared/excel_generator.py`): Creates Excel files with embedded images, data validation, and conditional formatting
4. **Receipt File Organization** (in `receipt_consolidator.py`): Copies receipt files from original locations with standardized naming: `YYYYMMDD_<receipt_id>__<vendor_name>.{extension}`
5. **Logging** (`shared/logger.py`): Comprehensive YAML logging with full prompts and API metadata

### Prompt System
- Templates in `prompts/`: Uses Jinja2 for maintainable prompts
- Category definitions in `docs/extraction-prompt/001-icount-categories.md`: Full Israeli tax categories with VAT rates and deductibility rules
- JSON schema in `prompts/receipt_extraction_schema.json`: Enforces structured output from OpenAI

### Data Classes (in `shared/openai_client.py`)
- `ProcessedReceipt`: Main data structure containing receipt info, amounts, line items, and classification
- `ReceiptInfo`: Vendor, date, document type, etc.
- `AmountData`: VAT calculations
- `Classification`: Category mapping with confidence

### Configuration
- `.env` file for API keys and processing parameters
- `CONFIG.personal.yaml` (untracked; template in `CONFIG.example.yaml`): non-secret personal config — `income_tax_advance_rate`, `own_tax_ids`
- `RECURRING_VENDORS.personal.yaml` (untracked; template in `RECURRING_VENDORS.example.yaml`): vendors expected every period, checked by `audit_batch recurring`
- `config/excel_layout.yaml`: review-workbook cell layout shared by generator and parsers
- Default model: gpt-5-mini (configurable)
- Concurrent request limits and batch sizes

## Israeli Tax Context

The system handles Israeli-specific tax requirements:
- VAT rates: 0%, 18%, 66% (for non-deductible items)
- Document types: Invoice, Receipt, or Invoice+Receipt
- Deductibility rules for home office, vehicles, and mixed expenses
- iCount category mappings with sorting codes

## Filename Casing

- **Dotted-infix family** (`*.example.*`, `*.personal.*`): SCREAMING_SNAKE body,
  lowercase infix — `CONFIG.personal.yaml`, `RECURRING_VENDORS.example.yaml`,
  `AUDIT_KNOWLEDGE.personal.md`, `002-ADDITIONAL_INSTRUCTIONS.personal.md`.
  The shouting body exists so the lowercase `.personal.` stands out: these are
  the files that must never reach the public repo. This rule **overrides** the
  directory conventions below — a `.personal.` file under `docs/` still shouts.
- `snake_case` for Python modules and other hand-authored YAML/JSON/J2 config
- `kebab-case` for everything under `docs/` — files and directories alike
- Root markdown and `LICENSE` keep their conventional SCREAMING names
  (`README.md`, `CLAUDE.md`); `SKILL.md` and `.githooks/post-commit` are
  mandated by Claude Code and git. `.env.example` is left alone: it has no
  trailing extension segment, so it isn't part of the dotted-infix family.
- Generated artifacts under `llm_logs/` and `receipts_*/` embed the source
  receipt filename verbatim — deliberately not normalized, since that string is
  what traces a log back to its receipt

Two naming patterns are load-bearing, not cosmetic:

- The `NNN-` prefix in `docs/extraction-prompt/` sets prompt load order
  (`shared/openai_client.py` sorts the glob)
- **The `.personal.` infix must stay lowercase.** It is matched by the
  `.gitignore` glob and by `PERSONAL_GLOB` in `tools/personal_backup.py`. The
  body's casing is free (both patterns are `*` + `.personal.` + `*`), but
  uppercasing the infix itself would stop `.gitignore` matching on a
  case-sensitive clone and make the file committable to the public repo — a
  leak, not just a broken link.

Renaming these on Windows: `core.ignorecase=true` on both `.git` and
`.git-personal`, so a case-only rename needs a two-step `git mv` in the public
repo, and an explicit `git personal rm --cached` + `git personal add -f` in the
overlay — a plain `add -u` will silently keep the old name.

## Important Notes

- Windows console uses cp1252 encoding — use `PYTHONIOENCODING=utf-8` when printing Hebrew/Unicode from Python CLI
- iCount exports: date columns are located by header text (תאריך / תאריך ערך); the income date is a DD/MM/YYYY string, the expenses date a datetime
- iCount expenses already include pre-calculated deductibility in "מע"מ מוכר" column — use those values directly
- iCount converts foreign-currency invoices to ILS on import using its own rate; the consolidator leaves the שער (rate) column blank for iCount to fill. So a foreign invoice's ILS amount in the export/VAT report differs from its nominal foreign amount — expected, not an extraction error
- Tests: `uv run pytest tests/` (covers receipt_checks, audit_batch, extractor warnings, consolidator parsing, personal_config, personal_backup, and the image_handler raster-PDF gate; no coverage for the live OpenAI API paths)
- Raster-only PDFs (no text layer + embedded bitmap, e.g. Weezmo receipts) are detected by `ImageHandler.extraction_bitmap`, which sends the crisp embedded bitmap (via poppler's `pdfimages`) to both the API and the Excel review image instead of the raw PDF; normal text-layer PDFs keep the raw-PDF path. Needs `pdftotext`/`pdfimages` on PATH.
- No linting/formatting tools configured
- Logs are stored in `llm_logs/` directories with YAML format
- Failed receipts automatically generate empty Excel batches for manual entry
- All processing is asynchronous with configurable concurrency
- Consolidation generates true XLS format (Excel 97-2003) using xlwt library for iCount compatibility
- Receipt files are automatically organized with standardized naming in the consolidation output

## Personal Files Backup

The gitignored `*.personal.*` files are version-tracked in place by a private overlay
repo at `.git-personal/` (see `docs/personal-backup.md`). Key facts:

- `git personal <cmd>` (alias) drives the overlay: `git personal log/diff/status`.
- Backups run automatically via `.githooks/post-commit` (runs `tools/personal_backup.py
  backup` with the venv python) and a Claude Code hook in `.claude/settings.json`
  (runs `uv run python tools/personal_backup.py backup --claude-hook`); both are
  silent no-ops if `.git-personal/` is absent.
- Fresh machine: `uv run python tools/personal_backup.py setup` restores the files.
- Never run `git personal clean -x` — it would treat the whole project as removable.
- Never commit personal content to THIS repo; the `*.personal.*` gitignore glob and
  the public/private split exist precisely to prevent that.