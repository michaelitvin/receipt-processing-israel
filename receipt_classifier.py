#!/usr/bin/env python3
"""
Receipt Classifier - Part 2
Classifies expenses with user interaction and generates final reports
With parallel processing for faster batch classification
"""

import os
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging
from decimal import Decimal
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time

import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ReceiptClassifier:
    """Classify and summarize receipt expenses with user interaction and parallel processing"""
    
    def __init__(self, api_key: Optional[str] = None, max_workers: int = 5, auto_mode: bool = False):
        """Initialize the classifier with Anthropic API key and parallel processing settings"""
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("Anthropic API key required. Set ANTHROPIC_API_KEY environment variable or pass as parameter")
        
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.max_workers = max_workers
        self.auto_mode = auto_mode
        self.processing_lock = Lock()
        
        # Use Claude Sonnet 4 model for faster, more cost-effective processing
        self.model = os.getenv('ANTHROPIC_MODEL', 'claude-3-5-sonnet-20241022')
        logger.info(f"Using model: {self.model}")
        
        # Define expense categories for Israeli tax reporting
        self.expense_categories = {
            'meals_entertainment': 'ארוחות ואירוח / Meals & Entertainment',
            'office_supplies': 'ציוד משרדי / Office Supplies',
            'travel_transport': 'נסיעות ותחבורה / Travel & Transport',
            'accommodation': 'לינה / Accommodation',
            'professional_services': 'שירותים מקצועיים / Professional Services',
            'equipment': 'ציוד / Equipment',
            'utilities': 'חשבונות משרד / Utilities',
            'insurance': 'ביטוח / Insurance',
            'marketing': 'שיווק ופרסום / Marketing',
            'education': 'השתלמויות / Education',
            'vehicle': 'רכב / Vehicle',
            'communication': 'תקשורת / Communication',
            'other': 'אחר / Other'
        }
        
        self.expense_types = {
            'business': '100% עסקי / Business',
            'personal': 'פרטי / Personal',
            'mixed': 'מעורב / Mixed'
        }
    
    def create_classification_prompt(self, receipt_data: Dict) -> str:
        """Create prompt for Claude to classify expenses"""
        return f"""Based on the extracted receipt data below, classify each expense for Israeli tax reporting.

Receipt Data:
{json.dumps(receipt_data, ensure_ascii=False, indent=2)}

Please analyze and return a JSON with the following structure:

{{
    "vendor_info": {{
        "name": "Cleaned vendor name",
        "tax_id": "Cleaned tax ID (ח.פ or עוסק מורשה)",
        "is_israeli_vendor": true/false,
        "vendor_type": "company/individual/unknown"
    }},
    
    "transaction_info": {{
        "date": "YYYY-MM-DD format",
        "amount": numeric total amount,
        "currency": "ILS/USD/EUR",
        "vat_amount": numeric VAT amount,
        "vat_rate": VAT percentage (usually 17 for Israel)
    }},
    
    "classification": {{
        "primary_category": "One of: {', '.join(self.expense_categories.keys())}",
        "expense_type": "business/personal/mixed",
        "business_percentage": 0-100 (percentage that is business-related),
        "confidence": "high/medium/low",
        "requires_clarification": true/false
    }},
    
    "line_items_classification": [
        {{
            "description": "Item description",
            "amount": numeric amount,
            "category": "expense category",
            "expense_type": "business/personal/mixed",
            "business_percentage": 0-100,
            "notes": "Any relevant notes"
        }}
    ],
    
    "questions_for_user": [
        // List questions if information is missing or unclear
        "Question 1 about missing/unclear information",
        "Question 2 if needed"
    ],
    
    "tax_notes": {{
        "deductible_amount": calculated deductible amount,
        "non_deductible_amount": calculated non-deductible amount,
        "special_considerations": "Any special tax considerations for this type of expense",
        "documentation_requirements": "What additional documentation might be needed"
    }},
    
    "ai_notes": "Any observations or recommendations about this receipt"
}}

Important considerations for Israeli tax law:
- Meals with clients: Usually 80% deductible
- Employee meals: Different rules apply
- Vehicle expenses: Depend on usage logs
- Home office: Requires proper documentation
- Foreign currency: Must note exchange rate
- Missing tax invoice: Note if receipt is not a proper tax invoice (חשבונית מס)

Be thorough but practical. If critical information is missing, include it in questions_for_user."""
    
    def classify_receipt(self, receipt_data: Dict) -> Dict:
        """Classify a single receipt using Claude"""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": self.create_classification_prompt(receipt_data)
                    }
                ]
            )
            
            classification = json.loads(response.content[0].text)
            return classification
            
        except Exception as e:
            logger.error(f"Error classifying receipt: {e}")
            return None
    
    def get_user_input(self, questions: List[str], receipt_info: str) -> Dict[str, str]:
        """Get user input for clarification questions"""
        print("\n" + "="*60)
        print(f"📋 CLARIFICATION NEEDED for: {receipt_info}")
        print("="*60)
        
        answers = {}
        for i, question in enumerate(questions, 1):
            print(f"\n❓ Question {i}: {question}")
            answer = input("Your answer (or press Enter to skip): ").strip()
            if answer:
                answers[f"question_{i}"] = answer
        
        return answers
    
    def classify_receipts_batch(self, receipt_data_list: List[Dict]) -> List[Dict]:
        """Classify multiple receipts in parallel"""
        classifications = [None] * len(receipt_data_list)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all classification tasks
            future_to_index = {
                executor.submit(self.classify_receipt, receipt_data): idx
                for idx, receipt_data in enumerate(receipt_data_list)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    classification = future.result()
                    classifications[idx] = classification
                    with self.processing_lock:
                        logger.info(f"Classified receipt {idx + 1}/{len(receipt_data_list)}")
                except Exception as e:
                    logger.error(f"Error in batch classification for receipt {idx}: {e}")
                    classifications[idx] = None
        
        return classifications
    
    def interactive_review(self, receipt_data: Dict, classification: Dict, idx: int) -> Dict:
        """Interactive review and modification of classification"""
        metadata = receipt_data.get('_metadata', {})
        
        print("\n" + "="*70)
        print(f"🧾 RECEIPT #{idx}: {metadata.get('file_name', 'Unknown')}")
        print("="*70)
        
        # Display classification
        vendor = classification.get('vendor_info', {})
        transaction = classification.get('transaction_info', {})
        class_info = classification.get('classification', {})
        
        print(f"\n📍 Vendor: {vendor.get('name', 'Unknown')}")
        print(f"   Tax ID: {vendor.get('tax_id', 'Not found')}")
        print(f"   Date: {transaction.get('date', 'Unknown')}")
        print(f"   Amount: {transaction.get('currency', 'ILS')} {transaction.get('amount', 0):.2f}")
        
        print(f"\n🏷️ Classification:")
        print(f"   Category: {self.expense_categories.get(class_info.get('primary_category', 'other'), 'Other')}")
        print(f"   Type: {self.expense_types.get(class_info.get('expense_type', 'business'), 'Business')}")
        print(f"   Business %: {class_info.get('business_percentage', 100)}%")
        print(f"   Confidence: {class_info.get('confidence', 'medium')}")
        
        # Show questions if any
        questions = classification.get('questions_for_user', [])
        user_answers = {}
        if questions:
            print(f"\n⚠️ Claude has {len(questions)} questions about this receipt")
            user_answers = self.get_user_input(questions, f"{vendor.get('name', 'Receipt')} - {transaction.get('date', '')}")
        
        # Show AI notes
        if classification.get('ai_notes'):
            print(f"\n💡 AI Notes: {classification['ai_notes']}")
        
        # Allow manual override (skip in auto mode)
        if self.auto_mode:
            choice = "1"  # Auto-accept
            logger.info(f"Auto-accepting classification for receipt #{idx}")
        else:
            print("\n" + "-"*60)
            print("Review Options:")
            print("1. Accept classification as-is")
            print("2. Modify business percentage")
            print("3. Change expense category")
            print("4. Change expense type (business/personal/mixed)")
            print("5. Add custom note")
            print("6. Mark as invalid/skip")
            
            choice = input("\nYour choice (1-6, default=1): ").strip() or "1"
        
        user_notes = ""
        skip_receipt = False
        
        if choice == "2":
            new_percentage = input(f"Enter business percentage (current: {class_info.get('business_percentage', 100)}%): ").strip()
            if new_percentage.isdigit():
                class_info['business_percentage'] = int(new_percentage)
                user_notes += f"User adjusted business percentage to {new_percentage}%. "
        
        elif choice == "3":
            print("\nAvailable categories:")
            for key, value in self.expense_categories.items():
                print(f"  {key}: {value}")
            new_category = input("Enter category code: ").strip().lower()
            if new_category in self.expense_categories:
                class_info['primary_category'] = new_category
                user_notes += f"User changed category to {new_category}. "
        
        elif choice == "4":
            print("\nExpense types: business, personal, mixed")
            new_type = input("Enter type: ").strip().lower()
            if new_type in ['business', 'personal', 'mixed']:
                class_info['expense_type'] = new_type
                user_notes += f"User changed type to {new_type}. "
        
        elif choice == "5":
            custom_note = input("Enter your note: ").strip()
            if custom_note:
                user_notes += custom_note
        
        elif choice == "6":
            skip_receipt = True
            user_notes = "User marked as invalid/skip"
        
        # Create final classification with user input
        final_classification = {
            'original_file': metadata.get('file_name', ''),
            'classification': classification,
            'user_answers': user_answers,
            'user_modifications': user_notes,
            'skip': skip_receipt,
            'reviewed_at': datetime.now().isoformat()
        }
        
        return final_classification
    
    def process_extracted_data(self, json_file: Path) -> Tuple[List[Dict], Dict]:
        """Process extracted data file with parallel classifications"""
        # Load extracted data
        with open(json_file, 'r', encoding='utf-8') as f:
            extracted_data = json.load(f)
        
        classified_receipts = []
        summary = {
            'total_receipts': len(extracted_data),
            'processed': 0,
            'skipped': 0,
            'total_business_amount': Decimal('0'),
            'total_personal_amount': Decimal('0'),
            'total_mixed_amount': Decimal('0'),
            'total_vat': Decimal('0'),
            'by_category': {},
            'by_month': {}
        }
        
        print("\n" + "="*70)
        print("🚀 STARTING RECEIPT CLASSIFICATION")
        print(f"📊 Total receipts to process: {len(extracted_data)}")
        print(f"⚡ Using {self.max_workers} parallel workers")
        if self.auto_mode:
            print("🤖 Running in AUTO mode - classifications will be auto-accepted")
        print("="*70)
        
        # Filter out failed extractions
        valid_receipts = []
        valid_indices = []
        for idx, receipt_data in enumerate(extracted_data):
            metadata = receipt_data.get('_metadata', {})
            if metadata.get('status') != 'failed':
                valid_receipts.append(receipt_data)
                valid_indices.append(idx)
            else:
                print(f"\n⏭️ Skipping failed extraction: {metadata.get('file_name', 'Unknown')}")
                summary['skipped'] += 1
        
        if valid_receipts:
            # Classify all valid receipts in parallel
            print(f"\n🔄 Classifying {len(valid_receipts)} receipts in parallel...")
            start_time = time.time()
            classifications = self.classify_receipts_batch(valid_receipts)
            elapsed = time.time() - start_time
            print(f"✅ Classification completed in {elapsed:.2f} seconds")
            print(f"   Average: {elapsed/len(valid_receipts):.2f} seconds per receipt")
            
            # Interactive review for each classification
            for i, (receipt_data, classification, original_idx) in enumerate(zip(valid_receipts, classifications, valid_indices)):
                idx = original_idx + 1
                metadata = receipt_data.get('_metadata', {})
                
                if not classification:
                    print(f"\n❌ Classification failed for: {metadata.get('file_name', 'Unknown')}")
                    summary['skipped'] += 1
                    continue
                
                print(f"\n[{i+1}/{len(valid_receipts)}] Reviewing: {metadata.get('file_name', 'Unknown')}")
                
                # Get user questions answers if not in auto mode
                questions = classification.get('questions_for_user', [])
                user_answers = {}
                if questions and not self.auto_mode:
                    user_answers = self.get_user_input(questions, 
                        f"{classification.get('vendor_info', {}).get('name', 'Receipt')} - {classification.get('transaction_info', {}).get('date', '')}")
                
                # Interactive review (auto-accept if in auto mode)
                final_classification = self.interactive_review(receipt_data, classification, idx)
                
                if not final_classification['skip']:
                    classified_receipts.append(final_classification)
                    summary['processed'] += 1
                    
                    # Update summary statistics
                    trans_info = classification.get('transaction_info', {})
                    class_info = classification.get('classification', {})
                    
                    amount = Decimal(str(trans_info.get('amount', 0)))
                    business_pct = Decimal(str(class_info.get('business_percentage', 0))) / 100
                    
                    if class_info.get('expense_type') == 'business':
                        summary['total_business_amount'] += amount
                    elif class_info.get('expense_type') == 'personal':
                        summary['total_personal_amount'] += amount
                    else:  # mixed
                        business_amount = amount * business_pct
                        personal_amount = amount * (1 - business_pct)
                        summary['total_business_amount'] += business_amount
                        summary['total_personal_amount'] += personal_amount
                        summary['total_mixed_amount'] += amount
                    
                    summary['total_vat'] += Decimal(str(trans_info.get('vat_amount', 0)))
                    
                    # Track by category
                    category = class_info.get('primary_category', 'other')
                    if category not in summary['by_category']:
                        summary['by_category'][category] = Decimal('0')
                    summary['by_category'][category] += amount
                    
                    # Track by month
                    date_str = trans_info.get('date', '')
                    if date_str:
                        try:
                            month_key = date_str[:7]  # YYYY-MM
                            if month_key not in summary['by_month']:
                                summary['by_month'][month_key] = Decimal('0')
                            summary['by_month'][month_key] += amount
                        except:
                            pass
                else:
                    summary['skipped'] += 1
        
        return classified_receipts, summary
    
    def generate_csv_report(self, classified_receipts: List[Dict], output_file: Path) -> Path:
        """Generate CSV report for tax reporting"""
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as csvfile:
            fieldnames = [
                'receipt_number', 'file_name', 'date', 'vendor_name', 'vendor_tax_id',
                'category', 'category_hebrew', 'expense_type', 'business_percentage',
                'total_amount', 'vat_amount', 'currency', 'deductible_amount',
                'non_deductible_amount', 'user_notes', 'ai_notes', 'requires_documentation'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for idx, receipt in enumerate(classified_receipts, 1):
                classification = receipt['classification']
                vendor = classification.get('vendor_info', {})
                transaction = classification.get('transaction_info', {})
                class_info = classification.get('classification', {})
                tax_notes = classification.get('tax_notes', {})
                
                category_key = class_info.get('primary_category', 'other')
                category_display = self.expense_categories.get(category_key, 'Other')
                category_parts = category_display.split(' / ')
                
                row = {
                    'receipt_number': idx,
                    'file_name': receipt.get('original_file', ''),
                    'date': transaction.get('date', ''),
                    'vendor_name': vendor.get('name', ''),
                    'vendor_tax_id': vendor.get('tax_id', ''),
                    'category': category_parts[1] if len(category_parts) > 1 else category_key,
                    'category_hebrew': category_parts[0] if category_parts else '',
                    'expense_type': class_info.get('expense_type', ''),
                    'business_percentage': class_info.get('business_percentage', 100),
                    'total_amount': transaction.get('amount', 0),
                    'vat_amount': transaction.get('vat_amount', 0),
                    'currency': transaction.get('currency', 'ILS'),
                    'deductible_amount': tax_notes.get('deductible_amount', 0),
                    'non_deductible_amount': tax_notes.get('non_deductible_amount', 0),
                    'user_notes': receipt.get('user_modifications', ''),
                    'ai_notes': classification.get('ai_notes', ''),
                    'requires_documentation': tax_notes.get('documentation_requirements', '')
                }
                
                writer.writerow(row)
        
        return output_file
    
    def save_results(self, classified_receipts: List[Dict], summary: Dict, output_dir: Path) -> Tuple[Path, Path, Path]:
        """Save all results"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Save classified data
        classified_file = output_dir / f"classified_receipts_{timestamp}.json"
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(classified_receipts, f, ensure_ascii=False, indent=2, default=str)
        
        # Save summary
        summary_file = output_dir / f"classification_summary_{timestamp}.json"
        
        # Convert Decimal to float for JSON serialization
        summary_serializable = {
            k: float(v) if isinstance(v, Decimal) else v
            for k, v in summary.items()
        }
        summary_serializable['by_category'] = {
            k: float(v) for k, v in summary['by_category'].items()
        }
        summary_serializable['by_month'] = {
            k: float(v) for k, v in summary['by_month'].items()
        }
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_serializable, f, ensure_ascii=False, indent=2)
        
        # Generate CSV report
        csv_file = output_dir / f"tax_report_{timestamp}.csv"
        self.generate_csv_report(classified_receipts, csv_file)
        
        return classified_file, summary_file, csv_file


def main():
    """Main function to run the receipt classifier"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Classify and summarize receipts - Part 2')
    parser.add_argument('extracted_json', type=str, 
                       help='Path to extracted_data JSON file from Part 1')
    parser.add_argument('--output', type=str, 
                       help='Output folder for results (default: receipts_classified)',
                       default='receipts_classified')
    parser.add_argument('--api-key', type=str, 
                       help='Anthropic API key (or set ANTHROPIC_API_KEY env var)',
                       default=None)
    parser.add_argument('--auto', action='store_true',
                       help='Auto-accept all classifications without review')
    parser.add_argument('--workers', type=int,
                       help='Number of parallel workers for classification (default: 5)',
                       default=5)
    parser.add_argument('--model', type=str,
                       help='Model to use (default: claude-3-5-sonnet-20241022)',
                       default=None)
    
    args = parser.parse_args()
    
    # Validate input file
    json_file = Path(args.extracted_json)
    if not json_file.exists():
        logger.error(f"Input file not found: {json_file}")
        return 1
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Set model if specified
    if args.model:
        os.environ['ANTHROPIC_MODEL'] = args.model
    
    # Initialize classifier with parallel processing
    try:
        classifier = ReceiptClassifier(
            api_key=args.api_key, 
            max_workers=args.workers,
            auto_mode=args.auto
        )
    except ValueError as e:
        logger.error(str(e))
        return 1
    
    # Process receipts
    logger.info(f"Starting classification of receipts from: {json_file}")
    classified_receipts, summary = classifier.process_extracted_data(json_file)
    
    # Save results
    classified_file, summary_file, csv_file = classifier.save_results(
        classified_receipts, summary, output_dir
    )
    
    # Print final summary
    print("\n" + "="*70)
    print("✅ CLASSIFICATION COMPLETE")
    print("="*70)
    print(f"Total receipts: {summary['total_receipts']}")
    print(f"Successfully processed: {summary['processed']}")
    print(f"Skipped: {summary['skipped']}")
    print(f"Model used: {classifier.model}")
    print(f"Parallel workers: {args.workers}")
    if args.auto:
        print("Mode: AUTO (all classifications auto-accepted)")
    print(f"\n💰 Financial Summary:")
    print(f"  Business expenses: ₪{float(summary['total_business_amount']):,.2f}")
    print(f"  Personal expenses: ₪{float(summary['total_personal_amount']):,.2f}")
    print(f"  Mixed expenses: ₪{float(summary['total_mixed_amount']):,.2f}")
    print(f"  Total VAT: ₪{float(summary['total_vat']):,.2f}")
    
    if summary['by_category']:
        print(f"\n📊 By Category:")
        for category, amount in summary['by_category'].items():
            category_name = classifier.expense_categories.get(category, category)
            print(f"  {category_name}: ₪{float(amount):,.2f}")
    
    print(f"\n📁 Output Files:")
    print(f"  📄 Classified data: {classified_file}")
    print(f"  📊 Summary: {summary_file}")
    print(f"  📑 Tax report CSV: {csv_file}")
    print(f"\n✨ The CSV file is ready for import into your accounting system!")
    
    return 0


if __name__ == "__main__":
    exit(main())
