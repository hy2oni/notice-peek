"""Generate the small N app icon with no image-library dependency."""

from pathlib import Path
import struct


def color_at(x, y):
    # Rounded blue square, white N. Coordinates are relative to icon size.
    dx, dy = max(abs(x - 0.5) - 0.31, 0), max(abs(y - 0.5) - 0.31, 0)
    if dx * dx + dy * dy > 0.16 * 0.16:
        return (0, 0, 0, 0)
    if 0.25 <= y <= 0.75 and (0.25 <= x <= 0.35 or 0.65 <= x <= 0.75 or
                              abs(x - (0.3 + (y - 0.25) * 0.8)) < 0.055):
        return (255, 255, 255, 255)
    return (36, 93, 181, 255)


def frame(size):
    pixels = bytearray()
    mask = bytearray()
    for y in reversed(range(size)):
        mask_row = bytearray(((size + 31) // 32) * 4)
        for x in range(size):
            samples = [color_at((x + (sx + 0.5) / 4) / size, (y + (sy + 0.5) / 4) / size)
                       for sy in range(4) for sx in range(4)]
            visible = [s for s in samples if s[3]]
            if visible:
                r, g, b = (round(sum(s[i] for s in visible) / len(visible)) for i in range(3))
                a = round(255 * len(visible) / 16)
            else:
                r = g = b = a = 0
                mask_row[x // 8] |= 1 << (7 - x % 8)
            pixels.extend((b, g, r, a))
        mask.extend(mask_row)
    header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    return header + pixels + mask


def main():
    sizes = (16, 32, 48, 64)
    frames = [frame(s) for s in sizes]
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    for size, bitmap in zip(sizes, frames):
        entries.extend(struct.pack('<BBBBHHII', size, size, 0, 0, 1, 32, len(bitmap), offset))
        offset += len(bitmap)
    target = Path(__file__).resolve().parents[1] / 'notice_peek' / 'assets' / 'notice-peek.ico'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + entries + b''.join(frames))
    print(target)


if __name__ == '__main__':
    main()
