import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

import convert_assets

# Load environment variables from a .env file (e.g. DEEPL_API_KEY).
load_dotenv()

# Project root is two levels up from tools/translation_tools.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Regex matching JSON string values for "message" or "select_<number>" keys.
FIELD_RE = re.compile(
    r'"(?P<key>message|select_\d+|Title)"(?P<sep>\s*:\s*)"(?P<val>(?:[^"\\]|\\.)*)"'
)

# Regex that detects any Japanese script (Hiragana, Katakana, Kanji/CJK).
JA_RE = re.compile(
    r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]"
)

# Batch configuration for translation calls.
BATCH_SIZE = 20
RETRIES = 3
SLEEP_SECONDS = 0.5


def is_japanese(text: str) -> bool:
    """Return True when the string contains at least one Japanese character."""
    return bool(text) and bool(JA_RE.search(text))


def collect_japanese_texts(text: str, found: dict):
    """Collect Japanese text from JSON string values in translatable fields.

    `found` is used as an ordered set: keys are Japanese strings in file order,
    values are None.  This keeps the iteration order deterministic.
    """
    for match in FIELD_RE.finditer(text):
        raw_value = match.group("val")
        decoded = json.loads('"' + raw_value + '"')
        if is_japanese(decoded):
            found[decoded] = None


def replace_japanese_texts(text: str, mapping: dict) -> str:
    """Replace Japanese text in translatable JSON string values in raw text."""

    def _replace(match: re.Match) -> str:
        key = match.group("key")
        sep = match.group("sep")
        raw_value = match.group("val")
        decoded = json.loads('"' + raw_value + '"')
        if is_japanese(decoded) and decoded in mapping:
            decoded = mapping[decoded]
        encoded = json.dumps(decoded, ensure_ascii=False)[1:-1]
        return f'"{key}"{sep}"{encoded}"'

    return FIELD_RE.sub(_replace, text)


def translate_batch(translator, texts: list[str]) -> list[str]:
    """Translate a batch of texts with retry logic."""
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            return translator.translate_batch(texts)
        except Exception as exc:  # pragma: no cover - network related
            last_error = exc
            print(f"  batch failed (attempt {attempt}/{RETRIES}): {exc}")
            time.sleep(SLEEP_SECONDS * attempt)
    print("  falling back to one-by-one translation for this batch")
    results = []
    for text in texts:
        for attempt in range(1, RETRIES + 1):
            try:
                results.append(translator.translate(text))
                break
            except Exception as exc:  # pragma: no cover - network related
                print(f"  single translation failed: {exc}")
                time.sleep(SLEEP_SECONDS * attempt)
                if attempt == RETRIES:
                    results.append(text)
    return results


class _StdlibTranslator:
    """Minimal Google Translate fallback using only the Python standard library."""

    def __init__(self, source: str = "ja", target: str = "en"): 
        self.source = source
        self.target = target

    def translate(self, text: str) -> str:
        import urllib.parse
        import urllib.request

        encoded = urllib.parse.quote(text)
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={self.source}&tl={self.target}&dt=t&q={encoded}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/115.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(segment[0] for segment in data[0])

    def translate_batch(self, texts: list[str]) -> list[str]:
        results = []
        for i, text in enumerate(texts):
            results.append(self.translate(text))
            if i < len(texts) - 1:
                time.sleep(0.1)
        return results


class _DeepLTranslator:
    """Wrapper around the official DeepL Python client."""

    # DeepL requires target-language variants for some languages.
    TARGET_VARIANTS = {
        "en": "EN-US",
        "pt": "PT-PT",
        "zh": "ZH-HANS",
    }

    def __init__(self, api_key: str, source: str = "ja", target: str = "en"):
        import deepl

        self._client = deepl.Translator(api_key)
        self._source = source.upper()
        self._target = self.TARGET_VARIANTS.get(target.lower(), target.upper())

    def translate(self, text: str) -> str:
        result = self._client.translate_text(
            text, source_lang=self._source, target_lang=self._target
        )
        return result.text

    def translate_batch(self, texts: list[str]) -> list[str]:
        results = self._client.translate_text(
            texts, source_lang=self._source, target_lang=self._target
        )
        return [r.text for r in results]


