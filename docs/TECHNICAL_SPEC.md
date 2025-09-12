# Receipt Processing System - Technical Specification

### Version: 2.0
### Date: September 11, 2025

---

## Architecture Overview

### System Components

```
receipt_processing_system/
├── receipt_extractor.py      # Stage 1: Extract + Classify → Excel
├── receipt_consolidator.py   # Stage 2: Consolidate → iCount format
├── shared/
│   ├── __init__.py
│   ├── openai_client.py     # OpenAI API wrapper
│   ├── excel_generator.py   # Excel file creation
│   ├── image_handler.py     # Image processing utilities
│   └── logger.py            # Logging and JSON dumping
├── .env                     # Configuration
├── requirements.txt
└── ICOUNT_CATEGORIES.md     # Expense categories
```

### Data Flow

```
[Receipt Images/PDFs] 
    ↓
[Stage 1: receipt_extractor.py]
    ↓ (OpenAI API + Image Processing)
[Batch Excel Files (.xlsx)]
    ↓ (Manual Review/Editing)
[Stage 2: receipt_consolidator.py]
    ↓ (Excel Reading + Filtering)
[Consolidated iCount Excel]
```

---

## Stage 1: Receipt Extractor

### Core Architecture

```python
# receipt_extractor.py - Main script
async def main():
    - Parse command line arguments
    - Initialize OpenAI client
    - Scan input directory for receipts
    - Process receipts in parallel using asyncio
    - Generate batch Excel files
    - Create processing summary

# Key async functions
async def process_receipts_batch(receipts: List[Path]) -> List[ProcessedReceipt]
async def extract_single_receipt(receipt_path: Path) -> ProcessedReceipt
async def generate_excel_batch(receipts: List[ProcessedReceipt]) -> Path
```

### OpenAI Integration

**File**: `shared/openai_client.py`

```python
class OpenAIClient:
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key)
    
    async def extract_receipt_data(
        self, 
        image_data: bytes, 
        categories: List[str]
    ) -> ReceiptData:
        # Use gpt-5-mini with structured output
        # response_format with JSON schema
        # Include image + categories in prompt
        pass
```

**Structured Output Schema** (High-level):
```python
{
    "receipt_info": {
        "number": "string",
        "vendor": "string", 
        "date": "YYYY-MM-DD",
        "document_type": "invoice|receipt|invoice+receipt",
        "original_file": "string"  # Original filename for link
    },
    "amounts": {
        "total_excl_vat": "number",
        "vat_amount": "number", 
        "total_incl_vat": "number"
    },
    "line_items": [
        {
            "description": "string",
            "amount_excl_vat": "number",
            "vat": "number",
            "total": "number",
            "deductible": "boolean"  # Default: true
        }
    ],
    "classification": {
        "category": "string",  # From ICOUNT_CATEGORIES.md
        "confidence": "number"
    }
}
```

### Excel Generation

**File**: `shared/excel_generator.py`

```python
class ExcelGenerator:
    def create_batch_workbook(self, receipts: List[ProcessedReceipt]) -> Workbook:
        # Create workbook with worksheets R001, R002, etc.
        # Each worksheet: receipt image + data + formulas
        pass
    
    def create_receipt_worksheet(self, ws: Worksheet, receipt: ProcessedReceipt):
        # Layout: Data (A-D), Image (H+)  
        # Header info rows 1-12, Line items from row 14
        # Add formulas, dropdowns, validation
        # Embed receipt image in merged cells
        pass
```

**Key Excel Features**:
- Image embedding using `openpyxl.drawing.image.Image`
- Data validation for dropdowns (categories, document types)
- Conditional formatting for validation errors (red/yellow cells)
- Formulas for VAT percentage and total verification
- Hyperlinks to original receipt files
- Notes column for validation messages

**Excel-Only Calculated Fields**:
- VAT % column: `=(VAT/Amount_excl_VAT)*100` (formula, not stored in JSON)
- Verification columns: Calculated values to compare against extracted
- Notes column: Auto-populated with validation warnings/errors

### Error Handling & Logging

**File**: `shared/logger.py`

```python
class ReceiptLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
    
    def log_llm_interaction(
        self, 
        receipt_file: str,
        request_data: dict,
        response_data: dict = None,
        error: Exception = None
    ):
        # Dump complete LLM request/response/error to JSON
        # Filename: llm_call_{receipt}_{timestamp}.json
        pass
    
    def log_processing_stats(self, stats: ProcessingStats):
        # Summary JSON with success/failure counts
        pass
```

**Error Strategy**:
- Try/catch around each receipt processing
- Continue processing other receipts on single failures  
- Log all LLM inputs, outputs, and errors to JSON files
- Create processing summary with success/failure counts

---

## Stage 2: Receipt Consolidator

### Core Architecture

```python
# receipt_consolidator.py - Main script
async def main():
    - Parse command line arguments  
    - Scan for batch Excel files
    - Read and parse all worksheets
    - Filter deductible line items
    - Generate iCount-compatible Excel
    - Create consolidation report

# Key functions  
def read_batch_excel(file_path: Path) -> List[ProcessedReceipt]
def filter_deductible_items(receipts: List[ProcessedReceipt]) -> List[LineItem]
def generate_icount_excel(items: List[LineItem]) -> Path
```

### Excel Reading

```python
class ExcelReader:
    def read_receipt_worksheet(self, ws: Worksheet) -> ProcessedReceipt:
        # Parse header data (rows 1-12)
        # Parse line items (from row 14)
        # Extract deductible status from checkboxes
        # Handle document type and category mappings
        pass
    
    def extract_line_items(self, ws: Worksheet) -> List[LineItem]:
        # Read line items table
        # Filter where deductible = TRUE
        # Include original receipt reference
        pass
```

