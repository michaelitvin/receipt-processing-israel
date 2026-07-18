# Receipt Processing System for Israeli Tax Reporting

An intelligent two-stage receipt processing system using OpenAI's API for accurate data extraction and Excel-based review workflow.

## 🎯 System Overview

The system provides a streamlined approach to processing receipts for Israeli tax compliance:

1. **Stage 1: Data Extraction** (`receipt_extractor.py`)
   - Extracts structured data from receipt images and PDFs
   - Uses OpenAI's Responses API with custom Jinja2 templates
   - Parallel processing with configurable concurrency
   - Generates XLSX files with embedded images for review
   - Creates failed receipt batches for manual entry

2. **Stage 2: Consolidation** (`receipt_consolidator.py`)
   - Processes reviewed XLSX files from Stage 1
   - Consolidates data into iCount-ready XLS format (Excel 97-2003) for direct import
   - Copies and organizes receipt files with standardized naming
   - Maintains data integrity and validation

3. **VAT Report** (`vat_report.py`)
   - Generates the bi-monthly VAT report from iCount income/expenses exports
   - Splits by reporting period, computes VAT due and income-tax advances

Between the stages, `tools/audit_batch.py` provides deterministic audit tooling
over the extraction batches (structural checks, visual-verification prompts,
fix application, recurring-vendor completeness); the `bimonthly-cycle` Claude
Code skill (`.claude/skills/`) orchestrates the full cycle.

## ⚡ Key Features

- **Direct PDF Support**: Processes PDFs natively via OpenAI Responses API; raster-only PDFs (e.g. Weezmo thermal receipts) are detected and their embedded bitmap is sent instead for a crisper read
- **Template-Based Prompts**: Uses Jinja2 templates with full Israeli tax category context
- **Comprehensive YAML Logging**: Detailed logs with timing, metadata, and full prompts
- **Excel Review Workflow**: Visual review with embedded images and validation formulas
- **Failed Receipt Handling**: Automatic empty batch files for processing failures
- **Receipt File Organization**: Automatic copying and renaming of receipt files with standardized naming
- **Israeli Tax Compliance**: Built-in VAT rules, deductibility, and category mappings

## 📊 Excel Worksheet Layout

Each extracted receipt creates an XLSX worksheet with this layout (Stage 1 extraction):

```
    A              B              C           D           |  H     I     J     K
 1  Field Name     Value          Validation  Notes       |
 2  Receipt #      12345                                  |  ┌─────────────────┐
 3  Vendor         Test Company                           |  │                 │
 4  Vendor ID      123456789                              |  │                 │
 5  Date           2024-01-15                             |  │                 │
 6  Doc Type       Invoice                                |  │   Receipt Image │
 7  Currency       ILS                                    |  │                 │
 8  Total ex VAT   100.00         85.47                   |  │   (Embedded)    │
 9  VAT            17.00          17.00                   |  │                 │
10  Total inc VAT  117.00         102.47                  |  │                 │
11  Category       Software                               |  │                 │
12  Reasoning      Software subscription for...           |  │                 │
13  Source Link    [link]                                 |  │                 │
...                                                       |  │                 │
18                                                        |  │                 │
19                                                        |  │                 │
20  Description    Total ex VAT   VAT     VAT %           |  └─────────────────┘
21  Office 365     85.47          14.53   18.0%         ☑️
22  Setup Fee      14.53          2.47    18.0%         ☐
23
```

**Key Layout Features:**
- **Columns A-D**: Header information (field names, values, verification, notes)
- **Columns H-K**: Receipt image (merged cells H2:K25)
- **Row 20**: Line item headers
- **Row 21+**: Individual line items with deductible checkboxes

**Hebrew Field Names** (actual Excel uses these):
- Field Name = שם שדה
- Receipt # = מספר קבלה
- Vendor = ספק
- Vendor ID = תז/חפ הספק
- Total ex VAT = סה"כ ללא מע"מ
- Category = קטגוריה
- Description = תיאור

**Output Formats:**
- **Stage 1 (Extraction)**: XLSX format with embedded images for review
- **Stage 2 (Consolidation)**: XLS format optimized for iCount import

## 🚀 Quick Start

### Prerequisites

1. **Python 3.13+** installed
2. **UV package manager**: Install with `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `pip install uv`)
3. **OpenAI API Key** from [OpenAI Platform](https://platform.openai.com/)
4. **Poppler** for PDF support:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install poppler-utils

   # macOS
   brew install poppler

   ```

   **Windows (Option 1: Miniconda - recommended)**
   1. Download and install Miniconda from https://docs.conda.io/en/latest/miniconda.html
   2. Open a new terminal and run:
      ```bash
      conda install -c conda-forge poppler
      ```

   **Windows (Option 2: Prebuilt binaries)**
   1. Download the latest zip from https://github.com/oschwartz10612/poppler-windows/releases
   2. Extract to `C:\poppler` (or any folder)
   3. Add to PATH (run in PowerShell as Administrator):
      ```powershell
      [Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\poppler\Library\bin", "Machine")
      ```
   4. Restart your terminal and verify with `pdfinfo -v`

