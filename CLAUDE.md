# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a two-stage receipt processing system for Israeli tax reporting that uses OpenAI's API to extract structured data from receipts and prepare it for import into iCount accounting software.

**Stage 1**: `receipt_extractor.py` - Extracts data from receipt images/PDFs using OpenAI's Responses API, generates Excel files with embedded images for review
**Stage 2**: `receipt_consolidator.py` - Processes reviewed Excel files and consolidates them into iCount-ready CSV format

## Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and add OPENAI_API_KEY
```

### Running the System

**Stage 1 - Extract receipts:**
```bash
python receipt_extractor.py /path/to/receipts/folder

# With options:
python receipt_extractor.py /path/to/receipts --concurrent 3 --receipts-per-file 5 --output ./output
```

**Stage 2 - Consolidate to iCount format:**
```bash
python receipt_consolidator.py path/to/excel1.xlsx path/to/excel2.xlsx

# With custom output:
python receipt_consolidator.py *.xlsx --output ./consolidated
```

### Dependencies
- Python 3.13+
- Poppler (for PDF processing): `brew install poppler` (macOS) or `sudo apt-get install poppler-utils` (Linux)
- OpenAI API key from https://platform.openai.com/

## Architecture & Key Components

### Core Processing Pipeline
1. **Image/PDF Processing** (`shared/image_handler.py`): Converts receipts to base64 for API submission
2. **OpenAI Integration** (`shared/openai_client.py`): Uses Responses API with structured JSON output, includes Jinja2 template rendering
3. **Excel Generation** (`shared/excel_generator.py`): Creates Excel files with embedded images, data validation, and conditional formatting
4. **Logging** (`shared/logger.py`): Comprehensive YAML logging with full prompts and API metadata

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
- Default model: gpt-4o-mini (configurable)
- Concurrent request limits and batch sizes

## Israeli Tax Context

The system handles Israeli-specific tax requirements:
- VAT rates: 0%, 18%, 66% (for non-deductible items)
- Document types: Invoice, Receipt, or Invoice+Receipt
- Deductibility rules for home office, vehicles, and mixed expenses
- iCount category mappings with sorting codes

## Important Notes

- No test suite currently exists
- No linting/formatting tools configured
- Logs are stored in `llm_logs/` directories with YAML format
- Failed receipts automatically generate empty Excel batches for manual entry
- All processing is asynchronous with configurable concurrency