"""Generate a replacement SDF atlas and patched MonoBehaviour from a TTF font.

This module:
1. Parses the TMP_FontAsset MonoBehaviour binary for glyph/character tables.
2. Optionally expands the tables with new Unicode codepoints not in the original.
3. Renders ALL glyphs from the replacement font and packs into an SDF atlas.
4. Rebuilds the MonoBehaviour binary with updated tables and trailing data.

Binary layout (07LogoTypeGothic7 SDF, Unity 2022.3.15f1):
  [0-383]          Header (fixed size)
  [384-387]        Glyph count prefix (uint32, stored value may be < actual)
  [388-411]        Glyph table preamble (24 bytes: first glyph record data)
  [412-G_END]      Glyph records (N × 52 bytes each)
  [G_END]          Character count (uint32)
  [G_END+4-C_END]  Character records (M × 16 bytes each)
  [C_END-EOF]      Trailing data (kerning tables, etc.)
"""

from __future__ import annotations

import io
import struct
from typing import Optional

import numpy as np
from PIL import Image, ImageFont
from scipy import ndimage as scipy_ndimage

# ---------------------------------------------------------------------------
# Binary layout constants
# ---------------------------------------------------------------------------
HEADER_SIZE = 384       # bytes before glyph count
GLYPH_COUNT_OFF = 384   # uint32 glyph count
GLYPH_PREFIX_OFF = 388  # 24-byte preamble before first record
GLYPH_PREFIX_LEN = 24
GLYPH_FIRST_OFF = 412   # first glyph record
GLYPH_STRIDE = 52       # bytes per glyph record
CHAR_STRIDE = 16         # bytes per character record

# SDF spread must match the original font's m_AtlasPadding (7).
DEFAULT_SDF_SPREAD = 7
DEFAULT_ATLAS_GAP = 20   # pixels between packed glyphs (>= 2*(spread+outline))
DEFAULT_OUTLINE = 3      # mild dilation to thicken letterforms
ATLAS_SCALE = 2           # scale factor for atlas (2 = 16384, gives 2x crispness)
DEFAULT_ATLAS_SIZE = 8192  # original atlas dimensions


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_glyph_table(raw: bytes, max_atlas_dim: int = DEFAULT_ATLAS_SIZE * ATLAS_SCALE) -> list[dict]:
    """Parse all glyph records from the MonoBehaviour binary."""
    glyph_count = struct.unpack_from('<I', raw, GLYPH_COUNT_OFF)[0]
    glyphs = []
    off = GLYPH_FIRST_OFF
    for _ in range(glyph_count):
        if off + GLYPH_STRIDE > len(raw):
            break
        idx = struct.unpack_from('<I', raw, off)[0]
        mw, mh, mbx, mby, ma = struct.unpack_from('<5f', raw, off + 4)
        rx, ry, rw, rh = struct.unpack_from('<4i', raw, off + 24)
        scale = struct.unpack_from('<f', raw, off + 40)[0]
        atlas_idx = struct.unpack_from('<I', raw, off + 44)[0]

        glyphs.append({
            'index': idx,
            'offset': off,
            'metrics': {'width': mw, 'height': mh, 'bearingX': mbx,
                        'bearingY': mby, 'advance': ma},
            'rect': {'x': rx, 'y': ry, 'w': rw, 'h': rh},
            'scale': scale,
            'atlas_index': atlas_idx,
        })
        off += GLYPH_STRIDE

    return glyphs


def find_char_table_offset(raw: bytes) -> int:
    """Find the character table count offset by scanning for the record pattern.

    Character records are 16 bytes: <fIII (scale=1.0, elem_type=1, unicode, glyph_index).
    We scan backwards from a known hiragana region to find the first record,
    then the count uint32 is 12 bytes before that (count + 8-byte prefix).
    """
    # Search for U+3042 (hiragana a) which must exist in the original font
    target = struct.pack('<fII', 1.0, 1, 0x3042)
    pos = raw.find(target)
    if pos < 0:
        raise ValueError("Cannot locate character table: U+3042 not found")

    # Scan backwards to find the first valid char record
    off = pos
    while off >= 16:
        off -= 16
        s, et = struct.unpack_from('<fI', raw, off)
        if not (s == 1.0 and et == 1):
            first_record = off + 16
            # Count is 16 bytes before first record (4 count + 12 prefix)
            count_off = first_record - 16
            return count_off
    raise ValueError("Cannot locate character table start")