### Installation

1. Clone the repository
2. Install dependencies (UV automatically manages the virtual environment):
   ```bash
   uv sync
   ```

3. Copy `.env.example` to `.env` and configure your API key:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your OpenAI API key:
   ```
   OPENAI_API_KEY=your-openai-api-key-here
   ```
   (`MODEL`, `MAX_CONCURRENT_REQUESTS`, and `RECEIPTS_PER_FILE` are optional —
   see `.env.example`.)

### Usage

#### Stage 1: Extract Receipt Data

```bash
# Basic usage
uv run python receipt_extractor.py /path/to/receipts/folder

# With custom concurrency and batch size
uv run python receipt_extractor.py /path/to/receipts/folder --concurrent 3 --receipts-per-file 5

# With custom output directory
uv run python receipt_extractor.py /path/to/receipts/folder --output ./my_extractions

# Flag receipts dated outside the reporting period (YYYY-MM)
uv run python receipt_extractor.py /path/to/receipts/folder --period 2026-05
```

This creates:
- Excel files with receipt data and embedded images
- Failed receipt batches for manual entry
- Comprehensive YAML logs of all API interactions
- Processing summary with statistics

#### Stage 2: Consolidate to iCount Format

```bash
# Process reviewed Excel files
uv run python receipt_consolidator.py path/to/receipts_batch_001.xlsx path/to/receipts_batch_002.xlsx

# With custom output directory
uv run python receipt_consolidator.py *.xlsx --output ./consolidated_output
```

This generates:
- iCount-ready XLS import file (true Excel 97-2003 format)
- Organized receipt files folder with standardized naming
  - Format: `YYYYMMDD_<receipt_id>__<vendor_name>.{extension}`
  - Files copied from original locations preserving quality
- Consolidation summary with statistics
- YAML processing logs

#### VAT Report

```bash
uv run python vat_report.py --income path/to/income.xlsx --expenses path/to/expenses.xlsx --output ./output
# Either --income or --expenses may be omitted (but not both).
# --advance-rate overrides the income_tax_advance_rate from CONFIG.personal.yaml.
```

## 📋 Technical Details

### OpenAI Integration
- Uses **Responses API** with gpt-5-mini model
- Direct PDF and image processing support
- Structured JSON output with strict schema validation
- Full Israeli tax category context in prompts

### Template System
- **Jinja2 templates** for maintainable prompts
- Full `icount-categories.md` content included in prompts
- VAT rates, deductibility rules, and sorting codes
- Single source of truth for tax categorization

### Excel Generation
- Embedded receipt images for visual review
- Data validation dropdowns for categories and document types
- Conditional formatting for VAT validation errors
- Hebrew field names and interface
- True XLS format (Excel 97-2003) using xlwt for iCount compatibility

### Comprehensive Logging
- **YAML format** with pipe notation for multiline strings
- Complete request/response logging with timing
- API metadata (model, parameters, response times)
- Full rendered prompts with category context
- Processing statistics and error details

### Receipt File Organization
- Locates originals via the workbook's source hyperlink, the working directory, `./receipts`, or a `--receipts-source-dir` you pass
- Standardized naming convention: `YYYYMMDD_<receipt_id>__<vendor_name>.{extension}`
- Preserves original file format and quality
- Comprehensive copying statistics and error tracking

## 📊 Project Structure

```
receipt_processing_system/
├── receipt_extractor.py          # Stage 1: Extract receipt data
├── receipt_consolidator.py       # Stage 2: Consolidate to iCount format
├── vat_report.py                 # Bi-monthly VAT report from iCount exports
├── shared/                       # Shared utilities
│   ├── openai_client.py          # OpenAI API integration with Responses API
│   ├── image_handler.py          # Image/PDF processing (incl. raster-PDF detection)
│   ├── excel_generator.py        # Excel file creation
│   ├── excel_config.py           # Review-workbook layout (config/excel_layout.yaml)
│   ├── receipt_checks.py         # Structural checks shared by extractor and audit
│   ├── personal_config.py        # CONFIG.personal.yaml loader
│   └── logger.py                 # YAML logging utilities
├── tools/
│   ├── audit_batch.py            # Audit subcommands over extraction batches
│   └── personal_backup.py        # Private overlay backup of *.personal.* files
├── config/
│   └── excel_layout.yaml         # Review-workbook cell layout
├── prompts/                      # Jinja2 templates and schemas
│   ├── receipt_extraction_prompt.j2   # OpenAI prompt template
│   └── receipt_extraction_schema.json # JSON schema for structured output
├── docs/                         # Documentation
│   ├── extraction-prompt/        # Prompt sources (001-icount-categories.md, ...)
│   ├── personal-backup.md        # Personal-files overlay backup
│   ├── product-requirements.md   # Product requirements (historical)
│   ├── technical-spec.md         # Technical specifications (historical)
│   └── icount-expenses-sample.xls # Sample iCount export
├── tests/                        # pytest suite (uv run pytest tests/)
├── CONFIG.example.yaml           # Template for CONFIG.personal.yaml
├── RECURRING_VENDORS.example.yaml # Template for RECURRING_VENDORS.personal.yaml
├── pyproject.toml                # Dependencies (managed with uv)
└── .env.example                  # Environment configuration template
```

