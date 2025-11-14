# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Logging utilities for receipt processing system"""

import json
import logging
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class ReceiptLogger:
    """Handles logging of LLM interactions and processing statistics"""
    
    def __init__(self, log_dir: Path):
        """Initialize logger with output directory"""
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
    def log_llm_interaction(
        self, 
        receipt_file: str,
        request_data: dict,
        response_data: dict = None,
        error: Exception = None,
        response_format: dict = None
    ):
        """Log complete LLM request/response/error to YAML file with pipe notation"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"llm_call_{Path(receipt_file).stem}_{timestamp}.yaml"
        log_path = self.log_dir / filename
        
        # Extract metadata from response if available
        api_metadata = response_data.get('api_metadata', {}) if response_data else {}
        prompt_used = response_data.get('prompt_used', '') if response_data else ''
        
        # Clean response data (remove metadata fields)
        clean_response = None
        if response_data:
            clean_response = {k: v for k, v in response_data.items() 
                            if k not in ['prompt_used', 'response_format_used', 'api_metadata']}
        
        # Prepare log entry with multiline strings using pipe notation
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'receipt_file': receipt_file,
            'request': request_data,
            'api_metadata': api_metadata,
            'response_format': response_format,
            'prompt_used': prompt_used,
            'response': clean_response,
            'error': str(error) if error else None,
            'success': error is None and response_data is not None
        }
        
        # Custom representer for multiline strings to use pipe notation
        def str_representer(dumper, data):
            # Force pipe notation for prompt_used field and any multiline strings
            if '\n' in data or len(data) > 80:
                return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
            return dumper.represent_scalar('tag:yaml.org,2002:str', data)
        
        yaml.add_representer(str, str_representer)
        
        with open(log_path, 'w', encoding='utf-8') as f:
            yaml.dump(log_entry, f, default_flow_style=False, allow_unicode=True, 
                     sort_keys=False, indent=2)
            
        logger.info(f"Logged LLM interaction to {log_path}")
        
    def log_processing_stats(self, stats: Dict[str, Any]):
        """Log processing summary statistics to YAML"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"processing_summary_{timestamp}.yaml"
        log_path = self.log_dir.parent / filename
        
        # Custom representer for multiline strings to use pipe notation
        def str_representer(dumper, data):
            # Force pipe notation for prompt_used field and any multiline strings
            if '\n' in data or len(data) > 80:
                return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
            return dumper.represent_scalar('tag:yaml.org,2002:str', data)
        
        yaml.add_representer(str, str_representer)
        
        with open(log_path, 'w', encoding='utf-8') as f:
            yaml.dump(stats, f, default_flow_style=False, allow_unicode=True, 
                     sort_keys=False, indent=2)
            
        logger.info(f"Logged processing stats to {log_path}")