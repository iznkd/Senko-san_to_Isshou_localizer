# Custom Fonts

Place your `.ttf` or `.otf` font files in this folder.
They will appear as options when running `main.py`.

## Recommended fonts per language

| Language(s) | Font file | Download |
|-------------|-----------|----------|
| en, de, es, fr, id, nl, pt, ru | `SourceSans3-Bold.ttf` **(default)** | [Google Fonts](https://fonts.google.com/specimen/Source+Sans+3) |
| hi (Hindi) | `NotoSansDevanagari-Bold.ttf` | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari) |
| ko (Korean) | `NotoSansKR-Bold.ttf` | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+KR) |
| zh (Chinese) | `NotoSansSC-Bold.ttf` | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+SC) |

## Alternative fonts (thinner/lighter style)

| Font | Style | Coverage | Download |
|------|-------|----------|----------|
| **Noto Sans Regular** | Thinner Noto | Same as Bold | [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans) |
| **Source Sans 3** | Elegant, thin | Latin, Cyrillic | [Google Fonts](https://fonts.google.com/specimen/Source+Sans+3) |
| **Inter** | Clean, modern | Latin, Cyrillic | [Google Fonts](https://fonts.google.com/specimen/Inter) |
| **Roboto** | Android default | Latin, Cyrillic | [Google Fonts](https://fonts.google.com/specimen/Roboto) |
| **IBM Plex Sans** | Professional | Latin, Cyrillic, Arabic, Devanagari, Korean | [Google Fonts](https://fonts.google.com/specimen/IBM+Plex+Sans) |
| **Nunito** | Rounded, light | Latin, Cyrillic | [Google Fonts](https://fonts.google.com/specimen/Nunito) |
| **M PLUS 1p** | Thin, JP-friendly | Latin, CJK | [Google Fonts](https://fonts.google.com/specimen/M+PLUS+1p) |

## Notes

- Download the TTF file and place it in this folder.
- Bold weights are recommended for better legibility with SDF rendering.
- The font coverage check in `main.py` will warn you if any characters are missing.
- For CJK languages, font files can be 10-20 MB due to the large character set.