### iCount Export Generation

```python
class iCountExporter:
    def create_icount_excel(self, items: List[LineItem]) -> Workbook:
        # Create Excel with Hebrew headers (from PRD)
        # Map document types: חשבונית→invoice, etc.
        # Add hyperlinks to original receipts
        # Apply iCount formatting requirements
        pass
```

---

## Configuration & Environment

### .env File Structure
```bash
# OpenAI Configuration
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-mini

# Processing Configuration  
MAX_CONCURRENT_REQUESTS=20
RECEIPTS_PER_FILE=10

# Output Configuration
OUTPUT_DIR=./receipts_extracted
LOG_LEVEL=INFO
```

### Command Line Interfaces

**Stage 1 - Receipt Extractor**:
```bash
python receipt_extractor.py [receipts_dir] [options]

Options:
  --output DIR          Output directory (default: ./receipts_extracted)
  --concurrent INT      Max concurrent API requests (default: 5) 
  --receipts-per-file INT  Receipts per Excel file (default: 10)
  --api-key KEY         OpenAI API key (overrides .env)
```

**Stage 2 - Receipt Consolidator**:
```bash
python receipt_consolidator.py [batch_dir] [options]

Options:
  --output FILE         Output Excel file path
  --filter-deductible   Only include deductible=TRUE items (default)
```

---

## Parallel Processing

### Async/Await Implementation

```python
# Concurrent receipt processing
async def process_receipts_parallel(
    receipts: List[Path], 
    max_concurrent: int = 5
) -> List[ProcessedReceipt]:
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_with_semaphore(receipt_path: Path):
        async with semaphore:
            return await extract_single_receipt(receipt_path)
    
    tasks = [process_with_semaphore(receipt) for receipt in receipts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Handle exceptions in results
    return [r for r in results if not isinstance(r, Exception)]
```

**Rate Limiting**:
- Semaphore-based concurrency control
- Configurable concurrent request limits 
- OpenAI API rate limit handling
- Exponential backoff for API errors

---

## File Organization Patterns

### Input Structure
```
receipts/
├── receipt_001.jpg
├── RCPT_2.pdf  
├── invoice_03_.png
└── ...
```

### Stage 1 Output
```
receipts_extracted/
├── receipts_batch_001.xlsx    # Receipts 1-10
├── receipts_batch_002.xlsx    # Receipts 11-20  
├── processing_summary.json
└── llm_logs/
    ├── llm_call_receipt_001_20250911_143022.json
    └── ...
```

### Stage 2 Output
```
consolidated_receipts_20250911.xlsx   # iCount-ready format
consolidation_report.json             # Processing summary
```

---

## Data Models

### Core Data Structures

```python
@dataclass
class ReceiptInfo:
    number: str
    vendor: str
    date: str
    document_type: str  # "invoice", "receipt", "invoice+receipt"
    original_file: str  # For creating link to source
    
@dataclass  
class AmountData:
    total_excl_vat: Decimal
    vat_amount: Decimal
    total_incl_vat: Decimal
    
@dataclass
class LineItem:
    description: str
    amount_excl_vat: Decimal
    vat: Decimal
    total: Decimal
    deductible: bool = True
    
@dataclass
class ProcessedReceipt:
    file_path: Path
    receipt_info: ReceiptInfo
    amounts: AmountData
    line_items: List[LineItem]
    classification: Classification
    processing_status: str
    validation_notes: Dict[str, str] = None  # For Excel notes column
    
@dataclass
class Classification:
    category: str
    confidence: float
    document_type_mapping: str  # For iCount export
```

---

## Validation & Quality Assurance

### Excel Formula Validation
```python
# Validation formulas embedded in Excel
VAT_PERCENTAGE_FORMULA = "=(C{row}/B{row})*100"
TOTAL_CHECK_FORMULA = "=B{row}+C{row}"
VAT_WARNING_FORMULA = "=IF(AND(ABS(D{row})>0.1,ABS(D{row}-18)>0.1),\"⚠️ Unusual VAT rate\",\"\")"
ERROR_CHECK_FORMULA = "=IF(E{row}<>D{row},\"❌ Total mismatch\",\"\")"
```

### Data Integrity Checks
- Verify extracted amounts match line item sums
- Flag unusual VAT rates (not 0% or 18%)
- Validate document type consistency
- Check required fields are populated

---

## Dependencies

### Python Requirements
**Python Version**: 3.13+

```txt
openai>=1.0.0
openpyxl>=3.1.0
Pillow>=10.0.0
python-dotenv>=1.0.0
pdf2image>=1.16.3
aiofiles>=23.0.0
asyncio-throttle>=1.0.0
```

### External Dependencies
- OpenAI API access with gpt-5-mini
- Poppler (for PDF processing)
- Excel-compatible application for review (Numbers/LibreOffice)

---

## Testing Strategy

### Unit Testing
- Mock OpenAI API responses for consistent testing
- Excel generation validation
- Data structure serialization/deserialization
- Error handling scenarios

### Integration Testing  
- End-to-end workflow with sample receipts
- Excel compatibility across Numbers/LibreOffice
- iCount import validation

---

## Deployment & Operations

### Installation Process
1. Clone repository
2. Create Python virtual environment: `python3 -m venv .venv`
3. Activate virtual environment: `source .venv/bin/activate` (macOS/Linux) or `.venv\Scripts\activate` (Windows)
4. Install Python dependencies: `pip install -r requirements.txt`
5. Install Poppler for PDF support
6. Configure `.env` with OpenAI API key
7. Test with sample receipts

### Monitoring & Maintenance
- Processing logs for troubleshooting
- API usage tracking
- Success/failure rate monitoring
- Regular testing with new receipt formats
