#!/usr/bin/env python3
"""
Receipt Data Extractor - Part 1
Extracts raw data from receipts and creates an HTML review interface
With parallel processing, comprehensive logging, and error handling
"""

import os
import json
import base64
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time
import traceback

import anthropic
from pdf2image import convert_from_path
from PIL import Image
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ReceiptExtractor:
    """Extract raw data from receipts using Anthropic Claude API with parallel processing"""
    
    def __init__(self, api_key: Optional[str] = None, max_workers: int = 5, log_dir: Optional[Path] = None):
        """Initialize the extractor with Anthropic API key and parallel processing settings"""
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("Anthropic API key required. Set ANTHROPIC_API_KEY environment variable or pass as parameter")
        
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.supported_image_formats = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        self.supported_pdf_format = '.pdf'
        self.max_workers = max_workers
        self.processing_lock = Lock()
        
        # Use Claude Sonnet 4 model for faster, more cost-effective processing
        self.model = os.getenv('ANTHROPIC_MODEL', 'claude-3-5-sonnet-20241022')
        logger.info(f"Using model: {self.model}")
        
        # Setup logging directory
        self.log_dir = log_dir
        self.attempt_count = {}  # Track retry attempts
    
    def setup_log_dir(self, output_dir: Path):
        """Setup the logging directory"""
        if not self.log_dir:
            self.log_dir = output_dir / "llm_logs"
        
        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LLM call logs will be saved to: {self.log_dir}")
    
    def get_media_type(self, file_suffix: str) -> str:
        """Get the correct media type for the image format"""
        # Normalize the suffix
        suffix = file_suffix.lower().strip('.')
        
        # Map file extensions to correct media types
        media_type_map = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        
        return media_type_map.get(suffix, 'image/jpeg')  # Default to jpeg
    
    def log_llm_call(self, file_path: Path, request_data: Dict, response_data: Optional[Dict], error: Optional[Exception] = None, attempt: int = 1):
        """Log LLM API calls for debugging and auditing"""
        if not self.log_dir:
            return
        
        # Ensure log directory exists
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        log_filename = f"llm_call_{file_path.stem}_attempt{attempt}_{timestamp}.json"
        log_path = self.log_dir / log_filename
        
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'file_path': str(file_path),
            'file_name': file_path.name,
            'model': self.model,
            'attempt': attempt,
            'request': {
                'prompt_length': len(request_data.get('prompt', '')),
                'has_image': request_data.get('has_image', False),
                'image_format': request_data.get('image_format', None),
                'media_type': request_data.get('media_type', None)
            },
            'response': None,
            'error': None,
            'success': False,
            'processing_time_ms': request_data.get('processing_time_ms', 0)
        }
        
        if response_data:
            log_entry['response'] = {
                'content_length': len(response_data.get('content', '')),
                'parsed_successfully': response_data.get('parsed_successfully', False),
                'extracted_fields': response_data.get('extracted_fields', []),
                'raw_response_preview': response_data.get('content', '')[:500] if response_data.get('content') else None
            }
            log_entry['success'] = response_data.get('parsed_successfully', False)
        
        if error:
            log_entry['error'] = {
                'message': str(error),
                'type': type(error).__name__,
                'traceback': traceback.format_exc(),
                'full_error': repr(error)
            }
            log_entry['success'] = False
        
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(log_entry, f, ensure_ascii=False, indent=2)
            logger.debug(f"LLM log saved: {log_path}")
        except Exception as e:
            logger.error(f"Failed to write LLM log: {e}")
    
    def encode_image(self, image_path: Path) -> str:
        """Encode image to base64 string"""
        with open(image_path, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def pdf_to_images(self, pdf_path: Path) -> List[Image.Image]:
        """Convert PDF pages to images"""
        try:
            images = convert_from_path(pdf_path, dpi=200)
            return images
        except Exception as e:
            logger.error(f"Error converting PDF {pdf_path}: {e}")
            return []
    
    def prepare_image_for_api(self, image: Image.Image) -> str:
        """Convert PIL Image to base64 string"""
        import io
        buffer = io.BytesIO()
        # Resize if too large
        max_size = (2000, 2000)
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        # Always save as PNG for consistency
        image.save(buffer, format='PNG')
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    def save_image_for_html(self, file_path: Path, output_dir: Path) -> str:
        """Save image in a format suitable for HTML display"""
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)
        
        try:
            if file_path.suffix.lower() in self.supported_image_formats:
                # Copy image file
                dest_path = images_dir / file_path.name
                shutil.copy2(file_path, dest_path)
                return f"images/{file_path.name}"
            
            elif file_path.suffix.lower() == self.supported_pdf_format:
                # Convert PDF to image
                images = self.pdf_to_images(file_path)
                if images:
                    image_name = f"{file_path.stem}.png"
                    dest_path = images_dir / image_name
                    images[0].save(dest_path, 'PNG')
                    return f"images/{image_name}"
        except Exception as e:
            logger.error(f"Error saving image for HTML: {e}")
        
        return None
    
    def create_extraction_prompt(self) -> str:
        """Create the prompt for Claude to extract raw receipt data"""
        return """You are a receipt data extraction system. Extract ALL visible information from this receipt image and return it as valid JSON.

CRITICAL: You must return ONLY a valid JSON object. No explanation, no markdown, just pure JSON.

Extract the following structure:

{
  "vendor_name": "string or null",
  "vendor_address": "string or null",
  "vendor_phone": "string or null",
  "vendor_tax_id": "string or null (ח.פ, עוסק מורשה, etc.)",
  "vendor_registration": "string or null",
  "date": "string or null (as shown)",
  "time": "string or null",
  "receipt_number": "string or null",
  "cashier": "string or null",
  "terminal": "string or null",
  "line_items": [
    {
      "line_text": "complete text of line",
      "description": "item name",
      "quantity": "string or null",
      "unit_price": "string or null",
      "total_price": "string or null",
      "discount": "string or null",
      "tax_code": "string or null",
      "additional_info": "string or null"
    }
  ],
  "payment_lines": ["array of payment-related text lines"],
  "payment_method": "string or null",
  "card_last_digits": "string or null",
  "approval_code": "string or null",
  "subtotal_line": "string or null",
  "tax_lines": ["array of tax-related lines"],
  "discount_lines": ["array of discount lines"],
  "total_line": "string or null",
  "currency_symbol": "string or null",
  "footer_text": ["array of footer lines"],
  "return_policy": "string or null",
  "website": "string or null",
  "promotional_text": "string or null",
  "language": "Hebrew/English/Arabic/Mixed or null",
  "receipt_type": "receipt/tax_invoice/invoice/credit_note/delivery_note or null",
  "is_duplicate": false,
  "has_signature_line": false,
  "printed_date_time": "string or null",
  "all_text_lines": ["array of ALL visible text lines in order"]
}

Rules:
- Use null for any field that is not visible or unclear
- Keep numbers as strings to preserve formatting
- Extract text EXACTLY as shown, including typos
- Return ONLY the JSON object, no other text"""
    
    def extract_receipt_data_with_retry(self, file_path: Path, max_attempts: int = 3) -> Dict:
        """Extract receipt data with retry logic"""
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Processing {file_path.name} (attempt {attempt}/{max_attempts})")
                result = self.extract_receipt_data(file_path, attempt)
                
                # If successful or max attempts reached, return result
                if result['_metadata'].get('status') == 'extracted' or attempt == max_attempts:
                    return result
                
                # Wait before retry
                if attempt < max_attempts:
                    time.sleep(2 * attempt)  # Exponential backoff
                    
            except Exception as e:
                logger.error(f"Error processing {file_path} on attempt {attempt}: {e}")
                if attempt == max_attempts:
                    return self.create_error_response(file_path, str(e))
        
        return self.create_error_response(file_path, "Max retry attempts reached")
    
    def extract_receipt_data(self, file_path: Path, attempt: int = 1) -> Dict:
        """Extract raw data from a single receipt file with improved error handling"""
        start_time = time.time()
        request_data = {
            'prompt': self.create_extraction_prompt(),
            'has_image': False,
            'image_format': None,
            'media_type': None
        }
        
        try:
            messages = []
            
            if file_path.suffix.lower() in self.supported_image_formats:
                # Handle image file
                base64_image = self.encode_image(file_path)
                media_type = self.get_media_type(file_path.suffix)
                
                request_data['has_image'] = True
                request_data['image_format'] = file_path.suffix.lower().strip('.')
                request_data['media_type'] = media_type
                
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self.create_extraction_prompt()
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_image
                                }
                            }
                        ]
                    }
                ]
            
            elif file_path.suffix.lower() == self.supported_pdf_format:
                # Handle PDF file
                images = self.pdf_to_images(file_path)
                if not images:
                    error_msg = f"PDF conversion failed for {file_path}"
                    logger.error(error_msg)
                    self.log_llm_call(file_path, request_data, None, Exception(error_msg), attempt)
                    return self.create_error_response(file_path, "PDF conversion failed")
                
                # Process first page - PDFs converted to PNG
                base64_image = self.prepare_image_for_api(images[0])
                media_type = "image/png"
                
                request_data['has_image'] = True
                request_data['image_format'] = 'png'
                request_data['media_type'] = media_type
                
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self.create_extraction_prompt()
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_image
                                }
                            }
                        ]
                    }
                ]
            else:
                error_msg = f"Unsupported file format: {file_path.suffix}"
                logger.warning(error_msg)
                self.log_llm_call(file_path, request_data, None, Exception(error_msg), attempt)
                return self.create_error_response(file_path, "Unsupported file format")
            
            # Call Anthropic API
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    temperature=0,
                    messages=messages
                )
            except Exception as api_error:
                processing_time = (time.time() - start_time) * 1000
                request_data['processing_time_ms'] = processing_time
                self.log_llm_call(file_path, request_data, None, api_error, attempt)
                raise api_error
            
            processing_time = (time.time() - start_time) * 1000
            request_data['processing_time_ms'] = processing_time
            
            # Parse the JSON response
            response_text = response.content[0].text
            
            # Clean up the response text if needed
            response_text = response_text.strip()
            
            # Remove markdown code blocks if present
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            try:
                receipt_data = json.loads(response_text)
                
                # Validate that we got a dictionary
                if not isinstance(receipt_data, dict):
                    raise ValueError(f"Expected JSON object, got {type(receipt_data)}")
                
                # Add metadata
                receipt_data['_metadata'] = {
                    'file_name': file_path.name,
                    'file_path': str(file_path),
                    'extracted_at': datetime.now().isoformat(),
                    'status': 'extracted',
                    'processing_time_ms': processing_time,
                    'attempt': attempt
                }
                
                # Log successful extraction
                response_data = {
                    'content': response_text[:500],  # First 500 chars for logging
                    'parsed_successfully': True,
                    'extracted_fields': list(receipt_data.keys())
                }
                self.log_llm_call(file_path, request_data, response_data, None, attempt)
                
                return receipt_data
                
            except (json.JSONDecodeError, ValueError) as e:
                error_msg = f"JSON parsing error: {e}\nResponse text: {response_text[:200]}..."
                logger.error(f"JSON parsing error for {file_path}: {e}")
                logger.debug(f"Raw response: {response_text[:500]}...")
                
                response_data = {
                    'content': response_text,
                    'parsed_successfully': False
                }
                self.log_llm_call(file_path, request_data, response_data, e, attempt)
                
                # Try to salvage what we can
                return self.create_error_response(file_path, f"JSON parsing error: {e}", raw_response=response_text)
            
        except Exception as e:
            error_msg = f"Error processing {file_path}: {e}"
            logger.error(error_msg)
            logger.debug(traceback.format_exc())
            
            processing_time = (time.time() - start_time) * 1000
            request_data['processing_time_ms'] = processing_time
            self.log_llm_call(file_path, request_data, None, e, attempt)
            
            return self.create_error_response(file_path, str(e))
    
    def create_error_response(self, file_path: Path, error_message: str, raw_response: Optional[str] = None) -> Dict:
        """Create a standardized error response"""
        response = {
            '_metadata': {
                'file_name': file_path.name,
                'file_path': str(file_path),
                'extracted_at': datetime.now().isoformat(),
                'error': error_message,
                'status': 'failed'
            }
        }
        
        if raw_response:
            response['_metadata']['raw_response'] = raw_response[:1000]  # Store first 1000 chars for debugging
        
        return response
    
    def process_folder(self, folder_path: Path, output_dir: Path) -> Tuple[List[Dict], Path, Path]:
        """Process all receipts in a folder using parallel processing"""
        results = []
        
        # Setup LLM logs directory
        self.setup_log_dir(output_dir)
        
        # Get all supported files
        supported_extensions = self.supported_image_formats | {self.supported_pdf_format}
        files = sorted([f for f in folder_path.iterdir() 
                       if f.is_file() and f.suffix.lower() in supported_extensions])
        
        total_files = len(files)
        logger.info(f"Found {total_files} receipt files to process")
        logger.info(f"Using {self.max_workers} parallel workers")
        logger.info(f"LLM logs directory: {self.log_dir}")
        
        # Process files in parallel
        start_time = time.time()
        completed = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks with retry logic
            future_to_file = {
                executor.submit(self.extract_receipt_data_with_retry, file_path): (idx, file_path)
                for idx, file_path in enumerate(files, 1)
            }
            
            # Process completed tasks as they finish
            for future in as_completed(future_to_file):
                idx, file_path = future_to_file[future]
                try:
                    result = future.result()
                    with self.processing_lock:
                        completed += 1
                        status = "✅" if result['_metadata'].get('status') == 'extracted' else "❌"
                        logger.info(f"{status} Completed {completed}/{total_files}: {file_path.name}")
                        results.append((idx, result))
                except Exception as e:
                    logger.error(f"Unexpected error processing {file_path}: {e}")
                    with self.processing_lock:
                        completed += 1
                        results.append((idx, self.create_error_response(file_path, str(e))))
        
        # Sort results by original index to maintain order
        results.sort(key=lambda x: x[0])
        results = [r[1] for r in results]
        
        elapsed_time = time.time() - start_time
        logger.info(f"Processing completed in {elapsed_time:.2f} seconds")
        if total_files > 0:
            logger.info(f"Average time per receipt: {elapsed_time/total_files:.2f} seconds")
        
        # Save raw extracted data
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        json_file = output_dir / f"extracted_data_{timestamp}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        logger.info(f"Raw data saved to: {json_file}")
        
        # Create review HTML
        html_file = self.create_review_html(results, output_dir)
        logger.info(f"Review HTML created: {html_file}")
        
        # Create summary log
        summary_log = output_dir / f"extraction_summary_{timestamp}.json"
        summary = {
            'timestamp': datetime.now().isoformat(),
            'total_files': total_files,
            'successful': sum(1 for r in results if r['_metadata'].get('status') == 'extracted'),
            'failed': sum(1 for r in results if r['_metadata'].get('status') == 'failed'),
            'processing_time_seconds': elapsed_time,
            'average_time_per_file': elapsed_time / total_files if total_files > 0 else 0,
            'model_used': self.model,
            'parallel_workers': self.max_workers,
            'log_directory': str(self.log_dir),
            'failed_files': [
                {
                    'file': r['_metadata']['file_name'],
                    'error': r['_metadata'].get('error', 'Unknown error'),
                    'raw_response_preview': r['_metadata'].get('raw_response', '')[:200] if r['_metadata'].get('raw_response') else None
                }
                for r in results if r['_metadata'].get('status') == 'failed'
            ]
        }
        
        with open(summary_log, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"Summary log saved to: {summary_log}")
        
        return results, json_file, html_file
    
    def create_review_html(self, results: List[Dict], output_dir: Path) -> Path:
        """Create an HTML file for reviewing extracted data"""
        html_file = output_dir / "receipt_review.html"
        
        html_content = """<!DOCTYPE html>
<html lang="he" dir="auto">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Receipt Data Review</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }
        
        .header {
            background: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        h1 {
            color: #333;
            margin-bottom: 10px;
        }
        
        .stats {
            color: #666;
            font-size: 18px;
        }
        
        .receipt-container {
            background: white;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .receipt-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .receipt-header.failed {
            background: linear-gradient(135deg, #f44336 0%, #e91e63 100%);
        }
        
        .receipt-number {
            font-size: 24px;
            font-weight: bold;
        }
        
        .receipt-status {
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
        }
        
        .status-extracted {
            background: rgba(255,255,255,0.3);
        }
        
        .status-failed {
            background: #f44336;
        }
        
        .receipt-body {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            padding: 20px;
        }
        
        @media (max-width: 968px) {
            .receipt-body {
                grid-template-columns: 1fr;
            }
        }
        
        .image-section {
            border-right: 1px solid #eee;
            padding-right: 20px;
        }
        
        @media (max-width: 968px) {
            .image-section {
                border-right: none;
                border-bottom: 1px solid #eee;
                padding-bottom: 20px;
                padding-right: 0;
            }
        }
        
        .receipt-image {
            width: 100%;
            height: auto;
            border-radius: 8px;
            cursor: zoom-in;
        }
        
        .data-section {
            padding-left: 20px;
            max-height: 800px;
            overflow-y: auto;
        }
        
        @media (max-width: 968px) {
            .data-section {
                padding-left: 0;
                padding-top: 20px;
            }
        }
        
        .data-group {
            margin-bottom: 25px;
        }
        
        .data-group-title {
            font-weight: bold;
            color: #667eea;
            margin-bottom: 10px;
            font-size: 16px;
            border-bottom: 2px solid #f0f0f0;
            padding-bottom: 5px;
        }
        
        .data-field {
            margin-bottom: 8px;
            display: flex;
            align-items: flex-start;
        }
        
        .field-label {
            font-weight: 600;
            color: #666;
            min-width: 140px;
            font-size: 14px;
        }
        
        .field-value {
            color: #333;
            font-size: 14px;
            flex: 1;
            word-break: break-word;
        }
        
        .field-value.null {
            color: #999;
            font-style: italic;
        }
        
        .line-items {
            background: #f9f9f9;
            border-radius: 5px;
            padding: 10px;
            margin-top: 10px;
        }
        
        .line-item {
            padding: 8px;
            border-bottom: 1px solid #e0e0e0;
        }
        
        .line-item:last-child {
            border-bottom: none;
        }
        
        .json-toggle {
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            margin-top: 15px;
        }
        
        .json-toggle:hover {
            background: #5a6fd8;
        }
        
        .json-view {
            display: none;
            background: #2d2d2d;
            color: #f8f8f2;
            padding: 15px;
            border-radius: 5px;
            margin-top: 10px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 400px;
            overflow-y: auto;
        }
        
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.9);
            cursor: zoom-out;
        }
        
        .modal-content {
            margin: auto;
            display: block;
            max-width: 90%;
            max-height: 90%;
            margin-top: 50px;
        }
        
        .close {
            position: absolute;
            top: 15px;
            right: 35px;
            color: #f1f1f1;
            font-size: 40px;
            font-weight: bold;
            cursor: pointer;
        }
        
        .close:hover {
            color: #bbb;
        }
        
        .error-message {
            background: #ffebee;
            color: #c62828;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
        }
        
        .raw-response {
            background: #f5f5f5;
            padding: 10px;
            border-radius: 5px;
            margin-top: 10px;
            font-family: monospace;
            font-size: 12px;
            max-height: 200px;
            overflow-y: auto;
        }
        
        .navigation {
            position: fixed;
            bottom: 20px;
            right: 20px;
            display: flex;
            gap: 10px;
        }
        
        .nav-button {
            background: #667eea;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }
        
        .nav-button:hover {
            background: #5a6fd8;
        }
        
        .processing-time {
            color: rgba(255,255,255,0.8);
            font-size: 12px;
            margin-top: 5px;
        }
        
        .attempt-info {
            color: rgba(255,255,255,0.7);
            font-size: 11px;
            margin-top: 3px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📋 Receipt Data Extraction Review</h1>
        <div class="stats">
            Total Receipts: <strong>""" + str(len(results)) + """</strong> | 
            Extracted: <strong>""" + str(sum(1 for r in results if r['_metadata'].get('status') == 'extracted')) + """</strong> | 
            Failed: <strong>""" + str(sum(1 for r in results if r['_metadata'].get('status') == 'failed')) + """</strong> |
            Generated: <strong>""" + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</strong>
        </div>
    </div>
