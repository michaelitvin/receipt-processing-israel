# Copyright (c) 2025 Michael Litvin
# Licensed under AGPL-3.0-or-later - see LICENSE file for details
"""Image processing utilities for receipts"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Union
from PIL import Image
import pdf2image
import io

logger = logging.getLogger(__name__)


class ImageHandler:
    """Handles image and PDF processing for receipts"""

    SUPPORTED_IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    SUPPORTED_PDF_FORMAT = '.pdf'
    MAX_IMAGE_SIZE = (2048, 2048)  # Max size for API
    # A PDF with fewer non-whitespace text-layer chars than this has no usable text
    # layer - its content is a raster (e.g. Weezmo receipts). Calibrated: Weezmo
    # receipts read 0-42 chars, text-layer invoices 600+.
    SPARSE_TEXT_MAX_CHARS = 100
    MIN_EMBEDDED_DIM = 300  # ignore logos/masks smaller than this
    
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
    def _pdf_text_char_count(cls, pdf_path: Path) -> int:
        """Non-whitespace chars in the PDF's text layer; -1 if unknowable.

        -1 (poppler missing / error) means "don't gate" - the caller keeps the
        normal path rather than risk a wrong decision.
        """
        exe = shutil.which('pdftotext')
        if not exe:
            return -1
        try:
            out = subprocess.run([exe, str(pdf_path), '-'],
                                 capture_output=True, timeout=30)
            return len(''.join(out.stdout.decode('utf-8', 'replace').split()))
        except Exception as e:
            logger.debug(f"pdftotext failed on {pdf_path}: {e}")
            return -1

    @classmethod
    def _largest_embedded_image(cls, pdf_path: Path) -> Optional[Image.Image]:
        """Largest-file-size embedded raster >= MIN_EMBEDDED_DIM, or None.

        Weezmo-style PDFs stretch a crisp embedded bitmap onto a tall page; blank
        or mask layers compress tiny, so the largest PNG is the real receipt.
        """
        exe = shutil.which('pdfimages')
        if not exe:
            return None
        with tempfile.TemporaryDirectory() as td:
            try:
                subprocess.run([exe, '-png', str(pdf_path), str(Path(td) / 'e')],
                               capture_output=True, timeout=60, check=True)
            except Exception as e:
                logger.debug(f"pdfimages failed on {pdf_path}: {e}")
                return None
            best, best_size = None, -1
            for p in Path(td).glob('e-*.png'):
                try:
                    with Image.open(p) as im:
                        w, h = im.size
                except Exception:
                    continue
                if w >= cls.MIN_EMBEDDED_DIM and h >= cls.MIN_EMBEDDED_DIM \
                        and p.stat().st_size > best_size:
                    best, best_size = p, p.stat().st_size
            if best is None:
                return None
            with Image.open(best) as im:
                return im.convert('RGB') if im.mode not in ('RGB', 'L') else im.copy()

    @classmethod
    def extraction_bitmap(cls, file_path: Path) -> Optional[Image.Image]:
        """The crisp source bitmap for a raster-only PDF, else None.

        Some digitally-generated PDFs (e.g. Weezmo fuel receipts) carry no usable
        text layer and stretch a small embedded bitmap onto a tall, sparse page.
        The model reads the raw PDF poorly (it downsamples the whole tall page),
        so for these we send the native embedded bitmap instead - to both the API
        and the Excel review image. Returns None for normal PDFs (which keep the
        raw-PDF path) and for non-PDF inputs.
        """
        if file_path.suffix.lower() != cls.SUPPORTED_PDF_FORMAT:
            return None
        chars = cls._pdf_text_char_count(file_path)
        if chars < 0 or chars >= cls.SPARSE_TEXT_MAX_CHARS:
            return None
        bitmap = cls._largest_embedded_image(file_path)
        if bitmap is not None:
            logger.info(f"{file_path.name}: no text layer ({chars} chars); using "
                        f"embedded bitmap {bitmap.size} for extraction")
        return bitmap

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