Untracked `*.personal.*` files (audit knowledge, prompt additions, personal
config) are version-tracked in place by a private overlay repo — see
`docs/personal-backup.md`.

## 📈 Israeli Tax Categories

The system includes comprehensive Israeli tax categories with:
- VAT percentages (0%, 18%, 66%)
- Income tax deductibility rules
- Sorting codes for accounting systems
- Special handling for home office, vehicle, and mixed expenses
- Notes on documentation requirements

Categories include:
- Office & Administrative (משרדיות, חומרי ניקוי)
- Technology & Communication (אינטרנט, סלולר, תוכנות)
- Professional Services (רו״ח, עו״ד, יועצים)
- Marketing & Sales (פרסום, שיווק)
- Vehicle Expenses (דלק, ביטוח רכב)
- Home Office (חשמל, מים, ארנונה)
- And many more...

## 🔧 Configuration Options

### Environment Variables (.env)
```
OPENAI_API_KEY=your-api-key-here      # Required: OpenAI API key
MODEL=gpt-5-mini                      # Optional: OpenAI model
MAX_CONCURRENT_REQUESTS=100           # Optional: Parallel processing limit
RECEIPTS_PER_FILE=100                 # Optional: Receipts per Excel file
```

### Personal Configuration (optional)

- `CONFIG.personal.yaml` (copy `CONFIG.example.yaml`): income-tax advance rate,
  the business's own tax ids (flagged if extracted as a vendor id)
- `RECURRING_VENDORS.personal.yaml` (copy `RECURRING_VENDORS.example.yaml`):
  vendors expected every period, checked by `tools/audit_batch.py recurring`

Both are gitignored; see `docs/personal-backup.md` for how they are backed up.

### Command Line Options

**receipt_extractor.py:**
- `--output`: Output directory (default: ./receipts_extracted)
- `--concurrent`: Max concurrent requests (default: 100)
- `--receipts-per-file`: Receipts per Excel file (default: 100)
- `--model`: OpenAI model (default: gpt-5-mini, or `MODEL` env var)
- `--period YYYY-MM`: Flag receipts dated outside the reporting period
- `--api-key`: Override API key

**receipt_consolidator.py:**
- `--output`: Output directory (default: ./receipts_consolidated)
- `--receipts-source-dir`: Extra folder to search for original receipt files

**vat_report.py:**
- `--income` / `--expenses`: iCount export files (either may be omitted, not both)
- `--output`: Output directory
- `--advance-rate`: Income-tax advance rate in percent (overrides CONFIG.personal.yaml)

## 🐛 Troubleshooting

### Common Issues

1. **PDF Processing Errors**
   - Ensure Poppler is installed
   - Check PDF isn't password protected

2. **API Rate Limits**
   - Reduce `--concurrent` parameter
   - Check OpenAI API usage limits

3. **Template Loading Errors**
   - Verify `prompts/` directory exists
   - Check `docs/extraction-prompt/001-icount-categories.md` is present

### Log Analysis
- Check YAML logs in `llm_logs/` directories
- Review `api_metadata` section for timing and errors
- Examine `prompt_used` field for template rendering issues

## 📄 Documentation

- **Personal Files Backup**: `docs/personal-backup.md`
- **Israeli Tax Categories**: `docs/extraction-prompt/001-icount-categories.md`
- **Product Requirements** (historical): `docs/product-requirements.md`
- **Technical Specifications** (historical): `docs/technical-spec.md`

## 🔒 Security & Privacy

- OpenAI API processes data according to their privacy policy
- No data stored permanently on OpenAI servers (store=false)
- API keys never logged or transmitted
- All processing logs stored locally

## 📊 Performance

**Processing Speed:** 100 receipts: ~2-3 minutes (concurrent processing)
**Cost:** ~$0.50 per 100 receipts using gpt-5-mini

## 📜 License

Copyright (c) 2025 Michael Litvin

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0) - see the [LICENSE](LICENSE) file for details.

### Important Disclaimer

**⚠️ FINANCIAL DATA PROCESSING NOTICE**: This software is provided "AS IS" without warranty of any kind. While designed to assist with receipt processing and tax reporting, it may contain errors or produce inaccurate results. You are solely responsible for:

- Verifying all extracted data for accuracy
- Ensuring compliance with Israeli tax laws and regulations
- Consulting with qualified tax professionals and accountants
- Any financial or legal consequences resulting from use of this software

The authors and copyright holders bear NO responsibility for accounting mistakes, tax errors, financial losses, or any other damages arising from the use of this software.

Always review processed receipts carefully and consult with a certified tax advisor before submitting any tax-related documentation.

---

**Note**: This system assists with receipt processing but doesn't replace professional tax advice. Always consult with a tax professional for compliance verification.
