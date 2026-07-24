"""Translate and patch: end-to-end pipeline.

Usage:
    python main.py

This script combines translate_assets.py and patch_datapack.py into a single
command. It will:
1. Prompt you to select a language folder.
2. Translate the original Japanese TextAssets into the selected language.
3. Convert them into the m_Name / m_Script wrapper format.
4. Patch `datapack.unity3d` by replacing the TextAssets with the translated ones.
5. Save the patched bundle to `assets/{lang}/{lang}.unity3d`.
"""

import json
import sys
from pathlib import Path

try:
    import UnityPy
except ImportError:
    print("UnityPy is not installed. Install it with:\n  pip install UnityPy")
    sys.exit(1)

import convert_assets
import font_replace
import translate_assets


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATAPACK_PATH = PROJECT_ROOT / "assets" / "original" / "datapack.unity3d"


def load_converted_assets(lang_dir: Path) -> dict[str, dict]:
    """Load all converted .bytes files into a dict keyed by m_Name."""
    converted_dir = lang_dir / "converted_assets" / "TextAsset"
    if not converted_dir.exists():
        print(f"Converted assets directory not found: {converted_dir}")
        sys.exit(1)

    assets = {}
    for file_path in sorted(converted_dir.rglob("*.bytes")):
        try:
            raw = file_path.read_text(encoding="utf-8", newline="")
            data = json.loads(raw)
            name = data.get("m_Name")
            if name:
                assets[name] = data
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"  Skipping {file_path.name}: {e}")
    return assets


