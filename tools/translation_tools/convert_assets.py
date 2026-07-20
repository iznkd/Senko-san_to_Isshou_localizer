import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def discover_textasset_folders(root: Path | None = None) -> list[Path]:
    """Find all assets/{folder}/TextAsset directories under the project root."""
    root = root or PROJECT_ROOT
    textasset_dirs = []
    assets_dir = root / "assets"
    if not assets_dir.exists():
        return textasset_dirs
    for folder in sorted(assets_dir.iterdir()):
        if folder.name == "original":
            continue
        textasset = folder / "TextAsset"
        if folder.is_dir() and textasset.is_dir():
            textasset_dirs.append(textasset)
    return textasset_dirs


def select_textasset_folder(folders: list[Path] | None = None) -> Path:
    """Prompt the user to pick an assets/{folder}/TextAsset directory."""
    if folders is None:
        folders = discover_textasset_folders()
    if not folders:
        raise FileNotFoundError(
            f"No assets/{{folder}}/TextAsset directories found under {PROJECT_ROOT / 'assets'}"
        )
    if len(folders) == 1:
        print(
            f"Using the only available asset folder: {folders[0].relative_to(PROJECT_ROOT)}"
        )
        return folders[0]
    print("Available asset folders:")
    for i, folder in enumerate(folders, 1):
        print(f"  {i}. {folder.relative_to(PROJECT_ROOT)}")
    while True:
        choice = input("Select a folder by number: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(folders):
                return folders[idx]
        print("Invalid selection. Please enter a valid number.")


def convert_file(input_path: Path, output_path: Path) -> None:
    """Wrap a .bytes file's contents in m_Name / m_Script JSON format."""
    content = input_path.read_text(encoding="utf-8", errors="replace", newline="")
    # Strip the trailing ".bytes" extension, leaving e.g. "story0" or "103.atlas".
    name = input_path.stem

    # Minify the inner content if it is valid JSON, removing structural whitespace
    # while preserving whitespace inside string values.
    try:
        content = json.dumps(json.loads(content), ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        pass  # Not JSON; keep original raw content.

    # Compact separators remove spaces after colons and commas in the wrapper.
    output = json.dumps(
        {"m_Name": name, "m_Script": content},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output + "\n", encoding="utf-8", newline="\n")


def convert_folder(input_dir: Path, output_dir: Path) -> None:
    """Convert all .bytes files under input_dir to m_Name/m_Script wrappers in output_dir."""
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for input_path in sorted(input_dir.rglob("*.bytes")):
        relative_path = input_path.relative_to(input_dir)
        output_path = output_dir / relative_path
        convert_file(input_path, output_path)
        print(f"Converted {relative_path} -> {output_path}")


def main(input_dir: Path | None = None, output_dir: Path | None = None) -> None:
    """Convert translated assets into m_Name/m_Script wrappers.

    When called without arguments, the user is prompted to select an
    assets/{folder}/TextAsset directory and the converted output is written
    to assets/{folder}/converted_assets/TextAsset.
    """
    if input_dir is None or output_dir is None:
        selected = select_textasset_folder()
        base = selected.parent
        if input_dir is None:
            input_dir = base / "TextAsset"
        if output_dir is None:
            output_dir = base / "converted_assets" / "TextAsset"

    convert_folder(input_dir, output_dir)


if __name__ == "__main__":
    main()
