# Receipt Processing System for Israeli Tax Reporting

An intelligent two-stage receipt processing system using OpenAI's API for accurate data extraction and Excel-based review workflow.

## 🎯 System Overview

The system provides a streamlined approach to processing receipts for Israeli tax compliance:

1. **Stage 1: Data Extraction** (`receipt_extractor.py`)
   - Extracts structured data from receipt images and PDFs
   - Uses OpenAI's Responses API with custom Jinja2 templates
   - Parallel processing with configurable concurrency
   - Generates Excel files with embedded images for review
   - Creates failed receipt batches for manual entry

2. **Stage 2: Consolidation** (`receipt_consolidator.py`)
   - Processes reviewed Excel files
   - Consolidates data into iCount-ready CSV format
   - Maintains data integrity and validation

## ⚡ Key Features

- **Direct PDF Support**: Processes PDFs natively via OpenAI Responses API
- **Template-Based Prompts**: Uses Jinja2 templates with full Israeli tax category context
- **Comprehensive YAML Logging**: Detailed logs with timing, metadata, and full prompts
- **Excel Review Workflow**: Visual review with embedded images and validation formulas
- **Failed Receipt Handling**: Automatic empty batch files for processing failures
- **Israeli Tax Compliance**: Built-in VAT rules, deductibility, and category mappings

## 🚀 Quick Start

### Prerequisites

1. **Python 3.13+** installed
2. **OpenAI API Key** from [OpenAI Platform](https://platform.openai.com/)
3. **Poppler** for PDF support:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install poppler-utils
   
   # macOS
   brew install poppler
   ```

### Installation

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and configure your API key:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your OpenAI API key:
   ```
   OPENAI_API_KEY=your-openai-api-key-here
   MAX_CONCURRENT_REQUESTS=5
   RECEIPTS_PER_FILE=10
   ```

### Usage

#### Stage 1: Extract Receipt Data

```bash
# Basic usage
python receipt_extractor.py /path/to/receipts/folder

# With custom concurrency and batch size
python receipt_extractor.py /path/to/receipts/folder --concurrent 3 --receipts-per-file 5

# With custom output directory
python receipt_extractor.py /path/to/receipts/folder --output ./my_extractions
```

This creates:
- Excel files with receipt data and embedded images
- Failed receipt batches for manual entry
- Comprehensive YAML logs of all API interactions
- Processing summary with statistics

#### Stage 2: Consolidate to iCount Format

```bash
# Process reviewed Excel files
python receipt_consolidator.py path/to/receipts_batch_001.xlsx path/to/receipts_batch_002.xlsx

# With custom output directory
python receipt_consolidator.py *.xlsx --output ./consolidated_output
```

This generates:
- iCount-ready CSV import file
- Consolidation summary with statistics
- YAML processing logs

## 📋 Technical Details

### OpenAI Integration
- Uses **Responses API** with gpt-4o-mini model
- Direct PDF and image processing support
- Structured JSON output with strict schema validation
- Full Israeli tax category context in prompts

### Template System
- **Jinja2 templates** for maintainable prompts
- Full `ICOUNT_CATEGORIES.md` content included in prompts
- VAT rates, deductibility rules, and sorting codes
- Single source of truth for tax categorization

### Excel Generation
- Embedded receipt images for visual review
- Data validation dropdowns for categories and document types
- Conditional formatting for VAT validation errors
- Hebrew field names and interface

### Comprehensive Logging
- **YAML format** with pipe notation for multiline strings
- Complete request/response logging with timing
- API metadata (model, parameters, response times)
- Full rendered prompts with category context
- Processing statistics and error details

## 📊 Project Structure

```
receipt_processing_system/
├── receipt_extractor.py          # Stage 1: Extract receipt data
├── receipt_consolidator.py       # Stage 2: Consolidate to iCount format
├── shared/                       # Shared utilities
│   ├── openai_client.py          # OpenAI API integration with Responses API
│   ├── image_handler.py          # Image/PDF processing
│   ├── excel_generator.py        # Excel file creation
│   └── logger.py                 # YAML logging utilities
├── prompts/                     # Jinja2 templates and schemas
│   ├── receipt_extraction_prompt.j2  # OpenAI prompt template
│   └── receipt_extraction_schema.json # JSON schema for structured output
├── docs/                        # Documentation
│   ├── ICOUNT_CATEGORIES.md     # Israeli tax categories
│   ├── PRODUCT_REQUIREMENTS.md  # Product requirements
│   ├── TECHNICAL_SPEC.md        # Technical specifications
│   ├── TROUBLESHOOTING.md       # Troubleshooting guide
│   └── iCount-Expenses-sample.xls       # Sample Excel output
├── requirements.txt             # Python dependencies
└── .env.example                 # Environment configuration template
```

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
MAX_CONCURRENT_REQUESTS=5             # Optional: Parallel processing limit
RECEIPTS_PER_FILE=10                  # Optional: Receipts per Excel file
```

### Command Line Options

**receipt_extractor.py:**
- `--output`: Output directory (default: ./receipts_extracted)
- `--concurrent`: Max concurrent requests (default: 5)
- `--receipts-per-file`: Receipts per Excel file (default: 10)
- `--api-key`: Override API key

**receipt_consolidator.py:**
- `--output`: Output directory (default: ./receipts_consolidated)

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
   - Check `docs/ICOUNT_CATEGORIES.md` is present

### Log Analysis
- Check YAML logs in `llm_logs/` directories
- Review `api_metadata` section for timing and errors
- Examine `prompt_used` field for template rendering issues

For detailed troubleshooting, see `docs/TROUBLESHOOTING.md`.

## 📄 Documentation

- **Product Requirements**: `docs/PRODUCT_REQUIREMENTS.md`
- **Technical Specifications**: `docs/TECHNICAL_SPEC.md`
- **Troubleshooting Guide**: `docs/TROUBLESHOOTING.md`
- **Israeli Tax Categories**: `docs/ICOUNT_CATEGORIES.md`

## 🔒 Security & Privacy

- OpenAI API processes data according to their privacy policy
- No data stored permanently on OpenAI servers (store=false)
- API keys never logged or transmitted
- All processing logs stored locally

## 📊 Performance

Typical processing times for 10 receipts:
- **Sequential**: ~2-3 minutes
- **Concurrent (5)**: ~45-60 seconds
- **Concurrent (10)**: ~30-45 seconds

Processing time depends on:
- Receipt complexity
- Image/PDF size
- OpenAI API response times
- Network latency

---

**Note**: This system assists with receipt processing but doesn't replace professional tax advice. Always consult with a tax professional for compliance verification.