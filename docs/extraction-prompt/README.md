# Extraction Prompt Components

This directory contains all components that are loaded into the receipt extraction prompt.

## How It Works

All `.md` files in this directory are automatically loaded and combined into the extraction prompt in alphabetical order.

## Adding Custom Instructions

You can add additional files to customize the extraction behavior:

### Examples:

- `002-VENDOR_MAPPINGS.personal.md` - Custom vendor name mappings
- `003-CAR_INFO.personal.md` - Car license plate for parking/fuel receipts
- `004-CUSTOM_CATEGORIES.personal.md` - Additional categorization rules
- `005-SPECIAL_RULES.personal.md` - Personal expense preferences

### Naming Convention

- Tracked files: `NNN-lowercase-kebab-name.md`, matching the rest of `docs/`
- Personal files: `NNN-SCREAMING_SNAKE_NAME.personal.md` — the shouting body
  makes the lowercase `.personal.` stand out, so a never-commit file is obvious
  at a glance. This overrides the kebab convention used elsewhere in `docs/`.
- Use numbered prefixes (001-, 002-, etc.) to control loading order — the prefix
  *is* the load order, since the loader sorts the glob alphabetically. Digits
  sort before letters, so the prefix keeps ordering stable regardless of the
  body's casing.
- Keep `.personal.` itself lowercase — `.gitignore` matches on that exact
  string, and uppercasing it would make the file committable.

### Personal Files

Files ending with `.personal.md` are gitignored and won't be committed. This allows you to add personal information without it being shared in the repository.

## Current Files

- `001-icount-categories.md` - Israeli tax categories and VAT rules (required)