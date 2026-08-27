#!/usr/bin/env python3
"""CIO2 の IPU3 パック 10bit raw (ip3b 等) をグレースケール PNG に復元する。
32バイトに 10bit 画素が 25個 詰まっている。numpy 不要（Pillow のみ）。

使い方: python3 decode_ipu3_raw.py <file> <width> <height> [間引き]
  width/height はセンサーモードの値（例 3264 2448 / 1632 1224）
  間引きは既定 4（速度のため縦横を間引く）

行長は cio2_bytesperline(): DIV_ROUND_UP(width, 50) * 64 で決まる。
"""
import sys, math
from PIL import Image

def main():
    if len(sys.argv) < 4:
        print(__doc__); return 1
    path, W, H = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    step = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    BPL = math.ceil(W / 50) * 64
    groups = BPL // 32
    d = open(path, 'rb').read()
    print("行長 %d バイト / %d グループ, ファイル %d バイト (%.2f フレーム)"
          % (BPL, groups, len(d), len(d) / (BPL * H)))
    rows = []
    for y in range(0, H, step):
        off = y * BPL
        if off + BPL > len(d):
            break
        line = []
        for g in range(0, groups, step):
            blk = int.from_bytes(d[off + g * 32: off + g * 32 + 32], 'little')
            for k in range(25):
                line.append((blk >> (10 * k)) & 0x3FF)
        rows.append(line)
    if not rows:
        print("フレームが読めない"); return 1
    flat = [v for r in rows for v in r]
    print("復元 %dx%d  10bit min=%d max=%d mean=%.1f"
          % (len(rows[0]), len(rows), min(flat), max(flat), sum(flat) / len(flat)))
    hi = sorted(flat)[int(len(flat) * 0.99)] or 1
    w, h = len(rows[0]), len(rows)
    px = bytearray(w * h)
    for i, v in enumerate(flat):
        s = int(v * 255 / hi)
        px[i] = 255 if s > 255 else s
    out = path.rsplit('.', 1)[0] + '_decoded.png'
    Image.frombytes('L', (w, h), bytes(px)).save(out)
    print("→", out, "(99%点", hi, "で正規化)")
    return 0

sys.exit(main())
