# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Image processing utilities for receipts"""

import logging
from pathlib import Path
from typing import List, Union
from PIL import Image
import pdf2image
import io

logger = logging.getLogger(__name__)


class ImageHandler:
    """Handles image and PDF processing for receipts"""
    
    SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    SUPPORTED_PDF_FORMAT = '.pdf'
    MAX_IMAGE_SIZE = (2048, 2048)  # Max size for API
    
    @classmethod
    def is_supported_file(cls, file_path: Path) -> bool:
        """Check if file format is supported"""
        suffix = file_path.suffix.lower()
        return suffix in cls.SUPPORTED_IMAGE_FORMATS or suffix == cls.SUPPORTED_PDF_FORMAT
        
    @classmethod
    def process_file(cls, file_path: Path) -> List[Image.Image]:
        """Process image or PDF file and return list of PIL Images"""
        suffix = file_path.suffix.lower()
        
        if suffix == cls.SUPPORTED_PDF_FORMAT:
            return cls._process_pdf(file_path)
        elif suffix in cls.SUPPORTED_IMAGE_FORMATS:
            return [cls._process_image(file_path)]
        else:
            raise ValueError(f"Unsupported file format: {suffix}")
            
    @classmethod
    def _process_pdf(cls, pdf_path: Path) -> List[Image.Image]:
        """Convert PDF to images"""
        try:
            images = pdf2image.convert_from_path(pdf_path, dpi=200)
            logger.info(f"Converted PDF {pdf_path.name} to {len(images)} images")
            return [cls._resize_image(img) for img in images]
        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {e}")
            raise
            
    @classmethod
    def _process_image(cls, image_path: Path) -> Image.Image:
        """Load and process image file"""
        try:
            with Image.open(image_path) as img:
                # Convert to RGB if necessary
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                    
                # Resize if too large
                img = cls._resize_image(img)
                
                logger.info(f"Processed image {image_path.name}")
                return img
        except Exception as e:
            logger.error(f"Error processing image {image_path}: {e}")
            raise
            
    @classmethod
    def _resize_image(cls, img: Image.Image) -> Image.Image:
        """Resize image if it exceeds max dimensions"""
        if img.width > cls.MAX_IMAGE_SIZE[0] or img.height > cls.MAX_IMAGE_SIZE[1]:
            img.thumbnail(cls.MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)
            logger.debug(f"Resized image to {img.size}")
        return img
        
    @classmethod
    def save_image_for_excel(cls, img: Image.Image, output_path: Path) -> Path:
        """Save image in format suitable for Excel embedding"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save as JPEG for smaller file size
        if output_path.suffix.lower() != '.jpg':
            output_path = output_path.with_suffix('.jpg')
            
        img.save(output_path, 'JPEG', quality=85, optimize=True)
        logger.debug(f"Saved image for Excel: {output_path}")
        return output_path