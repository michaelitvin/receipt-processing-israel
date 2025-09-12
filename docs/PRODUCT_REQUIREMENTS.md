# Receipt Processing System
## Product Requirements Document

### Version: 2.0
### Date: September 11, 2025

---

## Executive Summary

User-friendly receipt processing system that extracts data from receipts and generates Excel-based review interfaces. The system uses AI-powered extraction and classification while providing visual editing capabilities through spreadsheet applications compatible with macOS Numbers and LibreOffice Calc.

---

## Business Objectives

- **Streamline Review Process**: Enable visual side-by-side receipt and data review
- **Minimize Development**: Leverage existing commodity software with minimal custom code
- **Cross-Platform Compatibility**: Support macOS Numbers and LibreOffice Calc
- **Maintain Accuracy**: Provide formula-based verification for extracted data

---

## System Architecture

### Stage 1: Parse + Classify → Excel Batches

**Purpose**: Extract data and classify receipts in a single AI call, outputting user-friendly Excel files

**Component**: `receipt_extractor.py`

**Key Features**:
- Single LLM call combines extraction and classification
- Generate .xlsx files with embedded receipt images
- One worksheet per receipt for focused editing

**Inputs**:
- Receipt images/PDFs folder
- Classification categories (provided by user in markdown format)
- OpenAI API credentials

**Outputs**:
- Batch Excel files (max configurable amount of receipts per file - default 10)
- Processing logs and error reports
- Excel worksheets with embedded receipt image, extracted data, classification results, and formulas

### Stage 2: Consolidate → iCount-Ready Export

**Purpose**: Aggregate edited data from all batch files into accounting-ready format

**Component**: `receipt_consolidator.py`

**Inputs**:
- Directory containing edited batch Excel files
- Configuration for output format

**Outputs**:
- Single consolidated Excel file
- Only "deductible" line items included
- Links to original receipt images/pdfs
- Ready for import into iCount accounting system

---

## Excel Worksheet Design

### Layout Specifications

**Layout**:
- **Left Side**: Data fields starting from column A
- **Right Side**: Receipt image (large, merged cells starting from column H)

### Header Information Section
Location: Columns A-D, Rows 1-12

| שם שדה | ערך | אימות | הערות |
|--------|------|-------|-------|
| מספר קבלה | [Extracted] | | |
| ספק | [Extracted] | | |
| תאריך | [Extracted] | | |
| סוג מסמך | [Dropdown: חשבונית/קבלה/חשבונית+קבלה] | | |
| סה"כ ללא מע"מ | [Extracted] | [Calculated] | |
| מע"מ | [Extracted] | [Calculated] | |
| סה"כ כולל מע"מ | [Extracted] | [Calculated] | |
| קטגוריה | [Dropdown] | | |
| קישור למקור | [Link to original file] | | |

### Line Items Table
Location: Columns A-G, Starting Row 14

| תיאור | סה"כ ללא מע"מ | מע"מ | אחוז מע"מ | סה"כ כולל מע"מ | ניתן לניכוי | הערות |
|-------|---------------|------|-----------|----------------|-----------|-------|
| [Item 1] | [Amount] | [VAT] | `=VAT/Total*100` | [Total] | ☑️ | |
| [Item 2] | [Amount] | [VAT] | `=VAT/Total*100` | [Total] | ☐ | |

### Verification Formulas and Validation

**Header Verification**:
- VAT Percentage: `=(VAT_Amount/Total_excl_VAT)*100`
- Total Check: `=Total_excl_VAT+VAT_Amount` (compare to `Total_incl_VAT`)
- Notes Column Validation:
  - **Red cell with error message** if total check fails
  - **Yellow cell with warning** if VAT percentage not close to 0% or 18% (using float precision)

**Line Item Verification**:
- Per-item VAT %: `=(VAT_Column/Total_excl_VAT_Column)*100`  
- Items Sum: `=SUM(item_totals)` (compare to receipt total)
- Notes Column Validation:
  - **Red cell with error message** if item calculation fails
  - **Yellow cell with warning** if VAT percentage not close to 0% or 18% (using float precision)

**Validation Rules**:
- Total verification: `Total_excl_VAT + VAT_Amount = Total_incl_VAT` (exact equality)
- VAT rate validation: Show warning if `ABS(VAT% - 0) > 0.1` AND `ABS(VAT% - 18) > 0.1` (float precision for VAT% only)
- Error messages appear in notes column with appropriate cell formatting

---

## Classification System

### Receipt-Level Classification

**Category Dropdown** (from `ICOUNT_CATEGORIES.md`):
- Pre-defined expense categories with VAT and income tax rates
- Use category name in the dropdown
- Use the content of `ICOUNT_CATEGORIES.md` in the LLM prompt

**Classification Logic**:
- AI will suggest category based on vendor and item description
- User can override via dropdown in Excel
- Categories directly map to iCount expense type names

### Line Item Classification

**Deductible Status**:
- Checkbox for each line item
- TRUE = Include in final export (default for all items)
- FALSE = Exclude from tax reporting
- User manually unchecks items they don't want to deduct
- Stage 2 consolidation only includes items marked TRUE

---

## File Organization

### Batch Files Structure
```
receipts_extracted/
├── receipts_batch_001.xlsx (receipts 1-10)
├── receipts_batch_002.xlsx (receipts 11-20)
└── receipts_batch_003.xlsx (receipts 21-30)
```

### Worksheet Naming Convention
- Worksheet tabs: "R001", "R002", etc.
- File references maintained for consolidation

---

## Technical Requirements