"""
        
        for idx, receipt in enumerate(results, 1):
            metadata = receipt.get('_metadata', {})
            status = metadata.get('status', 'unknown')
            processing_time = metadata.get('processing_time_ms', 0)
            attempt = metadata.get('attempt', 1)
            
            header_class = 'failed' if status == 'failed' else ''
            
            html_content += f"""
    <div class="receipt-container" id="receipt-{idx}">
        <div class="receipt-header {header_class}">
            <div>
                <div class="receipt-number">Receipt #{idx}: {metadata.get('file_name', 'Unknown')}</div>
                {f'<div class="processing-time">Processed in {processing_time:.0f}ms</div>' if processing_time > 0 else ''}
                {f'<div class="attempt-info">Attempt {attempt}</div>' if attempt > 1 else ''}
            </div>
            <div class="receipt-status status-{status}">{status.upper()}</div>
        </div>
        <div class="receipt-body">
"""
            
            # Image section
            image_path = self.save_image_for_html(Path(metadata.get('file_path', '')), output_dir)
            if image_path:
                html_content += f"""
            <div class="image-section">
                <img src="{image_path}" alt="Receipt {idx}" class="receipt-image" onclick="openModal(this)">
            </div>
"""
            
            # Data section
            html_content += """
            <div class="data-section">
