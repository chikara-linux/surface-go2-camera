#!/usr/bin/env python3
"""cam --file が吐く NV12 生データを PNG に変換する。
numpy も ffmpeg も不要（Pillow のみ）。純Python なので1フレーム数秒かかる。

使い方:  python3 nv12topng.py front.bin 1280 720 [フレーム番号...]
省略時は全フレームを変換する。
"""
import sys
from PIL import Image

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    path, W, H = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    fs = W * H * 3 // 2
    data = open(path, 'rb').read()
    n = len(data) // fs
    if n == 0:
        print("フレームが1枚も入っていない。サイズ指定が違う可能性がある。")
        return 1
    wanted = [int(a) for a in sys.argv[4:]] or range(n)
    print("%d フレーム検出 (%dx%d)" % (n, W, H))

    for idx in wanted:
        if idx >= n:
            continue
        f = data[idx * fs:(idx + 1) * fs]
        y, uv = f[:W * H], f[W * H:]
        if max(f) == 0:
            print("frame %d: 完全な黒。スキップ" % idx)
            continue
        out = bytearray(W * H * 3)
        for j in range(H):
            row = (j >> 1) * (W // 2) * 2
            for i in range(W):
                Y = y[j * W + i]
                o = row + (i >> 1) * 2
                U, V = uv[o] - 128, uv[o + 1] - 128
                r = Y + 1.402 * V
                g = Y - 0.344136 * U - 0.714136 * V
                b = Y + 1.772 * U
                p = (j * W + i) * 3
                out[p]     = 0 if r < 0 else (255 if r > 255 else int(r))
                out[p + 1] = 0 if g < 0 else (255 if g > 255 else int(g))
                out[p + 2] = 0 if b < 0 else (255 if b > 255 else int(b))
        name = "%s_frame%d.png" % (path.rsplit('.', 1)[0], idx)
        Image.frombytes('RGB', (W, H), bytes(out)).save(name)
        print("frame %d -> %s" % (idx, name))
    return 0

sys.exit(main())
