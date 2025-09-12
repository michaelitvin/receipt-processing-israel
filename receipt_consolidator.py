#!/usr/bin/env python3
"""
Receipt Data Consolidator - Stage 2
Processes reviewed Excel files and generates iCount-ready CSV import files
"""

import argparse
import logging
import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import sys

# Add shared modules to path
sys.path.append(str(Path(__file__).parent))

from shared.logger import ReceiptLogger

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ReceiptConsolidator:
    """Consolidates reviewed Excel files into iCount import format"""
    
    def __init__(self, output_dir: Path):
        """Initialize consolidator"""
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        self.logs_dir = self.output_dir / 'consolidation_logs'
        self.logger = ReceiptLogger(self.logs_dir)
        
    def process_excel_files(self, excel_files: List[Path]) -> Dict[str, Any]:
        """Process multiple Excel files and generate consolidated output"""
        start_time = datetime.now()
        
        logger.info(f"Processing {len(excel_files)} Excel files for consolidation")
        
        all_receipts = []
        processing_errors = []
        
        # Process each Excel file
        for excel_file in excel_files:
            try:
                receipts = self._extract_receipts_from_excel(excel_file)
                all_receipts.extend(receipts)
                logger.info(f"Extracted {len(receipts)} receipts from {excel_file.name}")
                
            except Exception as e:
                logger.error(f"Error processing {excel_file}: {e}")
                processing_errors.append({
                    'file': str(excel_file),
                    'error': str(e)
                })
                
        if not all_receipts:
            logger.warning("No receipts extracted from Excel files")
            return {
                'status': 'error',
                'message': 'No receipts found in Excel files',
                'errors': processing_errors
            }
            
        # Generate iCount CSV
        csv_file = self._generate_icount_csv(all_receipts)
        
        # Generate summary
        end_time = datetime.now()
        summary = self._generate_summary(all_receipts, csv_file, processing_errors, start_time, end_time)
        
        # Log consolidation stats
        self.logger.log_processing_stats(summary)
        
        return summary
        
    def _extract_receipts_from_excel(self, excel_file: Path) -> List[Dict[str, Any]]:
        """Extract receipt data from a single Excel file"""
        receipts = []
        
        # Read all worksheets
        excel_data = pd.read_excel(excel_file, sheet_name=None, header=None)
        
        for sheet_name, df in excel_data.items():
            try:
                receipt = self._parse_worksheet(df, sheet_name, excel_file)
                if receipt:
                    receipts.append(receipt)
            except Exception as e:
                logger.error(f"Error parsing worksheet {sheet_name} in {excel_file}: {e}")
                
        return receipts
        
    def _parse_worksheet(self, df: pd.DataFrame, sheet_name: str, excel_file: Path) -> Optional[Dict[str, Any]]:
        """Parse a single worksheet into receipt data"""
        try:
            # Extract header information (rows 1-10)
            receipt_data = {}
            
            # Map Hebrew headers to field names
            header_mapping = {
                'מספר קבלה': 'receipt_number',
                'ספק': 'vendor',
                'תאריך': 'date', 
                'סוג מסמך': 'document_type',
                'סה"כ ללא מע"מ': 'total_excl_vat',
                'מע"מ': 'vat_amount',
                'סה"כ כולל מע"מ': 'total_incl_vat',
                'קטגוריה': 'category'
            }
            
            # Extract header fields (assuming they're in rows 1-10, column A=field, column B=value)
            for idx, row in df.iloc[:10].iterrows():
                if pd.notna(row.iloc[0]) and pd.notna(row.iloc[1]):
                    field_name = str(row.iloc[0]).strip()
                    field_value = row.iloc[1]
                    
                    if field_name in header_mapping:
                        receipt_data[header_mapping[field_name]] = field_value
                        
            # Validate required fields
            required_fields = ['receipt_number', 'vendor', 'date', 'total_incl_vat', 'category']
            missing_fields = [f for f in required_fields if not receipt_data.get(f)]
            
            if missing_fields:
                logger.warning(f"Missing required fields in {sheet_name}: {missing_fields}")
                return None
                
            # Extract line items (starting from row 14)
            line_items = []
            line_item_start = 14
            
            if len(df) > line_item_start:
                for idx, row in df.iloc[line_item_start:].iterrows():
                    # Check if row has data (description not empty)
                    if pd.notna(row.iloc[0]) and str(row.iloc[0]).strip():
                        line_item = {
                            'description': str(row.iloc[0]).strip(),
                            'amount_excl_vat': self._safe_float(row.iloc[1] if len(row) > 1 else 0),
                            'vat': self._safe_float(row.iloc[2] if len(row) > 2 else 0),
                            'total': self._safe_float(row.iloc[4] if len(row) > 4 else 0),
                            'deductible': self._safe_bool(row.iloc[5] if len(row) > 5 else True)
                        }
                        line_items.append(line_item)
                        
            # Add metadata
            receipt_data['line_items'] = line_items
            receipt_data['source_file'] = str(excel_file)
            receipt_data['worksheet'] = sheet_name
            receipt_data['processing_date'] = datetime.now().isoformat()
            
            return receipt_data
            
        except Exception as e:
            logger.error(f"Error parsing worksheet {sheet_name}: {e}")
            return None
            
    def _generate_icount_csv(self, receipts: List[Dict[str, Any]]) -> Path:
        """Generate CSV file in iCount import format"""
        
        # Prepare data for CSV
        csv_rows = []
        
        for receipt in receipts:
            # Handle line items - create separate row for each line item
            line_items = receipt.get('line_items', [])
            
            if not line_items:
                # If no line items, create single row with full amount
                csv_rows.append(self._create_csv_row(receipt, None))
            else:
                # Create row for each line item
                for line_item in line_items:
                    if line_item.get('deductible', True):  # Only include deductible items
                        csv_rows.append(self._create_csv_row(receipt, line_item))
                        
        # Create DataFrame
        df = pd.DataFrame(csv_rows)
        
        # Generate CSV file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_file = self.output_dir / f'icount_import_{timestamp}.csv'
        
        # Save with UTF-8 encoding for Hebrew support
        df.to_csv(csv_file, index=False, encoding='utf-8-sig')
        
        logger.info(f"Generated iCount CSV: {csv_file}")
        return csv_file
        
    def _create_csv_row(self, receipt: Dict[str, Any], line_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Create a single CSV row for iCount import"""
        
        if line_item:
            description = line_item['description']
            amount = line_item['total']
        else:
            description = f"Receipt from {receipt.get('vendor', 'Unknown')}"
            amount = receipt.get('total_incl_vat', 0)
            
        return {
            'תאריך': self._format_date(receipt.get('date', '')),
            'ספק': receipt.get('vendor', ''),
            'תיאור': description,
            'סכום': amount,
            'קטגוריה': receipt.get('category', ''),
            'מספר מסמך': receipt.get('receipt_number', ''),
            'סוג מסמך': receipt.get('document_type', ''),
            'קובץ מקור': receipt.get('source_file', '')
        }
        
    def _format_date(self, date_value: Any) -> str:
        """Format date for iCount import"""
        if pd.isna(date_value):
            return ''
            
        try:
            # Try to parse as datetime
            if isinstance(date_value, str):
                # Handle different date formats
                for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y']:
                    try:
                        dt = datetime.strptime(date_value, fmt)
                        return dt.strftime('%d/%m/%Y')
                    except ValueError:
                        continue
                        
            elif hasattr(date_value, 'strftime'):
                return date_value.strftime('%d/%m/%Y')
                
        except Exception as e:
            logger.warning(f"Error formatting date {date_value}: {e}")
            
        return str(date_value)
        
    def _safe_float(self, value: Any) -> float:
        """Safely convert value to float"""
        if pd.isna(value):
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
            
    def _safe_bool(self, value: Any) -> bool:
        """Safely convert value to boolean"""
        if pd.isna(value):
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ['true', '1', 'yes', 'כן']
        try:
            return bool(int(value))
        except (ValueError, TypeError):
            return True
            
    def _generate_summary(
        self,
        receipts: List[Dict[str, Any]],
        csv_file: Path,
        errors: List[Dict[str, str]],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Generate processing summary"""
        
        total_amount = sum(receipt.get('total_incl_vat', 0) for receipt in receipts)
        
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_receipts': len(receipts),
            'total_amount': total_amount,
            'csv_file': str(csv_file),
            'processing_time_seconds': (end_time - start_time).total_seconds(),
            'errors': errors
        }
        
        # Category breakdown
        categories = {}
        for receipt in receipts:
            category = receipt.get('category', 'Unknown')
            categories[category] = categories.get(category, 0) + 1
            
        summary['category_breakdown'] = categories
        
        return summary


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Consolidate reviewed Excel files into iCount import format'
    )
    parser.add_argument(
        'excel_files',
        nargs='+',
        type=Path,
        help='Excel files to consolidate'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('./receipts_consolidated'),
        help='Output directory (default: ./receipts_consolidated)'
    )
    
    args = parser.parse_args()
    
    # Validate input files
    valid_files = []
    for excel_file in args.excel_files:
        if excel_file.exists() and excel_file.suffix.lower() in ['.xlsx', '.xls']:
            valid_files.append(excel_file)
        else:
            logger.warning(f"Skipping invalid file: {excel_file}")
            
    if not valid_files:
        logger.error("No valid Excel files found")
        sys.exit(1)
        
    # Create timestamp-based output directory
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = args.output / f'consolidation_{timestamp}'
    
    # Initialize consolidator
    consolidator = ReceiptConsolidator(output_dir)
    
    # Process files
    logger.info(f"Starting consolidation of {len(valid_files)} Excel files")
    summary = consolidator.process_excel_files(valid_files)
    
    # Print summary
    print("\n" + "="*60)
    print("CONSOLIDATION COMPLETE")
    print("="*60)
    print(f"Total receipts: {summary.get('total_receipts', 0)}")
    print(f"Total amount: ₪{summary.get('total_amount', 0):,.2f}")
    print(f"Processing time: {summary.get('processing_time_seconds', 0):.1f} seconds")
    
    if summary.get('csv_file'):
        print(f"\nGenerated file: {summary['csv_file']}")
        
    if summary.get('category_breakdown'):
        print("\nCategory breakdown:")
        for category, count in summary['category_breakdown'].items():
            print(f"  {category}: {count} receipts")
            
    if summary.get('errors'):
        print(f"\nErrors encountered: {len(summary['errors'])}")
        for error in summary['errors']:
            print(f"  - {error['file']}: {error['error']}")
            
    print(f"\nOutput directory: {output_dir}")
    
    # Save summary to JSON
    summary_path = output_dir / f'consolidation_summary_{timestamp}.json'
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        
    print(f"Summary saved to: {summary_path}")


if __name__ == '__main__':
    main()