"""
            
            if status == 'failed':
                error_msg = metadata.get('error', 'Unknown error')
                html_content += f"""
                <div class="error-message">
                    <strong>❌ Error:</strong> {error_msg}
                    <br><br>
                    <strong>💡 Debugging Tips:</strong>
                    <ul style="margin-top: 10px; margin-left: 20px;">
                        <li>Check the llm_logs folder for detailed API logs</li>
                        <li>Verify the image quality and format</li>
                        <li>Try processing this file individually with --workers 1</li>
                    </ul>
"""
                if metadata.get('raw_response'):
                    html_content += f"""
                    <div class="raw-response">
                        <strong>Raw API Response:</strong><br>
                        {metadata.get('raw_response', '')[:500]}...
                    </div>
"""
                html_content += """
                </div>
"""
            else:
                # Display extracted data grouped by category
                if receipt.get('vendor_name') or receipt.get('vendor_address'):
                    html_content += """
                <div class="data-group">
                    <div class="data-group-title">🏢 Vendor Information</div>
"""
                    for field in ['vendor_name', 'vendor_address', 'vendor_phone', 'vendor_tax_id', 'vendor_registration']:
                        if field in receipt:
                            value = receipt[field]
                            display_value = value if value else '<span class="null">Not found</span>'
                            html_content += f"""
                    <div class="data-field">
                        <span class="field-label">{field.replace('_', ' ').title()}:</span>
                        <span class="field-value">{display_value}</span>
                    </div>
