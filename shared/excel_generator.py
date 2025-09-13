"""Excel file generation for receipt processing"""

import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from decimal import Decimal
import openpyxl
from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.styles import Color, PatternFill, Font

logger = logging.getLogger(__name__)


class ExcelGenerator:
    """Generate Excel files for receipt data"""
    
    # Header field names in Hebrew
    HEADER_FIELDS = [
        ('מספר קבלה', 'number'),
        ('ספק', 'vendor'),
        ('תאריך', 'date'),
        ('סוג מסמך', 'document_type'),
        ('מטבע', 'currency'),
        ('סה"כ ללא מע"מ', 'total_excl_vat'),
        ('מע"מ', 'vat_amount'),
        ('סה"כ כולל מע"מ', 'total_incl_vat'),
        ('קטגוריה', 'category'),
        ('הסבר והנמקה', 'reasoning'),
        ('קישור למקור', 'original_file')
    ]
    
    # Line item column headers
    LINE_ITEM_HEADERS = [
        'תיאור',
        'סה"כ ללא מע"מ', 
        'מע"מ',
        'אחוז מע"מ',
        'סה"כ כולל מע"מ',
        'ניתן לניכוי',
        'הערות'
    ]
    
    # Document type mappings
    DOCUMENT_TYPES = ['חשבונית', 'קבלה', 'חשבונית+קבלה']
    
    def __init__(self, categories_file_path: Path):
        """Initialize with categories file path"""
        self.categories_file_path = categories_file_path
        self.categories = self._load_categories()
        
    def create_batch_workbook(self, receipts: List[Dict[str, Any]], images_dir: Path) -> Workbook:
        """Create workbook with multiple receipt worksheets"""
        wb = Workbook()
        
        # Remove default sheet
        if 'Sheet' in wb.sheetnames:
            wb.remove(wb['Sheet'])
            
        # Create worksheet for each receipt
        for idx, receipt in enumerate(receipts, 1):
            ws_name = f"R{idx:03d}"
            ws = wb.create_sheet(title=ws_name)
            self._create_receipt_worksheet(ws, receipt, images_dir)
            
        return wb
        
    def _create_receipt_worksheet(self, ws: Worksheet, receipt: Dict[str, Any], images_dir: Path):
        """Create a single receipt worksheet with data and image"""
        
        # Set column widths
        ws.column_dimensions['A'].width = 20  # Field names
        ws.column_dimensions['B'].width = 50  # Values (wider for reasoning)
        ws.column_dimensions['C'].width = 15  # Verification
        ws.column_dimensions['D'].width = 30  # Notes
        
        # For line items
        for col in range(5, 8):  # E, F, G
            ws.column_dimensions[get_column_letter(col)].width = 15
            
        # Image columns (H onwards)
        ws.column_dimensions['H'].width = 50
        
        # Add header section
        self._add_header_section(ws, receipt)
        
        # Add line items section
        self._add_line_items_section(ws, receipt.get('line_items', []))
        
        # Add image if available
        self._add_receipt_image(ws, receipt, images_dir)
        
        # Add validation and formatting
        self._add_validation_and_formatting(ws, receipt)
        
    def _add_header_section(self, ws: Worksheet, receipt: Dict[str, Any]):
        """Add header information section"""
        ws['A1'] = 'שם שדה'
        ws['B1'] = 'ערך'
        ws['C1'] = 'אימות'
        ws['D1'] = 'הערות'
        
        # Style headers
        for cell in [ws['A1'], ws['B1'], ws['C1'], ws['D1']]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
        # Add receipt info
        receipt_info = receipt.get('receipt_info', {})
        amounts = receipt.get('amounts', {})
        classification = receipt.get('classification', {})
        
        row = 2
        for hebrew_name, field_key in self.HEADER_FIELDS:
            ws[f'A{row}'] = hebrew_name
            
            # Get value based on field
            if field_key in receipt_info:
                value = receipt_info[field_key]
            elif field_key in amounts:
                value = amounts[field_key]
            elif field_key == 'category':
                value = classification.get('category', '')
            else:
                value = ''
                
            # Special handling for document type
            if field_key == 'document_type':
                ws[f'B{row}'] = self._map_document_type(value)
                # Add dropdown validation
                dv = DataValidation(type="list", formula1='"' + ','.join(self.DOCUMENT_TYPES) + '"')
                dv.add(ws[f'B{row}'])
                ws.add_data_validation(dv)
            elif field_key == 'category':
                ws[f'B{row}'] = value
                # Add category dropdown
                if self.categories:
                    dv = DataValidation(type="list", formula1='"' + ','.join(self.categories) + '"')
                    dv.add(ws[f'B{row}'])
                    ws.add_data_validation(dv)
            elif field_key == 'original_file':
                # Add hyperlink to original file with filename as display text
                original_file_path = receipt_info.get('original_file', '')
                if original_file_path:
                    filename = Path(original_file_path).name
                    ws[f'B{row}'] = filename
                    ws[f'B{row}'].hyperlink = original_file_path
                    ws[f'B{row}'].font = Font(color="0000FF", underline="single")
                else:
                    ws[f'B{row}'] = ''
            elif field_key == 'reasoning':
                # Make reasoning cell multiline with text wrapping
                ws[f'B{row}'] = value
                ws[f'B{row}'].alignment = Alignment(wrap_text=True, vertical='top')
                # Make the row taller for reasoning
                ws.row_dimensions[row].height = 60
            else:
                ws[f'B{row}'] = value
                
            # Add verification formulas for amounts and deductible calculations
            if field_key in ['total_excl_vat', 'vat_amount', 'total_incl_vat']:
                # Replace static values with formulas that subtract non-deductible items
                if field_key == 'total_excl_vat':
                    ws[f'B{row}'] = f'={value}-SUMIF(F15:F115,FALSE,B15:B115)'
                elif field_key == 'vat_amount':
                    ws[f'B{row}'] = f'={value}-SUMIF(F15:F115,FALSE,C15:C115)'
                elif field_key == 'total_incl_vat':
                    ws[f'B{row}'] = f'={value}-SUMIF(F15:F115,FALSE,E15:E115)'
                    # Add verification formula
                    ws[f'C{row}'] = f'=B{row-2}+B{row-1}'
                    
            row += 1
            
    def _add_line_items_section(self, ws: Worksheet, line_items: List[Dict[str, Any]]):
        """Add line items table"""
        start_row = 14
        
        # Add headers
        for col_idx, header in enumerate(self.LINE_ITEM_HEADERS, 1):
            cell = ws.cell(row=start_row, column=col_idx, value=header)
            cell.font = Font(bold=True)
            cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            
        # Add line items
        for idx, item in enumerate(line_items, 1):
            row = start_row + idx
            
            ws.cell(row=row, column=1, value=item.get('description', ''))
            ws.cell(row=row, column=2, value=item.get('amount_excl_vat', 0))
            ws.cell(row=row, column=3, value=item.get('vat', 0))
            
            # VAT percentage formula
            ws.cell(row=row, column=4, value=f'=IF(B{row}=0,0,C{row}/B{row}*100)')
            
            ws.cell(row=row, column=5, value=item.get('total', 0))
            
            # Deductible checkbox
            deductible_cell = ws.cell(row=row, column=6, value=item.get('deductible', True))
            
            # Add checkbox-style data validation
            dv = DataValidation(type="list", formula1='"TRUE,FALSE"', showDropDown=False)
            dv.add(deductible_cell)
            ws.add_data_validation(dv)
            
            # Notes column - add note for non-deductible items
            if not item.get('deductible', True):
                notes_cell = ws.cell(row=row, column=7, value='לא ניתן לניכוי - ראה הסבר בשדה הנמקה')
                notes_cell.alignment = Alignment(wrap_text=True, vertical='top')
            else:
                ws.cell(row=row, column=7, value='')
            
    def _add_receipt_image(self, ws: Worksheet, receipt: Dict[str, Any], images_dir: Path):
        """Add receipt image to worksheet"""
        try:
            # Find the image file
            original_file = receipt.get('receipt_info', {}).get('original_file', '')
            if not original_file:
                return
                
            image_path = images_dir / Path(original_file).with_suffix('.jpg').name
            
            if image_path.exists():
                img = XLImage(str(image_path))
                
                # Scale image to fit in merged cells
                img.width = 400
                img.height = 600
                
                # Position image in column H
                ws.add_image(img, 'H2')
                
                # Merge cells for image area
                ws.merge_cells('H2:K25')
                
                logger.info(f"Added image to worksheet: {image_path.name}")
        except Exception as e:
            logger.error(f"Error adding image to worksheet: {e}")
            
    def _add_validation_and_formatting(self, ws: Worksheet, receipt: Dict[str, Any]):
        """Add conditional formatting and validation"""
        
        # Add conditional formatting for VAT validation
        # Yellow fill for VAT % not 0 or 18
        vat_yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
        vat_rule = FormulaRule(
            formula=['AND(ABS(D15)>0.1,ABS(D15-18)>0.1)'],
            fill=vat_yellow_fill
        )
        ws.conditional_formatting.add('D15:D30', vat_rule)
        
        # Red fill for non-deductible items (only if not empty)
        non_deductible_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
        non_deductible_rule = FormulaRule(
            formula=['AND(NOT(F15),NOT(ISBLANK(F15)))'],
            fill=non_deductible_fill
        )
        ws.conditional_formatting.add('F15:F115', non_deductible_rule)
        
        # Red fill for total mismatch
        error_red_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
        total_rule = FormulaRule(
            formula=['ABS(B9-C9)>0.01'],
            fill=error_red_fill
        )
        ws.conditional_formatting.add('D9:D9', total_rule)
        
        # Add notes for validation errors
        amounts = receipt.get('amounts', {})
        total_excl = amounts.get('total_excl_vat', 0)
        vat = amounts.get('vat_amount', 0)
        total_incl = amounts.get('total_incl_vat', 0)
        
        # Check total validation
        if abs((total_excl + vat) - total_incl) > 0.01:
            ws['D9'] = 'שגיאה: סכום לא תואם'
            ws['D9'].font = Font(color="FF0000")
            
        # Check VAT percentage
        if total_excl > 0:
            vat_pct = (vat / total_excl) * 100
            if abs(vat_pct) > 0.1 and abs(vat_pct - 18) > 0.1:
                ws['D7'] = f'אזהרה: מע"מ {vat_pct:.1f}%'
                ws['D7'].font = Font(color="FF9900")
                
    def _map_document_type(self, doc_type: str) -> str:
        """Map English document type to Hebrew"""
        mapping = {
            'invoice': 'חשבונית',
            'receipt': 'קבלה',
            'invoice+receipt': 'חשבונית+קבלה'
        }
        return mapping.get(doc_type, doc_type)
        
    def _load_categories(self) -> List[str]:
        """Load categories from ICOUNT_CATEGORIES.md file"""
        categories = []
        
        try:
            with open(self.categories_file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Extract category names from first column of tables (more restrictive pattern)
            # Matches: | **category_name** | at the start of a line
            pattern = r'^\|\s*\*\*([^*]+)\*\*\s*\|'
            matches = re.findall(pattern, content, re.MULTILINE)
            
            # Filter out table headers
            skip_items = ['קטגוריה']
            categories = [m.strip() for m in matches if m.strip() not in skip_items]
            
        except Exception as e:
            logger.error(f"Error loading categories from {self.categories_file_path}: {e}")
            
        return categories