### Excel Compatibility
- **Format**: .xlsx (Excel 2007+)
- **Compatible Applications**:
  - macOS Numbers (primary target)
  - LibreOffice Calc
  - Microsoft Excel (if available)
- **Features Used**:
  - Merged cells for images
  - Data validation (dropdowns, colored cells)
  - Basic formulas

### AI Processing
- **Model**: OpenAI gpt-5-mini
- **Structured Output**: Use `response_format` parameter for JSON schema validation (see https://platform.openai.com/docs/guides/structured-outputs)
- **Extraction and Classification Prompt**: Single call for extraction + classification + document type identification using categories from `ICOUNT_CATEGORIES.md`
- **Temperature**: Unsupported in gpt-5-mini
- **Parallel Processing**: Configurable workers for batch processing

### Image Handling
- **Embedding**: Images inserted into merged Excel cells
- **Size**: Large enough for clear reading (~200px height)
- **Format**: Preserve aspect ratio

---

## User Workflow

### Process
1. **Run Stage 1**: `python receipt_extractor.py ./receipts`
2. **Review Batches**: Open Excel files in Numbers/LibreOffice
3. **Edit Data**: Correct any extraction errors
4. **Update Classifications**: Adjust categories and deductible status
5. **Save In-Place**: Standard Save operation
6. **Consolidate**: `python receipt_consolidator.py ./receipts_extracted`
7. **Import to iCount**: Import consolidated Excel file

### Editing Experience
- Visual comparison between receipt image and extracted data
- Direct cell editing for all fields
- Immediate formula feedback for verification
- Checkbox interaction for deductible items
- Dropdown selection for categories

---

## Output Specifications

### Consolidated Excel for iCount

**iCount Import Format**:

Documentation: https://help.icount.co.il/expenses/import-from-excel/
Sample file: `iCount-Expenses-sample.xls` in the repo

| Column | Hebrew Field Name | English Translation | Type | Required | Description |
|--------|-------------------|-------------------|------|----------|-------------|
| A | תז/חפ הספק | Supplier Tax ID/Business Number | Text | Optional | ח.פ./עוסק מורשה from receipt |
| B | שם הספק | Supplier Name | Text | Optional | Vendor name from receipt |
| C | שם סוג הוצאה | Expense Type Name | Text | Required | Category from classification |
| D | סכום ההוצאה | Amount | Numeric | Required | Total amount including VAT |
| E | מטבע | Currency | Numeric | Optional | 1=Euro, 2=Dollar, 3=Yen, 4=Pound, 5=Shekel (default if blank) |
| F | שער | Exchange Rate | Numeric | Optional | Leave blank for system to query Bank of Israel |
| G | סוג מסמך | Document Type | Text | Optional | "receipt" or "invrec" (default if blank) |
| H | מספר מסמך | Document Number | Text | Required | Receipt number or generated ID |
| I | תאריך האסמכתא | Document Date | Date | Required | yyyy-mm-dd format (today if blank) |
| J | תאריך התשלום | Payment Date | Date | Required | yyyy-mm-dd format (today if blank) |
| K | ההוצאה שולמה | Paid Status | Numeric | Required | 1=yes, 0=no |
| L | שולמה בתאריך | Payment Date (if paid) | Date | Optional | yyyy-mm-dd (today if K=1 and blank) |
| M | שיוך לתאריך דיווח שונה | Reporting Date | Date | Optional | For retroactive reporting only |
| N | לקוח | Customer | Text | Optional | Customer name/ID (must exist in system) |
| O | פרויקט | Project | Text | Optional | Project name (must exist in system) |

**Key Requirements** (verified from sample):
- First row contains Hebrew column headers exactly as shown above
- Data starts from row 2
- Only "Deductible" = TRUE line items included
- Currency: Leave blank (defaults to Shekel) or use 5 for Shekel explicitly
- Document type: Leave blank (defaults to "invrec") or use "receipt"
- Paid status: 1 (yes, assuming all receipts are paid expenses)
- All dates in yyyy-mm-dd format
- System will auto-create new suppliers and expense types if not existing

**Sample Data Row** (from iCount file):
```
123456789 | ספקון ספקיהו | שירותי מחשוב | 1170 | [blank] | [blank] | [blank] | 12345 | 2021-07-25 | [blank] | 1 | [blank] | [blank] | [blank] | [blank]
```

**Category Mapping**: 
- Receipt categories from `ICOUNT_CATEGORIES.md` map directly to Column C (שם סוג הוצאה)
- Example: "תוכנות ומנויים" for software subscriptions, "משרדיות" for office supplies
- AI classification will select appropriate category during extraction

**Document Type Mapping**:
- חשבונית (Invoice) → "invoice" 
- קבלה (Receipt) → "receipt"
- חשבונית+קבלה (Invoice+Receipt) → "invrec"
- AI will identify document type based on presence of tax invoice elements, receipt characteristics, or combined format

---

## Quality Assurance

### Data Verification
- Formula-based validation in each worksheet
- Automatic highlighting of discrepancies
- Sum validation across line items
- VAT percentage verification

### Error Handling
- Failed extractions logged separately
- Malformed data warnings
- Processing statistics summary

---

## Conclusion

This system provides a streamlined receipt processing workflow using familiar spreadsheet-based tools. By combining AI processing with visual editing capabilities, users can efficiently review, correct, and classify receipts while maintaining the accuracy and auditability required for Israeli tax compliance.

The solution leverages familiar Excel-based tools available on macOS and other platforms, enabling an intuitive workflow from receipt extraction through accounting system import.