"""
                    html_content += "</div>"
                
                # Transaction details
                if receipt.get('date') or receipt.get('receipt_number'):
                    html_content += """
                <div class="data-group">
                    <div class="data-group-title">📅 Transaction Details</div>
"""
                    for field in ['date', 'time', 'receipt_number', 'cashier', 'terminal']:
                        if field in receipt:
                            value = receipt[field]
                            display_value = value if value else '<span class="null">Not found</span>'
                            html_content += f"""
                    <div class="data-field">
                        <span class="field-label">{field.replace('_', ' ').title()}:</span>
                        <span class="field-value">{display_value}</span>
                    </div>
"""
                    html_content += "</div>"
                
                # Line items
                if receipt.get('line_items'):
                    html_content += """
                <div class="data-group">
                    <div class="data-group-title">🛒 Line Items</div>
                    <div class="line-items">
"""
                    for item in receipt['line_items']:
                        html_content += f"""
                        <div class="line-item">
                            <strong>{item.get('description', 'Unknown item')}</strong><br>
                            Qty: {item.get('quantity', '-')} | 
                            Price: {item.get('unit_price', '-')} | 
                            Total: {item.get('total_price', '-')}
                            {f"<br>Original: {item.get('line_text', '')}" if item.get('line_text') else ''}
                        </div>