def _get_translator(source: str = "ja", target: str = "en"):
    """Prefer DeepL if DEEPL_API_KEY is set, else deep-translator Google."""
    deepl_key = os.environ.get("DEEPL_API_KEY")
    if deepl_key:
        try:
            import deepl

            print(f"Using DeepL ({source} -> {target})")
            return _DeepLTranslator(deepl_key, source=source, target=target)
        except ImportError:
            print(
                "DEEPL_API_KEY is set but the 'deepl' package is not installed; "
                "falling back to Google Translate"
            )

    try:
        from deep_translator import GoogleTranslator

        print(f"Using deep_translator ({source} -> {target})")
        return GoogleTranslator(source=source, target=target)
    except ImportError:
        print("deep-translator not installed; using stdlib Google Translate fallback")
        return _StdlibTranslator(source=source, target=target)


def translate_texts(
    texts: list[str], source: str = "ja", target: str = "en"
) -> dict[str, str]:
    """Translate a list of unique strings to a {original: translated} mapping."""
    if not texts:
        return {}

    translator = _get_translator(source=source, target=target)
    ordered = list(texts)
    mapping = {}

    for i in range(0, len(ordered), BATCH_SIZE):
        chunk = ordered[i : i + BATCH_SIZE]
        translated = translate_batch(translator, chunk)
        for original, translated_text in zip(chunk, translated):
            mapping[original] = translated_text
        time.sleep(SLEEP_SECONDS)

    return mapping


def main():
    textasset_folders = convert_assets.discover_textasset_folders(PROJECT_ROOT)
    if not textasset_folders:
        print(
            f"No assets/{{folder}}/TextAsset directories found under {PROJECT_ROOT / 'assets'}"
        )
        return

    selected_textasset = convert_assets.select_textasset_folder(textasset_folders)
    assets_folder = selected_textasset.parent

    # The folder name is the target language code expected by the translator.
    target_lang = assets_folder.name

    # Source assets always come from the original folder; translated files go
    # directly into the selected folder's TextAsset directory.
    assets_dir = PROJECT_ROOT / "assets" / "original" / "TextAsset"
    output_textasset_dir = assets_folder / "TextAsset"
    translations_cache = assets_folder / "translations.json"
    converted_dir = assets_folder / "converted_assets" / "TextAsset"

    if not assets_dir.exists():
        print(f"Original asset directory not found: {assets_dir}")
        return

    # Cache translations between runs so re-translation is avoided.
    translations: dict[str, str] = {}
    if translations_cache.exists():
        with translations_cache.open("r", encoding="utf-8") as f:
            translations = json.load(f)

    asset_files = sorted(assets_dir.rglob("*.bytes"))
    if not asset_files:
        print(f"No *.bytes files found under {assets_dir}")
        return

    # First pass: validate files are JSON and collect all unique Japanese strings.
    parsed_files: list[tuple[Path, str]] = []
    all_japanese_texts: dict[str, None] = {}
    for asset_path in asset_files:
        try:
            text = asset_path.read_text(encoding="utf-8", newline="")
            json.loads(text)  # validate only
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            print(f"Skipping non-JSON or unreadable file: {asset_path}")
            continue

        collect_japanese_texts(text, all_japanese_texts)
        parsed_files.append((asset_path, text))

    to_translate = [t for t in all_japanese_texts if t not in translations]
    if to_translate:
        new_translations = translate_texts(to_translate, target=target_lang)
        translations.update(new_translations)
        with translations_cache.open("w", encoding="utf-8") as f:
            json.dump(translations, f, ensure_ascii=False, indent=2)

    # Second pass: replace strings in the original raw text and write out.
    output_textasset_dir.mkdir(parents=True, exist_ok=True)
    for asset_path, text in parsed_files:
        new_text = replace_japanese_texts(text, translations)
        relative_path = asset_path.relative_to(assets_dir)
        out_path = output_textasset_dir / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(new_text, encoding="utf-8", newline="")
        print(f"Wrote {relative_path}")

    # Also generate the m_Name / m_Script converted asset wrapper.
    convert_assets.convert_folder(output_textasset_dir, converted_dir)


if __name__ == "__main__":
    main()
