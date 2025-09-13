"""OpenAI API client with structured output support"""

import base64
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from openai import AsyncOpenAI
import aiofiles
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)


@dataclass
class ReceiptInfo:
    number: str
    vendor: str
    date: str
    document_type: str  # "invoice", "receipt", "invoice+receipt"
    original_file: str
    reasoning: str

@dataclass
class AmountData:
    total_excl_vat: float
    vat_amount: float
    total_incl_vat: float

@dataclass
class LineItem:
    description: str
    amount_excl_vat: float
    vat: float
    total: float
    deductible: bool = True

@dataclass
class Classification:
    category: str
    confidence: float
    document_type_mapping: str  # For iCount export

@dataclass 
class ProcessedReceipt:
    file_path: Path
    receipt_info: ReceiptInfo
    amounts: AmountData
    line_items: List[LineItem]
    classification: Classification
    processing_status: str
    validation_notes: Optional[Dict[str, str]] = None


class OpenAIClient:
    """Client for OpenAI API with structured output"""
    
    def __init__(self, api_key: str, model: str):
        """Initialize OpenAI client with Jinja template environment"""
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        
        # Setup Jinja2 template environment and schema
        prompts_dir = Path(__file__).parent.parent / 'prompts'
        self.jinja_env = Environment(loader=FileSystemLoader(prompts_dir))
        self.prompt_template = self.jinja_env.get_template('receipt_extraction_prompt.j2')
        
        # Load JSON schema from file
        schema_path = prompts_dir / 'receipt_extraction_schema.json'
        with open(schema_path, 'r', encoding='utf-8') as f:
            self.text_format = json.load(f)
        
    async def extract_receipt_data(
        self, 
        file_path: Path,
        extraction_prompt_dir: Path
    ) -> Dict[str, Any]:
        """Extract receipt data using OpenAI Responses API with structured output"""
        
        # Record request timing
        from datetime import datetime
        request_start_time = datetime.now()
        
        # Read and encode file (image or PDF)
        file_data, mime_type = await self._encode_file(file_path)
        
        # Build the prompt using Jinja template with all extraction prompt content
        prompt = await self._build_extraction_prompt(extraction_prompt_dir)
        
        # Use the loaded schema
        text_format = self.text_format
        
        try:
            # Determine content type based on file type
            if mime_type == 'application/pdf':
                file_content = {
                    "type": "input_file",
                    "filename": file_path.name,
                    "file_data": f"data:{mime_type};base64,{file_data}"
                }
            else:
                # For images, use input_image type
                file_content = {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{file_data}"
                }
            
            # API parameters for logging
            api_params = {
                'model': self.model,
                'store': False,
                'text_format': text_format
            }
            
            # Make API call using Responses API with structured output
            api_call_start = datetime.now()
            response = await self.client.responses.create(
                model=self.model,
                instructions="You are a receipt data extraction expert. Extract data accurately from receipts and classify expenses for Israeli tax reporting.",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            file_content
                        ]
                    }
                ],
                text={"format": text_format},
                store=False  # Don't store for compliance
            )
            api_call_end = datetime.now()
            
            # Parse the structured response from output_text
            result = json.loads(response.output_text)
            
            # Add the original filename
            result['receipt_info']['original_file'] = file_path.name
            
            # Add metadata for logging (without underscore prefix)
            result['prompt_used'] = prompt
            result['response_format_used'] = text_format
            result['api_metadata'] = {
                'model': self.model,
                'request_timestamp': request_start_time.isoformat(),
                'api_call_start': api_call_start.isoformat(),
                'api_call_end': api_call_end.isoformat(),
                'total_response_time_seconds': (api_call_end - request_start_time).total_seconds(),
                'api_response_time_seconds': (api_call_end - api_call_start).total_seconds(),
                'store': False,
                'text_format_type': text_format.get('type', 'unknown')
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error extracting receipt data: {e}")
            raise
            
    async def _encode_file(self, file_path: Path) -> tuple[str, str]:
        """Encode file to base64 and determine MIME type"""
        async with aiofiles.open(file_path, 'rb') as f:
            file_data = await f.read()
            base64_data = base64.b64encode(file_data).decode('utf-8')
            
        # Determine MIME type based on file extension
        suffix = file_path.suffix.lower()
        if suffix == '.pdf':
            mime_type = 'application/pdf'
        elif suffix in ['.jpg', '.jpeg']:
            mime_type = 'image/jpeg'
        elif suffix == '.png':
            mime_type = 'image/png'
        elif suffix == '.gif':
            mime_type = 'image/gif'
        elif suffix == '.webp':
            mime_type = 'image/webp'
        else:
            mime_type = 'image/jpeg'  # Default fallback
            
        return base64_data, mime_type
            
    async def _build_extraction_prompt(self, extraction_prompt_dir: Path) -> str:
        """Build the extraction prompt using Jinja template with all content from extraction-prompt directory"""
        
        # Load all markdown files from extraction-prompt directory in order
        # Skip README.md file
        prompt_files = sorted([f for f in extraction_prompt_dir.glob("*.md") if f.name != "README.md"])
        
        combined_content = []
        for file_path in prompt_files:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                combined_content.append(content)
        
        # Join all content with double newlines
        full_content = "\n\n".join(combined_content)
        
        # Render the template with combined content
        prompt = self.prompt_template.render(
            categories_content=full_content,
            personal_instructions="",  # Now included in categories_content
        )
        
        return prompt