"""
                    html_content += """
                    </div>
                </div>
"""
                
                # Totals
                if receipt.get('total_line') or receipt.get('subtotal_line'):
                    html_content += """
                <div class="data-group">
                    <div class="data-group-title">💰 Totals</div>
"""
                    if receipt.get('subtotal_line'):
                        html_content += f"""
                    <div class="data-field">
                        <span class="field-label">Subtotal:</span>
                        <span class="field-value">{receipt.get('subtotal_line', '-')}</span>
                    </div>
"""
                    if receipt.get('tax_lines'):
                        for tax_line in receipt['tax_lines']:
                            html_content += f"""
                    <div class="data-field">
                        <span class="field-label">Tax:</span>
                        <span class="field-value">{tax_line}</span>
                    </div>
"""
                    if receipt.get('total_line'):
                        html_content += f"""
                    <div class="data-field">
                        <span class="field-label"><strong>Total:</strong></span>
                        <span class="field-value"><strong>{receipt.get('total_line', '-')}</strong></span>
                    </div>
"""
                    html_content += "</div>"
            
            # JSON toggle button
            json_str = json.dumps(receipt, ensure_ascii=False, indent=2)
            html_content += f"""
                <button class="json-toggle" onclick="toggleJson('json-{idx}')">View Raw JSON</button>
                <div id="json-{idx}" class="json-view">{json_str}</div>
            </div>
        </div>
    </div>
