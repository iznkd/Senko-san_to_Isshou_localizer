These tools translate the game's Japanese `TextAsset` files into another language, replace the in-game font with one that supports the target script, and produce a ready-to-use patched `datapack.unity3d`.

## Files

- `main.py` — **Primary entry point.** Translates, converts, replaces the font, generates an SDF atlas, and patches `datapack.unity3d` in one step. Supports processing a single language or all languages at once.
- `translate_assets.py` — Translates Japanese text in `assets/original/TextAsset/*.bytes` and writes translated `.bytes` files plus a `translations.json` cache. Can be run standalone.
- `convert_assets.py` — Wraps `.bytes` files into the `m_Name` / `m_Script` JSON format. Called automatically by `main.py` and `translate_assets.py`, or can be run standalone.
- `font_replace.py` — Generates a replacement SDF atlas from a TTF/OTF font and patches the TMP_FontAsset MonoBehaviour binary. Adds new glyph and character table entries for codepoints not in the original font. Called by `main.py`.

## Expected folder layout

```
assets/
├── original/
│   ├── TextAsset/          # Japanese source .bytes files
│   └── datapack.unity3d    # Original game datapack
├── en/                     # Target language folder (example: English)
│   ├── TextAsset/          # Translated .bytes files
│   ├── converted_assets/   # Wrapped assets (m_Name / m_Script format)
│   ├── translations.json   # Cache of original → translated strings
│   └── en.unity3d          # Patched datapack (output)
└── es/                     # Another target language
    └── ...
fonts/
├── SourceSans3-Bold.ttf    # Default font (Latin/Cyrillic)
├── NotoSansArabic-Bold.ttf # For Arabic
└── ...                     # See fonts/README.md for recommendations
```

Create a new folder under `assets/` for your target language using its two-letter code (e.g. `en`, `es`, `fr`, `de`, `ar`, `hi`, `ko`, `nl`, `ru`, `zh`).

## Install dependencies

Before running the scripts, install the required Python packages from the project root:

```bash
cd ../..
pip install -e .
```

See the **Python setup** section in the root `README.MD` for details.

## Usage

### 1. Optional: set up DeepL

Copy the example environment file and add your API key:

```bash
cp ../../.env.example ../../.env
```

Edit `.env`:

```
DEEPL_API_KEY=your_key_here
```

Without a DeepL key, the script uses Google Translate. You can get a DeepL key at the [DeepL API quickstart](https://developers.deepl.com/docs/getting-started/quickstart).

### 2. Translate, patch, and build (recommended)

Run from this directory:

```bash
python main.py
```

The script will:

1. List available language folders and ask you to pick one (or all).
2. Read the original Japanese `.bytes` files from `assets/original/TextAsset/`.
3. Extract unique strings from JSON fields (`message`, `select_*`, `Title`), including non-Japanese strings for font coverage.
4. Translate Japanese strings using DeepL (or Google Translate as a fallback).
5. Cache all translations in `assets/{lang}/translations.json`.
6. Write translated `.bytes` files to `assets/{lang}/TextAsset/`.
7. Convert them into the `m_Name` / `m_Script` wrapper format.
8. Prompt you to select a replacement font from `fonts/` (default: `SourceSans3-Bold.ttf`).
9. Check that the font covers all characters used in translations.
10. Generate a new SDF atlas (16384×16384) with the replacement font.
11. Expand the glyph and character tables for any codepoints missing from the original font.
12. Patch face info (line height, ascender, descender, etc.) to match the replacement font.
13. Replace TextAssets in `datapack.unity3d` with the translated versions.
14. Save the patched datapack to `assets/{lang}/{lang}.unity3d`.

### 3. Translate only (no patching)

If you only want to translate without patching the datapack:

```bash
python translate_assets.py
```

### 4. Convert only (no translation)

If you already have translated `.bytes` files and only need the wrapper format:

```bash
python convert_assets.py
```

## Font selection

The script prompts you to pick a font. Place `.ttf` or `.otf` files in the `fonts/` folder. See `fonts/README.md` for recommended fonts per language.

- **Latin/Cyrillic** (en, de, es, fr, id, nl, pt, ru): `SourceSans3-Bold.ttf`
- **Arabic** (ar): `NotoSansArabic-Bold.ttf`
- **Hindi** (hi): `NotoSansDevanagari-Bold.ttf`
- **Korean** (ko): `NotoSansKR-Bold.ttf`
- **Chinese** (zh): `NotoSansSC-Bold.ttf`
