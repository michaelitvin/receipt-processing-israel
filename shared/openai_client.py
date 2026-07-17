# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""OpenAI API client with structured output support"""

import base64
import io
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
    vendor_id: str
    date: str
    document_type: str  # "invoice", "receipt", "invoice+receipt"
    original_file: str
    reasoning: str
    currency: str

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


# USD per 1M tokens: (input, cached input, output) - update when OpenAI changes pricing
MODEL_PRICING = {
    'gpt-5': (1.25, 0.125, 10.00),
    'gpt-5-mini': (0.25, 0.025, 2.00),
    'gpt-5-nano': (0.05, 0.005, 0.40),
}


def estimate_cost_usd(model: str, usage: Dict[str, int]) -> Optional[float]:
    """Estimate call cost in USD from token usage; None if model pricing unknown"""
    pricing = MODEL_PRICING.get(model)
    if not pricing or not usage:
        return None
    input_price, cached_price, output_price = pricing
    cached = usage.get('cached_input_tokens', 0)
    uncached = usage.get('input_tokens', 0) - cached
    return (uncached * input_price + cached * cached_price
            + usage.get('output_tokens', 0) * output_price) / 1_000_000


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
        extraction_prompt_dir: Path,
        image: Optional["Image.Image"] = None
    ) -> Dict[str, Any]:
        """Extract receipt data using OpenAI Responses API with structured output.

        When ``image`` is given, that PIL image is sent instead of the source file
        (used for raster-only PDFs whose embedded bitmap reads far better than the
        raw PDF the model would otherwise downsample). Otherwise the file itself is
        sent - raw PDF as input_file, or the image as input_image.
        """

        # Record request timing
        from datetime import datetime
        request_start_time = datetime.now()

        # Read and encode file (image or PDF), unless an override image was provided
        if image is None:
            file_data, mime_type = await self._encode_file(file_path)
        else:
            file_data, mime_type = None, None

        # Build the prompt using Jinja template with all extraction prompt content
        prompt = await self._build_extraction_prompt(extraction_prompt_dir)
        
        # Use the loaded schema
        text_format = self.text_format
        
        try:
            # Determine content type based on input
            if image is not None:
                # Override bitmap (raster-only PDF): send as an image
                buf = io.BytesIO()
                image.save(buf, 'PNG')
                img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
                file_content = {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{img_b64}"
                }
            elif mime_type == 'application/pdf':
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

            # Capture token usage for cost tracking
            usage = getattr(response, 'usage', None)
            usage_data = None
            if usage:
                input_details = getattr(usage, 'input_tokens_details', None)
                output_details = getattr(usage, 'output_tokens_details', None)
                usage_data = {
                    'input_tokens': usage.input_tokens,
                    'cached_input_tokens': getattr(input_details, 'cached_tokens', 0) or 0,
                    'output_tokens': usage.output_tokens,
                    'reasoning_tokens': getattr(output_details, 'reasoning_tokens', 0) or 0,
                    'total_tokens': usage.total_tokens,
                }

            # Parse the structured response from output_text
            result = json.loads(response.output_text)
            
            # Add the original file path
            result['receipt_info']['original_file'] = str(file_path)
            
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
                'text_format_type': text_format.get('type', 'unknown'),
                'usage': usage_data
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