def patch_datapack(lang_dir: Path, lang: str) -> None:
    """Replace TextAssets in datapack.unity3d with translated versions."""
    if not DATAPACK_PATH.exists():
        print(f"datapack.unity3d not found at: {DATAPACK_PATH}")
        sys.exit(1)

    print(f"\nLoading converted assets for '{lang}'...")
    replacement_assets = load_converted_assets(lang_dir)
    if not replacement_assets:
        print("No converted assets found. Nothing to patch.")
        sys.exit(1)
    print(f"  Found {len(replacement_assets)} converted asset(s).")

    print(f"\nLoading: {DATAPACK_PATH}")
    env = UnityPy.load(str(DATAPACK_PATH))

    # Replace the TMP font to support accented/Latin chars.
    FONTS_DIR = PROJECT_ROOT / "fonts"
    FONTS_DIR.mkdir(exist_ok=True)
    DEFAULT_FONT = FONTS_DIR / "SourceSans3-Bold.ttf"
    FALLBACK_FONT = Path(r"C:\Windows\Fonts\arialbd.ttf")

    # List available fonts in the fonts/ folder
    available_fonts = sorted(
        f for f in FONTS_DIR.iterdir()
        if f.suffix.lower() in ('.ttf', '.otf')
    )

    print(f"\n  Font selection:")
    print(f"    [0] original (keep original font, no replacement) not recommended for languages that use non-ASCII characters")
    for i, f in enumerate(available_fonts, 1):
        default_marker = " (default)" if f == DEFAULT_FONT else ""
        print(f"    [{i}] {f.name}{default_marker}")
    print(f"    Or type a full path to a .ttf/.otf file")

    default_idx = next((i for i, f in enumerate(available_fonts, 1) if f == DEFAULT_FONT), None)
    prompt_default = f", Enter={default_idx}" if default_idx else ""
    print(f"  Choose{prompt_default}: ", end="")
    custom_font_input = input().strip()

    if custom_font_input.lower() == "original" or custom_font_input == "0":
        REPLACEMENT_FONT = None
        print("  Keeping original font (no replacement).")
    elif custom_font_input == "" and DEFAULT_FONT.exists():
        REPLACEMENT_FONT = DEFAULT_FONT
        print(f"  Using default: {DEFAULT_FONT.name}")
    elif custom_font_input.isdigit():
        idx = int(custom_font_input)
        if 1 <= idx <= len(available_fonts):
            REPLACEMENT_FONT = available_fonts[idx - 1]
            print(f"  Selected: {REPLACEMENT_FONT.name}")
        else:
            print(f"  Invalid selection. Falling back to {FALLBACK_FONT.name}")
            REPLACEMENT_FONT = FALLBACK_FONT
    elif custom_font_input:
        REPLACEMENT_FONT = Path(custom_font_input)
        if not REPLACEMENT_FONT.exists():
            print(f"  Font not found: {REPLACEMENT_FONT}")
            REPLACEMENT_FONT = FALLBACK_FONT
            print(f"  Falling back to {FALLBACK_FONT.name}")
    else:
        REPLACEMENT_FONT = FALLBACK_FONT
        print(f"  No default found, using {FALLBACK_FONT.name}")

    if REPLACEMENT_FONT and REPLACEMENT_FONT.exists():
        ttf_bytes = REPLACEMENT_FONT.read_bytes()

        # 1. Replace TTF data in Font objects
        font_targets = ("07LogoTypeGothic7", "ロゴたいぷゴシック")
        fonts_replaced = []
        for obj in env.objects:
            if obj.type.name == "Font":
                data = obj.read()
                if any(t in data.m_Name for t in font_targets):
                    print(f"\n  Replacing font TTF '{data.m_Name}' with {REPLACEMENT_FONT.name}...")
                    data.m_FontData = list(ttf_bytes)
                    data.save()
                    fonts_replaced.append(data.m_Name)
        if fonts_replaced:
            print(f"  Replaced {len(fonts_replaced)} font TTF(s): {fonts_replaced}")

        # 2. Collect all Unicode codepoints used in translations
        needed_codepoints: set[int] = set()
        translations_file = lang_dir / "translations.json"
        if translations_file.exists():
            translations = json.loads(translations_file.read_text("utf-8"))
            for translated_text in translations.values():
                if isinstance(translated_text, str):
                    needed_codepoints.update(
                        ord(c) for c in translated_text if ord(c) >= 32
                    )
        # Also include codepoints from converted assets
        for asset_info in replacement_assets.values():
            script = asset_info.get("m_Script", "")
            if isinstance(script, bytes):
                script = script.decode("utf-8", errors="replace")
            needed_codepoints.update(ord(c) for c in script if ord(c) >= 32)
        print(f"\n  Unique codepoints in translations: {len(needed_codepoints)}")

        # Check font coverage
        from PIL import ImageFont
        check_font = ImageFont.truetype(str(REPLACEMENT_FONT), 20)
        missing = set()
        for cp in sorted(needed_codepoints):
            try:
                bbox = check_font.getbbox(chr(cp))
                if bbox is None or (bbox[2] - bbox[0]) <= 0:
                    missing.add(cp)
            except Exception:
                missing.add(cp)
        if missing:
            print(f"  WARNING: {len(missing)} codepoints missing from font:")
            sample = sorted(missing)[:20]
            print(f"    {' '.join(f'U+{cp:04X}({chr(cp)})' for cp in sample)}")
        else:
            print(f"  All {len(needed_codepoints)} codepoints supported by font ✓")

        # 3. Generate replacement SDF atlas + patch zero-rect glyphs
        TMP_FONT_NAME = "07LogoTypeGothic7 SDF"
        mono_obj = font_replace.get_mono_object(env, TMP_FONT_NAME)
        if mono_obj:
            mono_raw = mono_obj.get_raw_data()
            print(f"\n  Generating replacement SDF atlas from {REPLACEMENT_FONT.name}...")
            atlas_img, patched_mono = font_replace.generate_patched_font(
                ttf_data=ttf_bytes,
                mono_raw=mono_raw,
                extra_codepoints=needed_codepoints,
                log_fn=lambda msg: print(f"    {msg}"),
            )

            # Apply patched MonoBehaviour (only zero-rect glyphs get updated)
            mono_obj.set_raw_data(patched_mono)

            # Find and replace the atlas Texture2D
            atlas_replaced = False
            for obj in env.objects:
                if obj.type.name == "Texture2D":
                    data = obj.read()
                    if data.m_Name == f"{TMP_FONT_NAME} Atlas":
                        print(f"  Replacing atlas texture: {data.m_Name}")
                        data.image = atlas_img
                        data.save()
                        atlas_replaced = True
                        break
            if atlas_replaced:
                print("  SDF atlas replaced successfully.")
            else:
                print("  WARNING: Atlas texture not found in bundle.")
        else:
            print("\n  WARNING: TMP_FontAsset MonoBehaviour not found, skipping atlas.")
    elif REPLACEMENT_FONT:
        print(f"\n  WARNING: Replacement font not found at {REPLACEMENT_FONT}, skipping.")

    # Replace matching TextAssets
    replaced = 0
    skipped = []
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            if data.m_Name in replacement_assets:
                replacement = replacement_assets[data.m_Name]
                script = replacement["m_Script"]
                # UnityPy expects m_Script as a str
                if isinstance(script, bytes):
                    script = script.decode("utf-8")
                data.m_Script = script
                data.save()
                replaced += 1

    # Check which assets were not found in the bundle
    found_names = set()
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            data = obj.read()
            found_names.add(data.m_Name)

    for name in replacement_assets:
        if name not in found_names:
            skipped.append(name)

    print(f"\n  Replaced: {replaced}/{len(replacement_assets)} TextAsset(s)")
    if skipped:
        print(f"  Not found in bundle: {', '.join(skipped)}")

    # Replace images if the lang has an img/ folder
    img_dir = lang_dir / "img"
    if img_dir.exists() and any(img_dir.iterdir()):
        from PIL import Image

        img_files = {p.stem: p for p in img_dir.iterdir()
                     if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp')}
        print(f"\n  Replacing {len(img_files)} image(s) from {img_dir.name}/...")

        # Build export_name -> obj mapping (handles duplicate names like AssetRipper)
        name_counts: dict[str, int] = {}
        img_replaced = 0
        for obj in env.objects:
            if obj.type.name not in ("Texture2D", "Sprite"):
                continue
            data = obj.read()
            name = data.m_Name

            if name in name_counts:
                export_name = f"{name}_{name_counts[name]}"
                name_counts[name] += 1
            else:
                export_name = name
                name_counts[name] = 0

            if export_name in img_files:
                img = Image.open(img_files[export_name])
                data.image = img
                data.save()
                print(f"    Replaced: {export_name} ({img.size[0]}x{img.size[1]})")
                img_replaced += 1

        print(f"  Replaced {img_replaced}/{len(img_files)} image(s)")
        if img_replaced < len(img_files):
            replaced_names = set()
            nc2: dict[str, int] = {}
            for obj in env.objects:
                if obj.type.name in ("Texture2D", "Sprite"):
                    n = obj.read().m_Name
                    en = f"{n}_{nc2[n]}" if n in nc2 else n
                    nc2[n] = nc2.get(n, -1) + 1
                    if en in img_files:
                        replaced_names.add(en)
            not_found = sorted(set(img_files.keys()) - replaced_names)
            if not_found:
                print(f"  Not found in bundle: {not_found}")
    else:
        print(f"\n  No img/ folder found for '{lang}', skipping image replacement.")

    # Save the patched datapack
    output_path = lang_dir / f"{lang}.unity3d"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nSaving patched datapack to: {output_path}")
    with open(output_path, "wb") as f:
        f.write(env.file.save(packer="lz4"))
    print("Done!")


def translate_and_patch_lang(lang_dir: Path, lang: str) -> None:
    """Translate and patch a single language."""
    # --- Translate ---
    print(f"\n{'=' * 60}")
    print(f"  Step 1/2: Translating assets for '{lang}'")
    print(f"{'=' * 60}\n")

    assets_dir = PROJECT_ROOT / "assets" / "original" / "TextAsset"
    output_textasset_dir = lang_dir / "TextAsset"
    translations_cache = lang_dir / "translations.json"
    converted_dir = lang_dir / "converted_assets" / "TextAsset"

    if not assets_dir.exists():
        print(f"Original asset directory not found: {assets_dir}")
        sys.exit(1)

    # Cache translations between runs so re-translation is avoided.
    translations: dict[str, str] = {}
    if translations_cache.exists():
        with translations_cache.open("r", encoding="utf-8") as f:
            translations = json.load(f)

    asset_files = sorted(assets_dir.rglob("*.bytes"))
    if not asset_files:
        print(f"No *.bytes files found under {assets_dir}")
        sys.exit(1)

    # First pass: validate files are JSON and collect all translatable strings.
    parsed_files: list[tuple[Path, str]] = []
    all_japanese_texts: dict[str, None] = {}
    all_texts: dict[str, None] = {}
    for asset_path in asset_files:
        try:
            text = asset_path.read_text(encoding="utf-8", newline="")
            json.loads(text)  # validate only
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            print(f"Skipping non-JSON or unreadable file: {asset_path}")
            continue

        translate_assets.collect_japanese_texts(text, all_japanese_texts)
        translate_assets.collect_all_texts(text, all_texts)
        parsed_files.append((asset_path, text))

    # Add non-Japanese strings to translations with identity mapping
    # so their characters are included in font generation.
    for msg in all_texts:
        if msg not in translations:
            if not translate_assets.is_japanese(msg):
                translations[msg] = msg  # keep as-is

    to_translate = [t for t in all_japanese_texts if t not in translations]
    if to_translate:
        print(f"Translating {len(to_translate)} new string(s)...")
        new_translations = translate_assets.translate_texts(to_translate, target=lang)
        translations.update(new_translations)

    # Always save — includes both translated and identity-mapped strings
    with translations_cache.open("w", encoding="utf-8") as f:
        json.dump(translations, f, ensure_ascii=False, indent=2)
    print(f"  translations.json: {len(translations)} entries")

    # Second pass: replace strings in the original raw text and write out.
    output_textasset_dir.mkdir(parents=True, exist_ok=True)
    for asset_path, text in parsed_files:
        new_text = translate_assets.replace_japanese_texts(text, translations)
        relative_path = asset_path.relative_to(assets_dir)
        out_path = output_textasset_dir / relative_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(new_text, encoding="utf-8", newline="")
        print(f"Wrote {relative_path}")

    # Generate the m_Name / m_Script converted asset wrapper.
    convert_assets.convert_folder(output_textasset_dir, converted_dir)

    # --- Patch ---
    print(f"\n{'=' * 60}")
    print(f"  Step 2/2: Patching datapack.unity3d for '{lang}'")
    print(f"{'=' * 60}")

    patch_datapack(lang_dir, lang)


def main():
    # --- Step 1: Select language ---
    textasset_folders = convert_assets.discover_textasset_folders(PROJECT_ROOT)
    if not textasset_folders:
        print(
            f"No assets/{{folder}}/TextAsset directories found under {PROJECT_ROOT / 'assets'}"
        )
        sys.exit(1)

    print("Available language folders:")
    for i, folder in enumerate(textasset_folders, 1):
        lang_name = folder.parent.name
        print(f"  {i}. {lang_name}")
    print(f"  A. All of the above")

    while True:
        choice = input("\nSelect a folder by number (or 'A' for all): ").strip()
        if choice.lower() == 'a':
            selected_folders = textasset_folders
            break
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(textasset_folders):
                selected_folders = [textasset_folders[idx]]
                break
        print("Invalid selection. Please enter a valid number or 'A'.")

    for selected_textasset in selected_folders:
        lang_dir = selected_textasset.parent
        lang = lang_dir.name
        print(f"\n{'#' * 60}")
        print(f"  Processing: {lang}")
        print(f"{'#' * 60}")
        translate_and_patch_lang(lang_dir, lang)


if __name__ == "__main__":
    main()
