These tools translate the game's Japanese `TextAsset` files into another language and wrap them in the `m_Name` / `m_Script` format that **uabea** expects.

## Files

- `translate_assets.py` — Translates Japanese text in `assets/original/TextAsset/*.bytes` and writes both human-readable translations and uabea-ready converted assets.
- `convert_assets.py` — Wraps already-translated `.bytes` files into the `m_Name` / `m_Script` JSON format without re-translating. Can be run standalone or is called automatically by `translate_assets.py`.

## Expected folder layout

```
assets/
├── original/
│   └── TextAsset/          # Japanese source .bytes files
├── en/                     # Target language folder (example: English)
│   ├── TextAsset/          # Translated .bytes files (created by translate_assets.py)
│   ├── converted_assets/   # uabea-ready wrapped assets (created by the scripts)
│   └── translations.json   # Cache of original → translated strings
└── es/                     # Another target language
    └── ...
```

Create a new folder under `assets/` for your target language using its two-letter code (e.g. `en`, `es`, `fr`, `de`, `ko`, `nl`, `ru`, `zh`). If no DeepL API key is set, the script falls back to Google Translate.

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

### 2. Translate the assets

Run from this directory:

```bash
python translate_assets.py
```

The script will:

1. Discover all `assets/{lang}/TextAsset` folders and ask you to pick one.
2. Read the original Japanese `.bytes` files from `assets/original/TextAsset/`.
3. Extract unique Japanese strings from JSON fields named `message` or `select_<number>`.
4. Translate those strings using DeepL (or Google Translate as a fallback).
5. Write translated `.bytes` files to `assets/{lang}/TextAsset/`.
6. Cache the original → translated mapping in `assets/{lang}/translations.json` so later runs skip already-translated text.
7. Convert the translated files into the `m_Name` / `m_Script` wrapper format under `assets/{lang}/converted_assets/TextAsset/`.

### 3. Convert only (no translation)

If you already have translated `.bytes` files and only need the uabea wrapper format, run:

```bash
python convert_assets.py
```

Then select the target language folder. The converted output is written to `assets/{lang}/converted_assets/TextAsset/`.

## Importing into uabea

After conversion, open `datapack.unity3d` in **uabea**. See step 5 in the "Quick workflow" section of the root `README.MD` for a more detailed explanation.