def parse_char_table(raw: bytes, char_count_off: int) -> tuple[list[dict], int]:
    """Parse the character table. Returns (chars, first_record_offset).

    Binary layout: [count:4][prefix:12][record0:16][record1:16]...
    Record format: <fIII (scale, element_type, unicode, glyph_index)
    """
    count = struct.unpack_from('<I', raw, char_count_off)[0]
    first_off = char_count_off + 16  # 4 (count) + 12 (prefix)
    chars = []
    for i in range(count):
        off = first_off + i * CHAR_STRIDE
        if off + CHAR_STRIDE > len(raw):
            break
        scale = struct.unpack_from('<f', raw, off)[0]
        elem_type = struct.unpack_from('<I', raw, off + 4)[0]
        unicode_val = struct.unpack_from('<I', raw, off + 8)[0]
        glyph_idx = struct.unpack_from('<I', raw, off + 12)[0]
        chars.append({
            'element_type': elem_type,
            'unicode': unicode_val,
            'glyph_index': glyph_idx,
            'scale': scale,
        })
    return chars, first_off


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _pack_glyph_record(idx: int, mw: float, mh: float, mbx: float,
                       mby: float, ma: float, rx: int, ry: int,
                       rw: int, rh: int, scale: float,
                       atlas_idx: int) -> bytes:
    """Serialise a single 52-byte glyph record."""
    return struct.pack('<I5f4i f I I',
                       idx, mw, mh, mbx, mby, ma,
                       rx, ry, rw, rh,
                       scale, atlas_idx, 0)


def _pack_char_record(elem_type: int, unicode_val: int,
                      glyph_idx: int, scale: float) -> bytes:
    """Serialise a single 16-byte character record."""
    return struct.pack('<fIII', scale, elem_type, unicode_val, glyph_idx)


# ---------------------------------------------------------------------------
# SDF rendering
# ---------------------------------------------------------------------------

def _compute_sdf_tile(alpha_tile: np.ndarray, spread: int,
                      outline: int = 0) -> np.ndarray:
    """Compute SDF values from a greyscale alpha tile.

    If *outline* > 0, the glyph shape is dilated by that many pixels before
    computing the distance field.  This bakes a wider gradient into the SDF
    so that TMP's shader can render a thicker outline / shadow.
    """
    inside = alpha_tile > 127
    if not inside.any():
        return np.zeros_like(alpha_tile, dtype=np.uint8)
    if inside.all():
        return np.full_like(alpha_tile, 255, dtype=np.uint8)

    # Dilate the glyph shape to widen the "inside" region
    if outline > 0:
        dist_to_inside = scipy_ndimage.distance_transform_edt(~inside)
        inside = dist_to_inside <= outline

    dist_outside = scipy_ndimage.distance_transform_edt(~inside)
    dist_inside = scipy_ndimage.distance_transform_edt(inside)
    signed_distance = dist_inside - dist_outside

    # Normalize to match original TMP atlas SDF encoding (range 49-164,
    # centered at 128). The TMP shader thresholds expect this range.
    SDF_MID = 128.0
    SDF_HALF_RANGE = 57.5  # (164 - 49) / 2
    spread_value = float(max(1, spread))
    normalized = SDF_MID + (signed_distance / spread_value) * SDF_HALF_RANGE
    return np.clip(normalized, 0, 255).astype(np.uint8)


