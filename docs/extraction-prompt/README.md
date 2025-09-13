# Extraction Prompt Components

This directory contains all components that are loaded into the receipt extraction prompt.

## How It Works

All `.md` files in this directory are automatically loaded and combined into the extraction prompt in alphabetical order.

## Adding Custom Instructions

You can add additional files to customize the extraction behavior:

### Examples:

- `002-VENDOR-MAPPINGS.personal.md` - Custom vendor name mappings
- `003-CAR-INFO.personal.md` - Car license plate for parking/fuel receipts  
- `004-CUSTOM-CATEGORIES.personal.md` - Additional categorization rules
- `005-SPECIAL-RULES.personal.md` - Personal expense preferences

### Naming Convention

- Use numbered prefixes (001-, 002-, etc.) to control loading order
- Add `.personal.md` suffix for personal files that shouldn't be committed to git
- Regular `.md` files will be tracked by git

### Personal Files

Files ending with `.personal.md` are gitignored and won't be committed. This allows you to add personal information without it being shared in the repository.

## Current Files

- `001-ICOUNT_CATEGORIES.md` - Israeli tax categories and VAT rules (required)