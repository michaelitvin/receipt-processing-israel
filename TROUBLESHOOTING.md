# Troubleshooting Guide for Receipt Processing System

## Common Issues and Solutions

### 1. JSON Parsing Error: "Expecting value: line 1 column 1 (char 0)"

**Problem**: The AI model returned an empty response or non-JSON formatted text.

**Solutions Implemented**:
- Enhanced error handling to catch and log problematic responses
- Added response text cleaning to remove markdown formatting
- Implemented comprehensive logging of all LLM calls
- Added retry logic with better prompts

**How to Debug**:
1. Check the `llm_logs/` directory for the specific file's log
2. Look for the `raw_response` field in the error metadata
3. Review the HTML file to see what was extracted

**Manual Fix**:
If a specific file consistently fails:
```bash
# Process it separately with verbose logging
python receipt_extractor.py ./problem_file --workers 1 --log-dir ./debug_logs
```

### 2. PDF Conversion Failed

**Problem**: PDF file cannot be converted to image.

**Common Causes**:
- Poppler not installed
- Corrupted PDF file
- Password-protected PDF

**Solutions**:
```bash
# Install poppler
sudo apt-get install poppler-utils  # Ubuntu/Debian
brew install poppler                 # macOS

# Test PDF manually
pdftoppm input.pdf output -png
```

### 3. API Rate Limits

**Problem**: Too many parallel requests causing API errors.

**Solution**:
```bash
# Reduce parallel workers
python receipt_extractor.py ./receipts --workers 2
```

### 4. Memory Issues with Large Batches

**Problem**: System runs out of memory processing many large images.

**Solution**:
```bash
# Process in smaller batches
python receipt_extractor.py ./batch1 --output results1
python receipt_extractor.py ./batch2 --output results2
```

### 5. Inconsistent JSON Output

**Problem**: Claude sometimes returns explanations instead of pure JSON.

**Solutions Implemented**:
- Stronger prompt instructions emphasizing JSON-only output
- Response cleaning to strip markdown and explanations
- Validation of JSON structure before saving

### 6. Failed Classifications

**Problem**: Classification fails for extracted receipts.

**Debug Steps**:
1. Check if extraction was successful in the HTML review
2. Look for missing critical fields (vendor_name, date, amounts)
3. Review the classification logs

## Debugging Tools

### 1. Check LLM Logs

Each API call creates a detailed log file:
```json
{
  "timestamp": "2024-09-10T18:19:29",
  "file_path": "receipt_001.pdf",
  "model": "claude-3-5-sonnet-20241022",
  "request": {...},
  "response": {...},
  "error": {...},
  "success": false,
  "processing_time_ms": 1234
}
```

### 2. Review HTML Output

The HTML file shows:
- Which files succeeded/failed
- Processing time for each file
- Error messages with raw responses
- Side-by-side image and extracted data

### 3. Summary Logs

Check `extraction_summary_*.json` for:
- Overall success rate
- List of failed files with errors
- Processing performance metrics

## Prevention Tips

### 1. Pre-process Images

```python
# Resize large images before processing
from PIL import Image

img = Image.open('large_receipt.jpg')
img.thumbnail((2000, 2000))
img.save('receipt_resized.jpg')
```

### 2. Validate PDFs

```bash
# Check if PDF is valid
pdfinfo receipt.pdf

# Convert problem PDFs to images first
convert -density 200 problem.pdf receipt.jpg
```

### 3. Test with Single File First

```bash
# Test extraction
python -c "
from receipt_extractor import ReceiptExtractor
from pathlib import Path
import json

extractor = ReceiptExtractor(max_workers=1)
result = extractor.extract_receipt_data(Path('test_receipt.jpg'))
print(json.dumps(result, indent=2))
"
```

## Error Recovery

### If Extraction Partially Fails

1. The system will continue processing other files
2. Failed files are logged in the summary
3. You can re-run just the failed files:

```python
import json
from pathlib import Path

# Load summary
with open('extraction_summary_*.json') as f:
    summary = json.load(f)

# Get failed files
failed_files = [f['file'] for f in summary['failed_files']]

# Create a folder with just failed files for reprocessing
# Then run extractor again on that folder
```

### Manual Data Entry for Failed Files

If a file consistently fails:
1. Open the HTML review file
2. View the receipt image
3. Manually create the JSON structure
4. Add to the extracted_data.json file

## Performance Optimization

### For Large Batches (100+ receipts)

```bash
# Optimal settings for most systems
python receipt_extractor.py ./receipts \
    --workers 10 \
    --model claude-3-5-sonnet-20241022 \
    --output ./results

# Then classify with auto-mode
python receipt_classifier.py ./results/extracted_data_*.json \
    --auto \
    --workers 10
```

### For Better Accuracy

```bash
# Use more capable model with fewer workers
python receipt_extractor.py ./receipts \
    --workers 3 \
    --model claude-3-opus-20240229
```

## Contact Support

If issues persist:
1. Collect the following:
   - Error messages from console
   - Relevant LLM log files
   - The problem receipt image/PDF
   - extraction_summary_*.json file

2. Check Anthropic API status: https://status.anthropic.com

3. Verify API key has sufficient credits

## Quick Fixes Reference

| Error | Quick Fix |
|-------|-----------|
| JSON parsing error | Check LLM logs, reduce workers |
| PDF conversion failed | Install poppler, convert to image |
| Rate limit exceeded | Reduce --workers parameter |
| Out of memory | Process smaller batches |
| Missing API key | Set ANTHROPIC_API_KEY env variable |
| Network timeout | Retry with --workers 1 |