def _render_glyph_bitmap(font: ImageFont.FreeTypeFont, char: str):
    """Render a single character and return (bitmap_array, off_x, off_y) or None."""
    try:
        mask = font.getmask(char, mode='L')
        w, h = mask.size
        if w <= 0 or h <= 0:
            return None
        bitmap = np.frombuffer(bytes(mask), dtype=np.uint8).reshape((h, w))
        bbox = font.getbbox(char)
        if bbox is None:
            return None
        return bitmap, bbox[0], bbox[1]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_patched_font(
    ttf_data: bytes,
    mono_raw: bytes,
    extra_codepoints: set[int] | None = None,
    sdf_spread: int = DEFAULT_SDF_SPREAD,
    log_fn=None,
) -> tuple[Image.Image, bytes]:
    """Generate a new SDF atlas that matches the existing MonoBehaviour layout.

    The MonoBehaviour binary is returned **unmodified**.  Each glyph is rendered
    from the replacement font and placed into the atlas at the exact rect
    specified by the original glyph table.  This preserves all of TMP's
    metrics, bearings, and advance widths so nothing is garbled.

    Args:
        ttf_data: Replacement TTF font bytes.
        mono_raw: Original MonoBehaviour raw bytes.
        extra_codepoints: (reserved for future use)
        sdf_spread: SDF spread in pixels.
        log_fn: Optional logging callback.

    Returns:
        (atlas_image, mono_raw_unchanged)
    """
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    # ------------------------------------------------------------------
    # Phase 0: Parse existing tables
    # ------------------------------------------------------------------
    _log("Parsing glyph table...")
    glyphs = parse_glyph_table(mono_raw)
    glyph_table_end = GLYPH_FIRST_OFF + len(glyphs) * GLYPH_STRIDE
    _log(f"  {len(glyphs)} glyphs")

    _log("Parsing character table...")
    char_count_off = find_char_table_offset(mono_raw)
    chars, char_first_off = parse_char_table(mono_raw, char_count_off)
    _log(f"  {len(chars)} characters")

    # Build glyph_index -> unicode
    glyph_to_unicode: dict[int, int] = {}
    for ch in chars:
        gidx = ch['glyph_index']
        if gidx not in glyph_to_unicode:
            glyph_to_unicode[gidx] = ch['unicode']

    # ------------------------------------------------------------------
    # Phase 1: Render all glyphs into scaled atlas
    # ------------------------------------------------------------------
    # Scale the atlas by ATLAS_SCALE (2x) and render each glyph into a
    # proportionally larger rect. This gives the SDF more pixels to work
    # with, resulting in crisper text.
    S = ATLAS_SCALE
    atlas_w = DEFAULT_ATLAS_SIZE * S
    atlas_h = DEFAULT_ATLAS_SIZE * S
    render_point = 89   # matches original rect sizing
    metric_point = 89   # same as render for consistent metrics
    render_size = render_point * S  # render at scaled size for real detail
    _log(f"  Render: {render_size}px, metrics: {metric_point}px, "
         f"atlas: {atlas_w}x{atlas_h} ({S}x)")

    font = ImageFont.truetype(io.BytesIO(ttf_data), size=render_size)
    ascent, _ = font.getmetrics()
    # Font at metric_point for computing advance/bearings
    font_metric = ImageFont.truetype(io.BytesIO(ttf_data), size=metric_point)
    ascent_metric, _ = font_metric.getmetrics()

    atlas_alpha = np.zeros((atlas_h, atlas_w), dtype=np.uint8)

    patched = bytearray(mono_raw)
    gap = DEFAULT_ATLAS_GAP
    outline = DEFAULT_OUTLINE * S
    pad_size = (sdf_spread * S) + outline

    # ------------------------------------------------------------------
    # Pre-phase: For orphaned chars (glyph not in table 1), assign them
    # to unused glyph slots so Phase 1 can render them normally.
    # ------------------------------------------------------------------
    glyph_idx_set = {g['index'] for g in glyphs}
    orphan_chars = []
    if extra_codepoints:
        for ch in chars:
            u = ch['unicode']
            if u in extra_codepoints and ch['glyph_index'] not in glyph_idx_set:
                orphan_chars.append(ch)

    if orphan_chars:
        # Find glyph slots in table 1 not needed by translations
        needed_glyph_indices = set()
        for ch in chars:
            if ch['unicode'] in (extra_codepoints or set()) and ch['glyph_index'] in glyph_idx_set:
                needed_glyph_indices.add(ch['glyph_index'])
        reusable_glyphs = [g for g in glyphs
                           if g['index'] not in needed_glyph_indices]

        orphan_unicodes_patched = set()
        for orphan_ch, donor_g in zip(orphan_chars, reusable_glyphs):
            u = orphan_ch['unicode']
            # Reassign this char to use the donor glyph slot
            orphan_ch['glyph_index'] = donor_g['index']
            # Update glyph_to_unicode so Phase 1 renders this glyph as this char
            glyph_to_unicode[donor_g['index']] = u
            # Patch the char record in the binary
            ci = next(i for i, c in enumerate(chars) if c is orphan_ch)
            char_off = char_first_off + ci * CHAR_STRIDE
            struct.pack_into('<fIII', patched, char_off, 1.0, 1, u, donor_g['index'])
            orphan_unicodes_patched.add(u)
        _log(f"  Reassigned {len(orphan_unicodes_patched)} orphaned chars to table-1 glyph slots")

    # ------------------------------------------------------------------
    # Unified rendering: render ALL glyphs at natural size and pack them
    # ------------------------------------------------------------------
    # Collect all glyphs to render (existing with rect + zero-rect needed)
    glyphs_to_render = []
    skipped = 0

    for glyph in glyphs:
        gidx = glyph['index']
        rect = glyph['rect']
        rw, rh = rect['w'], rect['h']
        unicode_val = glyph_to_unicode.get(gidx)

        if unicode_val is None or unicode_val < 32 or unicode_val > 0x10FFFF:
            skipped += 1
            continue

        # Only render glyphs whose unicode is needed by translations.
        # This avoids trying to render CJK chars with a non-CJK font.
        if extra_codepoints and unicode_val not in extra_codepoints:
            skipped += 1
            continue

        if rw <= 0 or rh <= 0:
            # Zero-rect: needed by translations, render it
            glyphs_to_render.append(glyph)
            continue

        # Existing glyph needed by translations — render
        glyphs_to_render.append(glyph)

    _log(f"  Rendering {len(glyphs_to_render)} glyphs...")

    # Row-based bin packing — all glyphs get natural-sized SDF tiles
    cursor_x = 0
    cursor_y = 0
    row_height = 0
    rendered = 0

    for glyph in glyphs_to_render:
        gidx = glyph['index']
        unicode_val = glyph_to_unicode.get(gidx)
        char = chr(unicode_val)

        # Render at full resolution
        result = _render_glyph_bitmap(font, char)
        if result is None:
            skipped += 1
            continue

        bitmap, _, _ = result

        # Pad for SDF
        padded_h = bitmap.shape[0] + 2 * pad_size
        padded_w = bitmap.shape[1] + 2 * pad_size
        padded = np.zeros((padded_h, padded_w), dtype=np.uint8)
        padded[pad_size:pad_size + bitmap.shape[0],
               pad_size:pad_size + bitmap.shape[1]] = bitmap

        sdf_tile = _compute_sdf_tile(padded, sdf_spread * S, outline=outline)
        tile_h, tile_w = sdf_tile.shape

        # Row-based packing
        if cursor_x + tile_w + gap > atlas_w:
            cursor_y += row_height + gap
            cursor_x = 0
            row_height = 0

        if cursor_y + tile_h > atlas_h:
            _log(f"  WARNING: Atlas full at glyph {gidx}, rendered {rendered}")
            break

        # Place in atlas
        atlas_alpha[cursor_y:cursor_y + tile_h,
                    cursor_x:cursor_x + tile_w] = sdf_tile

        # TMP Y is bottom-origin
        tmp_y = atlas_h - cursor_y - tile_h

        # Get metrics from metric font
        metric_result = _render_glyph_bitmap(font_metric, char)
        if metric_result is not None:
            m_bmp, m_off_x, m_off_y = metric_result
            met_w = float(m_bmp.shape[1])
            met_h = float(m_bmp.shape[0])
            bearing_x = float(m_off_x)
            bearing_y = float(ascent_metric - m_off_y)
        else:
            met_w = float(tile_w) / S
            met_h = float(tile_h) / S
            bearing_x = 0.0
            bearing_y = met_h
        advance = float(font_metric.getlength(char))

        # Patch MonoBehaviour: metrics + rect
        off = glyph['offset']
        struct.pack_into('<5f', patched, off + 4,
                         met_w, met_h,
                         bearing_x, bearing_y, advance)
        struct.pack_into('<4i', patched, off + 24,
                         cursor_x, tmp_y, tile_w, tile_h)

        cursor_x += tile_w + gap
        row_height = max(row_height, tile_h)
        rendered += 1

    _log(f"  Rendered {rendered} glyphs, skipped {skipped}")
    _log(f"  Atlas usage: {cursor_y + row_height} / {atlas_h} rows")

    # ------------------------------------------------------------------
    # Phase 2: Repurpose unused glyph+char slots for missing codepoints
    # ------------------------------------------------------------------
    # Instead of expanding the binary (which shifts trailing arrays and
    # crashes the game), overwrite existing entries that are not needed
    # by the current translation.
    existing_unicodes = {ch['unicode'] for ch in chars}
    needed_unicodes = extra_codepoints if extra_codepoints else set()
    glyph_idx_to_offset = {g['index']: g['offset'] for g in glyphs}

    # A codepoint needs repurposing if it either:
    # 1. Doesn't exist in the char table at all, OR
    # 2. Exists but its glyph is not in glyph table 1 (so it wasn't rendered)
    orphaned_unicodes = set()
    for ch in chars:
        u = ch['unicode']
        if u in needed_unicodes and ch['glyph_index'] not in glyph_idx_to_offset:
            orphaned_unicodes.add(u)

    missing_codepoints = sorted(
        cp for cp in needed_unicodes
        if cp >= 32 and cp <= 0x10FFFF
        and (cp not in existing_unicodes or cp in orphaned_unicodes)
    )

    if orphaned_unicodes:
        _log(f"  Found {len(orphaned_unicodes)} orphaned codepoints (glyph not in table 1)")
    if missing_codepoints:
        _log(f"  Repurposing slots for {len(missing_codepoints)} missing codepoints...")
        _log(f"    First 20: {[f'U+{cp:04X}' for cp in missing_codepoints[:20]]}")

        # Build map: unicode -> char table index
        unicode_to_char_idx = {ch['unicode']: i for i, ch in enumerate(chars)}

        # Find char entries whose unicode is NOT needed by translations
        # and whose glyph has a valid offset — these can be overwritten.
        reusable = []
        for i, ch in enumerate(chars):
            u = ch['unicode']
            if u not in needed_unicodes and u > 0x7F and u <= 0x10FFFF:
                glyph_off = glyph_idx_to_offset.get(ch['glyph_index'])
                if glyph_off is not None:
                    reusable.append((i, ch, glyph_off))

        if len(reusable) < len(missing_codepoints):
            _log(f"  WARNING: Only {len(reusable)} reusable slots for "
                 f"{len(missing_codepoints)} missing codepoints")

        new_rendered = 0

        for cp, (ci, old_ch, glyph_off) in zip(missing_codepoints, reusable):
            char = chr(cp)
            result = _render_glyph_bitmap(font, char)
            if result is None:
                continue

            bitmap, _, _ = result

            # SDF generation
            padded_h = bitmap.shape[0] + 2 * pad_size
            padded_w = bitmap.shape[1] + 2 * pad_size
            padded = np.zeros((padded_h, padded_w), dtype=np.uint8)
            padded[pad_size:pad_size + bitmap.shape[0],
                   pad_size:pad_size + bitmap.shape[1]] = bitmap
            sdf_tile = _compute_sdf_tile(padded, sdf_spread * S, outline=outline)
            tile_h, tile_w = sdf_tile.shape

            # Row-based packing (continue from where Phase 1 left off)
            if cursor_x + tile_w + gap > atlas_w:
                cursor_y += row_height + gap
                cursor_x = 0
                row_height = 0

            if cursor_y + tile_h > atlas_h:
                _log(f"  WARNING: Atlas full, could not add U+{cp:04X}")
                break

            atlas_alpha[cursor_y:cursor_y + tile_h,
                        cursor_x:cursor_x + tile_w] = sdf_tile

            tmp_y = atlas_h - cursor_y - tile_h

            # Metrics from metric font
            metric_result = _render_glyph_bitmap(font_metric, char)
            if metric_result is not None:
                m_bmp, m_off_x, m_off_y = metric_result
                met_w = float(m_bmp.shape[1])
                met_h = float(m_bmp.shape[0])
                bearing_x = float(m_off_x)
                bearing_y = float(ascent_metric - m_off_y)
            else:
                met_w = float(tile_w) / S
                met_h = float(tile_h) / S
                bearing_x = 0.0
                bearing_y = met_h
            advance = float(font_metric.getlength(char))

            # Overwrite the glyph record in-place (same offset, same size)
            glyph_idx = old_ch['glyph_index']
            struct.pack_into('<I', patched, glyph_off, glyph_idx)
            struct.pack_into('<5f', patched, glyph_off + 4,
                             met_w, met_h, bearing_x, bearing_y, advance)
            struct.pack_into('<4i', patched, glyph_off + 24,
                             cursor_x, tmp_y, tile_w, tile_h)

            # Overwrite the char record in-place (correct field order: scale, elem, unicode, glyph)
            char_off = char_first_off + ci * CHAR_STRIDE
            struct.pack_into('<fIII', patched, char_off, 1.0, 1, cp, glyph_idx)

            # For orphaned codepoints: also patch the ORIGINAL char entry
            # so TMP finds the correct glyph no matter which entry it reads first.
            if cp in orphaned_unicodes:
                orig_ci = unicode_to_char_idx.get(cp)
                if orig_ci is not None:
                    orig_off = char_first_off + orig_ci * CHAR_STRIDE
                    struct.pack_into('<fIII', patched, orig_off, 1.0, 1, cp, glyph_idx)

            cursor_x += tile_w + gap
            row_height = max(row_height, tile_h)
            new_rendered += 1

        _log(f"  Repurposed {new_rendered} slots for new codepoints")

    _log(f"  Final atlas usage: {cursor_y + row_height} / {atlas_h} rows")

    # ------------------------------------------------------------------
    # Phase 3: Patch face info + atlas dimensions in MonoBehaviour
    # ------------------------------------------------------------------
    # Face info from replacement font (at metric_point size)
    _ascent = float(ascent_metric)
    _descent_tuple = font_metric.getmetrics()
    _descent = -float(_descent_tuple[1])  # negative in TMP
    _line_height = _ascent - _descent
    _cap_height = _ascent * 0.7  # approximate cap height
    _underline_off = _descent * 0.5
    _underline_thick = float(metric_point) * 0.05
    _strike_off = _ascent * 0.3
    _strike_thick = _underline_thick
    _tab_width = float(font_metric.getlength(' ')) * 4

    _log(f"  Patching face info: pointSize={metric_point}, "
         f"lineHeight={_line_height:.1f}, ascender={_ascent:.1f}, "
         f"descender={_descent:.1f}")

    # Offsets in MonoBehaviour header (from binary inspection)
    struct.pack_into('<f', patched, 192, float(metric_point))   # m_PointSize
    struct.pack_into('<f', patched, 196, _line_height)           # m_LineHeight
    struct.pack_into('<f', patched, 200, _ascent)                # m_Ascender
    struct.pack_into('<f', patched, 204, _cap_height)            # m_CapHeight
    struct.pack_into('<f', patched, 212, _descent)               # m_Descender
    struct.pack_into('<f', patched, 216, _line_height)           # m_LineHeight (dup)
    struct.pack_into('<f', patched, 224, _descent)               # m_Descender (dup)
    struct.pack_into('<f', patched, 232, _underline_off)         # m_UnderlineOffset
    struct.pack_into('<f', patched, 236, _underline_thick)       # m_UnderlineThickness
    struct.pack_into('<f', patched, 240, _strike_off)            # m_StrikethroughOffset
    struct.pack_into('<f', patched, 244, _strike_thick)          # m_StrikethroughThickness
    struct.pack_into('<f', patched, 248, _tab_width)             # m_TabWidth

    _log(f"  Patching atlas dimensions: {DEFAULT_ATLAS_SIZE} -> {atlas_w}x{atlas_h}")
    char_table_end = char_first_off + len(chars) * CHAR_STRIDE
    # Search trailing data for all occurrences of the original atlas size (8192)
    # and replace them with the new dimensions (alternating width/height).
    target_val = struct.pack('<I', DEFAULT_ATLAS_SIZE)
    dim_patches = 0
    search_start = char_table_end
    for off in range(search_start, len(patched) - 3, 4):
        if patched[off:off + 4] == target_val:
            # Alternate: first=width, second=height, third=width, fourth=height
            new_val = atlas_w if dim_patches % 2 == 0 else atlas_h
            struct.pack_into('<I', patched, off, new_val)
            _log(f"    [{off}] {DEFAULT_ATLAS_SIZE} -> {new_val}")
            dim_patches += 1
    _log(f"    Patched {dim_patches} atlas dimension field(s)")

    # Create RGBA atlas image
    atlas_rgba = np.zeros((atlas_h, atlas_w, 4), dtype=np.uint8)
    atlas_rgba[:, :, 3] = atlas_alpha
    atlas_image = Image.fromarray(atlas_rgba, mode='RGBA')

    return atlas_image, bytes(patched)


# ---------------------------------------------------------------------------
# UnityPy helpers
# ---------------------------------------------------------------------------

def get_mono_object(env, target_name: str = "07LogoTypeGothic7 SDF"):
    """Find the TMP_FontAsset MonoBehaviour ObjectReader in a UnityPy environment."""
    atlas_file = None
    for obj in env.objects:
        if obj.type.name == "Texture2D":
            data = obj.read()
            if data.m_Name == f"{target_name} Atlas":
                atlas_file = obj.assets_file.name
                break

    best_obj = None
    best_size = 0
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            if atlas_file and obj.assets_file.name == atlas_file:
                if obj.byte_size > best_size:
                    best_size = obj.byte_size
                    best_obj = obj

    return best_obj


def get_mono_raw_bytes(env, target_name: str = "07LogoTypeGothic7 SDF") -> Optional[bytes]:
    """Extract the TMP_FontAsset MonoBehaviour raw bytes from a UnityPy environment."""
    obj = get_mono_object(env, target_name)
    if obj is None:
        return None
    return obj.get_raw_data()
