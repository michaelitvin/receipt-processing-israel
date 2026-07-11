#!/usr/bin/env python3
# Copyright (c) 2025 Michael Litvin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# IMPORTANT: This processes financial data. Verify all data for accuracy.
# The author bears NO responsibility for data errors or tax mistakes.
"""
Receipt Data Extractor - Stage 1
Extracts and classifies receipt data, generates Excel files for review
"""

import asyncio
import argparse
import logging
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Add shared modules to path
sys.path.append(str(Path(__file__).parent))

from shared.openai_client import OpenAIClient, ProcessedReceipt, estimate_cost_usd
from shared.image_handler import ImageHandler
from shared.excel_generator import ExcelGenerator
from shared.logger import ReceiptLogger

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)




class ReceiptExtractor:
    """Main receipt extraction orchestrator"""
    
    def __init__(
        self,
        api_key: str,
        output_dir: Path,
        model: str,
        max_concurrent: int = 100,
        receipts_per_file: int = 100,
        period: Optional[str] = None
    ):
        """Initialize the extractor"""
        self.api_key = api_key
        self.output_dir = output_dir
        self.max_concurrent = max_concurrent
        self.receipts_per_file = receipts_per_file
        self.model = model
        self.period_months = self._parse_period(period) if period else None
        
        # Load extraction prompt directory
        self.extraction_prompt_dir = Path(__file__).parent / 'docs' / 'extraction-prompt'
        
        # Initialize components
        self.openai_client = OpenAIClient(api_key, model)
        # Pass the categories file to ExcelGenerator for category validation (dropdown)
        categories_file = self.extraction_prompt_dir / '001-ICOUNT_CATEGORIES.md'
        self.excel_generator = ExcelGenerator(categories_file)
        
        # Setup output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir = self.output_dir / 'images'
        self.images_dir.mkdir(exist_ok=True)
        self.logs_dir = self.output_dir / 'llm_logs'
        self.logger = ReceiptLogger(self.logs_dir)
        
    async def process_receipts(self, receipts_dir: Path) -> Dict[str, Any]:
        """Process all receipts in directory"""
        start_time = datetime.now()
        
        # Find all receipt files
        receipt_files = self._find_receipt_files(receipts_dir)
        logger.info(f"Found {len(receipt_files)} receipt files to process")
        
        if not receipt_files:
            logger.warning("No receipt files found")
            return {'status': 'error', 'message': 'No receipt files found'}
            
        # Process receipts in parallel
        results = await self._process_receipts_parallel(receipt_files)

        # Flag suspicious extractions for manual review
        self._add_review_warnings(results)

        # Generate Excel files in batches
        excel_files = self._generate_excel_batches(results)
        
        # Generate processing summary
        end_time = datetime.now()
        summary = self._generate_summary(results, excel_files, start_time, end_time)
        
        # Log processing stats
        self.logger.log_processing_stats(summary)
        
        return summary
        
    @staticmethod
    def _parse_period(period: str) -> List[str]:
        """Parse YYYY-MM into the canonical bi-monthly VAT period containing it.

        Israeli VAT periods are Jan-Feb, Mar-Apr, May-Jun, Jul-Aug, Sep-Oct, Nov-Dec.
        Returns the two months as ['YYYY-MM', 'YYYY-MM'] prefixes for date matching.
        """
        try:
            year, month = map(int, period.split('-'))
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            raise ValueError(f"Invalid period {period!r}, expected YYYY-MM")
        start = month if month % 2 == 1 else month - 1
        return [f"{year:04d}-{start:02d}", f"{year:04d}-{start + 1:02d}"]

    def _add_review_warnings(self, results: List[Dict[str, Any]]) -> None:
        """Attach review warnings to successful results with suspicious data"""
        for result in results:
            if result.get('status') != 'success':
                continue
            info = result.get('receipt_info', {})
            amounts = result.get('amounts', {})
            warnings = []

            if not amounts.get('total_incl_vat'):
                warnings.append('סה"כ כולל מע"מ הוא 0 - ייתכן שהחילוץ נכשל')
            if not info.get('number'):
                warnings.append('חסר מספר קבלה')
            if not info.get('vendor_id'):
                warnings.append('חסר תז/חפ הספק')
            date = info.get('date', '')
            if not date:
                warnings.append('חסר תאריך')
            elif self.period_months and date[:7] not in self.period_months:
                warnings.append(f'תאריך {date} מחוץ לתקופת הדיווח ({self.period_months[0]} עד {self.period_months[1]})')

            if warnings:
                result['review_warnings'] = warnings
                logger.warning(f"Review needed for {Path(result.get('file_path', '?')).name}: {'; '.join(warnings)}")

    def _find_receipt_files(self, receipts_dir: Path) -> List[Path]:
        """Find all supported receipt files in directory"""
        receipt_files = []
        
        for file_path in receipts_dir.iterdir():
            if file_path.is_file() and ImageHandler.is_supported_file(file_path):
                receipt_files.append(file_path)
                
        # Sort files for consistent ordering
        receipt_files.sort()
        
        return receipt_files
        
    async def _process_receipts_parallel(self, receipt_files: List[Path]) -> List[Dict[str, Any]]:
        """Process receipts in parallel with concurrency control"""
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_with_semaphore(receipt_path: Path) -> Dict[str, Any]:
            async with semaphore:
                return await self._process_single_receipt(receipt_path)
                
        tasks = [process_with_semaphore(receipt) for receipt in receipt_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle exceptions
        processed_results = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Error processing {receipt_files[idx]}: {result}")
                processed_results.append({
                    'status': 'error',
                    'file_path': str(receipt_files[idx]),
                    'error': str(result)
                })
            else:
                processed_results.append(result)
                
        return processed_results
        
    async def _process_single_receipt(self, receipt_path: Path) -> Dict[str, Any]:
        """Process a single receipt file"""
        logger.info(f"Processing {receipt_path.name}")
        
        try:
            # Process image/PDF
            images = ImageHandler.process_file(receipt_path)
            
            # For PDFs with multiple pages, process first page only
            if images:
                image = images[0]
                
                # Save processed image for Excel
                image_output_path = self.images_dir / receipt_path.with_suffix('.jpg').name
                ImageHandler.save_image_for_excel(image, image_output_path)
                
                # Extract data using OpenAI
                request_data = {
                    'file': str(receipt_path),
                    'extraction_prompt_dir': str(self.extraction_prompt_dir),
                    'timestamp': datetime.now().isoformat()
                }
                
                try:
                    result = await self.openai_client.extract_receipt_data(
                        receipt_path,
                        self.extraction_prompt_dir
                    )
                    
                    # Log successful interaction with response format
                    response_format = result.get('response_format_used')
                    self.logger.log_llm_interaction(
                        str(receipt_path),
                        request_data,
                        result,
                        response_format=response_format
                    )
                    
                    # Add processing metadata
                    result['status'] = 'success'
                    result['file_path'] = str(receipt_path)
                    result['image_path'] = str(image_output_path)
                    
                    logger.info(f"Successfully extracted data from {receipt_path.name}")
                    
                    return result
                    
                except Exception as e:
                    # Log failed interaction (no response format available on error)
                    self.logger.log_llm_interaction(
                        str(receipt_path),
                        request_data,
                        None,
                        error=e,
                        response_format=None
                    )
                    raise
                    
        except Exception as e:
            logger.error(f"Error processing {receipt_path}: {e}")
            return {
                'status': 'error',
                'file_path': str(receipt_path),
                'error': str(e)
            }
            
    def _generate_excel_batches(self, results: List[Dict[str, Any]]) -> List[Path]:
        """Generate Excel files in batches for successful and failed receipts"""
        excel_files = []
        
        # Filter successful and failed results
        successful_results = [r for r in results if r.get('status') == 'success']
        failed_results = [r for r in results if r.get('status') == 'error']
        
        # Create batches for successful results
        for i in range(0, len(successful_results), self.receipts_per_file):
            batch = successful_results[i:i + self.receipts_per_file]
            
            # Generate Excel file for batch
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            batch_num = (i // self.receipts_per_file) + 1
            excel_path = self.output_dir / f'receipts_batch_{batch_num:03d}_{timestamp}.xlsx'
            
            try:
                wb = self.excel_generator.create_batch_workbook(batch, self.images_dir)
                wb.save(excel_path)
                excel_files.append(excel_path)
                logger.info(f"Generated Excel file: {excel_path}")
                
            except Exception as e:
                logger.error(f"Error generating Excel batch {batch_num}: {e}")
                
        # Create batches for failed results with empty data
        for i in range(0, len(failed_results), self.receipts_per_file):
            batch = failed_results[i:i + self.receipts_per_file]
            
            # Convert failed results to empty receipt format
            empty_batch = []
            for failed_result in batch:
                empty_receipt = {
                    'status': 'success',  # Mark as success for Excel generation
                    'file_path': failed_result.get('file_path', ''),
                    'image_path': failed_result.get('file_path', ''),  # Use original file path
                    'receipt_info': {
                        'number': '',
                        'vendor': '',
                        'date': '',
                        'document_type': '',
                        'original_file': failed_result.get('file_path', '')
                    },
                    'amounts': {
                        'total_excl_vat': 0,
                        'vat_amount': 0,
                        'total_incl_vat': 0
                    },
                    'line_items': [],
                    'classification': {
                        'category': '',
                        'confidence': 0
                    },
                    '_processing_error': failed_result.get('error', 'Unknown error')
                }
                empty_batch.append(empty_receipt)
            
            # Generate Excel file for failed batch
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            batch_num = (i // self.receipts_per_file) + 1
            excel_path = self.output_dir / f'receipts_batch_failed_{batch_num:03d}_{timestamp}.xlsx'
            
            try:
                wb = self.excel_generator.create_batch_workbook(empty_batch, self.images_dir)
                wb.save(excel_path)
                excel_files.append(excel_path)
                logger.info(f"Generated failed receipts Excel file: {excel_path}")
                
            except Exception as e:
                logger.error(f"Error generating failed Excel batch {batch_num}: {e}")
                
        return excel_files
        
    def _generate_summary(
        self,
        results: List[Dict[str, Any]],
        excel_files: List[Path],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, Any]:
        """Generate processing summary"""
        
        successful = len([r for r in results if r.get('status') == 'success'])
        failed = len([r for r in results if r.get('status') == 'error'])
        
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_files': len(results),
            'successful': successful,
            'failed': failed,
            'processing_time_seconds': (end_time - start_time).total_seconds(),
            'excel_files_generated': len(excel_files),
            'excel_files': [str(f) for f in excel_files],
            'configuration': {
                'max_concurrent_requests': self.max_concurrent,
                'receipts_per_file': self.receipts_per_file,
                'extraction_prompt_dir': str(self.extraction_prompt_dir)
            }
        }
        
        # Aggregate token usage and estimated cost across all calls
        totals = {'input_tokens': 0, 'cached_input_tokens': 0,
                  'output_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 0}
        calls_with_usage = 0
        for r in results:
            usage = (r.get('api_metadata') or {}).get('usage')
            if usage:
                calls_with_usage += 1
                for key in totals:
                    totals[key] += usage.get(key, 0) or 0
        if calls_with_usage:
            summary['token_usage'] = totals
            cost = estimate_cost_usd(self.model, totals)
            if cost is not None:
                summary['estimated_cost_usd'] = round(cost, 4)

        # Add failed files details
        if failed > 0:
            summary['failed_files'] = [
                {
                    'file': r.get('file_path'),
                    'error': r.get('error')
                }
                for r in results if r.get('status') == 'error'
            ]

        return summary


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Extract and classify receipt data into Excel files'
    )
    parser.add_argument(
        'receipts_dir',
        type=Path,
        help='Directory containing receipt images/PDFs'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('./receipts_extracted'),
        help='Output directory (default: ./receipts_extracted)'
    )
    parser.add_argument(
        '--concurrent',
        type=int,
        default=int(os.getenv('MAX_CONCURRENT_REQUESTS', 100)),
        help='Max concurrent API requests (default: 100)'
    )
    parser.add_argument(
        '--receipts-per-file',
        type=int,
        default=int(os.getenv('RECEIPTS_PER_FILE', 100)),
        help='Receipts per Excel file (default: 100)'
    )
    parser.add_argument(
        '--api-key',
        type=str,
        default=os.getenv('OPENAI_API_KEY'),
        help='OpenAI API key (overrides .env)'
    )
    parser.add_argument(
        '--model',
        type=str,
        default=os.getenv('MODEL', 'gpt-5-mini'),
        help='OpenAI model to use (default: gpt-5-mini)'
    )
    parser.add_argument(
        '--period',
        type=str,
        default=None,
        help='Reporting period as YYYY-MM; receipts dated outside the bi-monthly '
             'VAT period containing this month are flagged for review'
    )

    args = parser.parse_args()
    
    # Validate inputs
    if not args.receipts_dir.exists():
        logger.error(f"Receipts directory not found: {args.receipts_dir}")
        sys.exit(1)
        
    if not args.api_key:
        logger.error("OpenAI API key not provided. Set OPENAI_API_KEY in .env or use --api-key")
        sys.exit(1)

    if args.period:
        try:
            ReceiptExtractor._parse_period(args.period)
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)
        
    # Create timestamp-based output directory
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = args.output / f'extraction_{timestamp}'
    
    # Initialize extractor
    extractor = ReceiptExtractor(
        api_key=args.api_key,
        output_dir=output_dir,
        model=args.model,
        max_concurrent=args.concurrent,
        receipts_per_file=args.receipts_per_file,
        period=args.period
    )
    
    # Process receipts
    logger.info(f"Starting receipt extraction from {args.receipts_dir}")
    summary = await extractor.process_receipts(args.receipts_dir)
    
    # Print summary
    print("\n" + "="*60)
    print("PROCESSING COMPLETE")
    print("="*60)
    print(f"Total receipts: {summary['total_files']}")
    print(f"Successful: {summary['successful']}")
    print(f"Failed: {summary['failed']}")
    print(f"Processing time: {summary['processing_time_seconds']:.1f} seconds")
    print(f"Excel files generated: {summary['excel_files_generated']}")

    if 'token_usage' in summary:
        usage = summary['token_usage']
        print(f"Tokens: {usage['input_tokens']:,} in "
              f"({usage['cached_input_tokens']:,} cached), "
              f"{usage['output_tokens']:,} out "
              f"({usage['reasoning_tokens']:,} reasoning)")
        if 'estimated_cost_usd' in summary:
            print(f"Estimated cost: ${summary['estimated_cost_usd']:.4f}")
    
    if summary['excel_files']:
        print("\nGenerated files:")
        for excel_file in summary['excel_files']:
            print(f"  - {excel_file}")
            
    if summary.get('failed_files'):
        print("\nFailed files:")
        for failed in summary['failed_files']:
            print(f"  - {failed['file']}: {failed['error']}")
            
    print(f"\nOutput directory: {output_dir}")
    
    print(f"Summary saved to: {output_dir / 'llm_logs' / 'processing_stats.yaml'}")


if __name__ == '__main__':
    asyncio.run(main())