"""
        
        # Add JavaScript and closing HTML
        html_content += """
    <div class="navigation">
        <button class="nav-button" onclick="scrollToTop()">↑ Top</button>
    </div>
    
    <div id="imageModal" class="modal" onclick="closeModal()">
        <span class="close">&times;</span>
        <img class="modal-content" id="modalImage">
    </div>
    
    <script>
        function toggleJson(id) {
            const element = document.getElementById(id);
            element.style.display = element.style.display === 'none' || element.style.display === '' ? 'block' : 'none';
        }
        
        function openModal(img) {
            const modal = document.getElementById('imageModal');
            const modalImg = document.getElementById('modalImage');
            modal.style.display = 'block';
            modalImg.src = img.src;
        }
        
        function closeModal() {
            document.getElementById('imageModal').style.display = 'none';
        }
        
        function scrollToTop() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
        
        // Close modal on Esc key
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeModal();
            }
        });
    </script>
</body>
</html>
"""
        
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return html_file


def main():
    """Main function to run the receipt extractor"""
    parser = argparse.ArgumentParser(description='Extract data from receipts - Part 1')
    parser.add_argument('folder', type=str, help='Path to folder containing receipt images/PDFs')
    parser.add_argument('--output', type=str, help='Output folder for results (default: receipts_extracted)',
                       default='receipts_extracted')
    parser.add_argument('--api-key', type=str, help='Anthropic API key (or set ANTHROPIC_API_KEY env var)',
                       default=None)
    parser.add_argument('--workers', type=int, help='Number of parallel workers (default: 5)',
                       default=5)
    parser.add_argument('--model', type=str, help='Model to use (default: claude-3-5-sonnet-20241022)',
                       default=None)
    parser.add_argument('--log-dir', type=str, help='Directory for LLM call logs (default: output/llm_logs)',
                       default=None)
    
    args = parser.parse_args()
    
    # Validate paths
    folder_path = Path(args.folder)
    if not folder_path.exists() or not folder_path.is_dir():
        logger.error(f"Invalid folder path: {folder_path}")
        return 1
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Set model if specified
    if args.model:
        os.environ['ANTHROPIC_MODEL'] = args.model
    
    # Setup log directory
    log_dir = None
    if args.log_dir:
        log_dir = Path(args.log_dir)
    
    # Initialize extractor with parallel processing
    try:
        extractor = ReceiptExtractor(
            api_key=args.api_key, 
            max_workers=args.workers,
            log_dir=log_dir
        )
    except ValueError as e:
        logger.error(str(e))
        return 1
    
    # Process receipts
    logger.info(f"Starting receipt extraction from: {folder_path}")
    results, json_file, html_file = extractor.process_folder(folder_path, output_dir)
    
    # Print summary
    successful = sum(1 for r in results if r['_metadata'].get('status') == 'extracted')
    failed = sum(1 for r in results if r['_metadata'].get('status') == 'failed')
    
    print("\n" + "="*50)
    print("EXTRACTION COMPLETE")
    print("="*50)
    print(f"Total files processed: {len(results)}")
    print(f"Successfully extracted: {successful}")
    print(f"Failed: {failed}")
    print(f"Model used: {extractor.model}")
    print(f"Parallel workers: {args.workers}")
    print(f"\nOutput files:")
    print(f"  📄 Raw data: {json_file}")
    print(f"  🌐 Review HTML: {html_file}")
    print(f"  📊 LLM logs: {extractor.log_dir}")
    print(f"  📋 Summary: {output_dir}/extraction_summary_*.json")
    
    if failed > 0:
        print(f"\n⚠️ {failed} files failed to process.")
        print(f"   - Check {html_file} for error details")
        print(f"   - Review {extractor.log_dir} for API logs")
        print(f"   - See extraction_summary_*.json for failed files list")
    
    print(f"\n✨ Open the HTML file in your browser to review the extracted data")
    print(f"   Then run receipt_classifier.py to classify and summarize the expenses")
    
    return 0


if __name__ == "__main__":
    exit(main())
