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
# The income-tax advance rate comes from config.personal.yaml; --advance-rate overrides it.
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
- Category definitions in `docs/extraction-prompt/001-ICOUNT_CATEGORIES.md`: Full Israeli tax categories with VAT rates and deductibility rules
- JSON schema in `prompts/receipt_extraction_schema.json`: Enforces structured output from OpenAI

### Data Classes (in `shared/openai_client.py`)
- `ProcessedReceipt`: Main data structure containing receipt info, amounts, line items, and classification
- `ReceiptInfo`: Vendor, date, document type, etc.
- `AmountData`: VAT calculations
- `Classification`: Category mapping with confidence

### Configuration
- `.env` file for API keys and processing parameters
- `config.personal.yaml` (untracked; template in `config.example.yaml`): non-secret personal config — `income_tax_advance_rate`, `own_tax_ids`
- `recurring_vendors.personal.yaml` (untracked; template in `recurring_vendors.example.yaml`): vendors expected every period, checked by `audit_batch recurring`
- `config/excel_layout.yaml`: review-workbook cell layout shared by generator and parsers
- Default model: gpt-5-mini (configurable)
- Concurrent request limits and batch sizes

## Israeli Tax Context

The system handles Israeli-specific tax requirements:
- VAT rates: 0%, 18%, 66% (for non-deductible items)
- Document types: Invoice, Receipt, or Invoice+Receipt
- Deductibility rules for home office, vehicles, and mixed expenses
- iCount category mappings with sorting codes

## Important Notes

- Windows console uses cp1252 encoding — use `PYTHONIOENCODING=utf-8` when printing Hebrew/Unicode from Python CLI
- iCount exports: date columns are located by header text (תאריך / תאריך ערך); the income date is a DD/MM/YYYY string, the expenses date a datetime
- iCount expenses already include pre-calculated deductibility in "מע"מ מוכר" column — use those values directly
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
repo at `.git-personal/` (see `docs/PERSONAL_BACKUP.md`). Key facts:

- `git personal <cmd>` (alias) drives the overlay: `git personal log/diff/status`.
- Backups run automatically via `.githooks/post-commit` (runs `tools/personal_backup.py
  backup` with the venv python) and a Claude Code hook in `.claude/settings.json`
  (runs `uv run python tools/personal_backup.py backup --claude-hook`); both are
  silent no-ops if `.git-personal/` is absent.
- Fresh machine: `uv run python tools/personal_backup.py setup` restores the files.
- Never run `git personal clean -x` — it would treat the whole project as removable.
- Never commit personal content to THIS repo; the `*.personal.*` gitignore glob and
  the public/private split exist precisely to prevent that.