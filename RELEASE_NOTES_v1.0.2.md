# amiagi v1.0.2 — Internationalization (PL/EN)

Release focused on full i18n support: all user-facing strings externalized to
JSON locale files with runtime language switching.

## Highlights

### i18n Infrastructure (`src/amiagi/i18n/`)
- New translation subsystem based on JSON locale files (`locales/pl.json`, `locales/en.json`)
- `_("key", var=val)` function with `str.format()` interpolation
- Fallback chain: active locale → Polish → raw key (untranslated strings never crash the app)
- Auto-initialisation from `AMIAGI_LANG` environment variable on import

### String Extraction
- **335 hardcoded Polish strings** migrated to `_()` calls across 3 files:
  - `textual_cli.py` — 278 replacements
  - `cli.py` — 52 replacements
  - `main.py` — 5 replacements
- ~360 locale keys organized by section (help, permissions, models, agents, dashboard, etc.)

### Language Selection (3 methods)
1. **`--lang` CLI argument**: `amiagi --lang en`
2. **`/lang` TUI command**: `/lang en` switches at runtime, instantly rebuilds help text
3. **`AMIAGI_LANG` env var**: `AMIAGI_LANG=en amiagi`
4. Default: Polish (`pl`)

### Locale Files
- `pl.json` — full Polish translation (~360 keys)
- `en.json` — full English translation (~360 keys)
- Completeness verified: both files have identical key sets

## Test Coverage

- **24 new i18n tests** covering:
  - Translation lookup, fallback, interpolation
  - Language switching (set/get, case-insensitive, whitespace)
  - `AMIAGI_LANG` environment variable auto-init
  - Locale file completeness (same keys, no empty values)
  - `/lang` TUI command handler (show, switch, invalid code)
  - `--lang` argparse integration
- Total: **1069 tests passed**, 0 failed

## Compatibility

- Python: 3.10+
- OS: Linux
- Locale files shipped as package data (`locales/*.json`)

## Migration Notes

- No breaking changes — default language remains Polish
- To switch to English: `amiagi --lang en` or `AMIAGI_LANG=en`
- Adding new languages: create `src/amiagi/i18n/locales/<code>.json` with the same key set

## Safety

No permission policy expansion and no shell allowlist relaxation were introduced
in this release.

Use only in isolated/sandboxed environments as described in `SECURITY.md`.
