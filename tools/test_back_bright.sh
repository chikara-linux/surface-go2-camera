#!/bin/bash
# リアカメラ (OV8865) の回避策を明るい場所で検証する。
# 1632x1224 モードは1フレームで停止するため、2048x1536 を要求して
# フル解像度センサーモード (3264x2448) を選ばせる。詳細は hikitugi §9-1。
set -u
cd "$(mktemp -d)" || exit 1
echo "作業ディレクトリ: $PWD"

W=2048; H=1536; N=90
echo "リアカメラから ${W}x${H} で ${N} フレーム取得中（約10秒）..."
timeout 120 cam -c1 -s width=$W,height=$H --capture=$N --metadata --file=back.bin > meta.txt 2>&1

if [ ! -s back.bin ]; then
  echo "❌ フレームが取得できなかった"
  grep -vE 'INFO |WARN ' meta.txt | head -5
  exit 1
fi

echo "--- 収束後の露出/ゲイン ---"
grep -oE '(ExposureTime|AnalogueGain) = [0-9.]+' meta.txt | tail -2
echo "--- payload 警告の有無 ---"
journalctl -k -b --since "2 min ago" 2>/dev/null | grep -c 'payload length' \
  | xargs -I{} echo "  payload 警告 {} 件（0 なら正常）"

python3 - "$W" "$H" <<'PY'
import sys
from PIL import Image
W,H=int(sys.argv[1]),int(sys.argv[2]); fs=W*H*3//2
d=open('back.bin','rb').read(); n=len(d)//fs
print("--- 取得フレーム数 %d（90 なら停止せず完走）---" % n)
for i in (0, n//3, 2*n//3, n-1):
    y=d[i*fs:i*fs+W*H]
    print("  frame %2d: 平均輝度 %6.1f  max %3d" % (i, sum(y)/len(y), max(y)))
# 最終フレームを 1/2 に間引いて PNG 化（純Python なので間引いて高速化）
i=n-1; f=d[i*fs:(i+1)*fs]; y,uv=f[:W*H],f[W*H:]
ow,oh=W//2,H//2
out=bytearray(ow*oh*3)
for j in range(oh):
    sy=j*2; row=(sy>>1)*(W//2)*2
    for x in range(ow):
        sx=x*2; Y=y[sy*W+sx]; o=row+(sx>>1)*2
        U,V=uv[o]-128,uv[o+1]-128
        r=Y+1.402*V; g=Y-0.344136*U-0.714136*V; b=Y+1.772*U
        p=(j*ow+x)*3
        out[p]  =0 if r<0 else (255 if r>255 else int(r))
        out[p+1]=0 if g<0 else (255 if g>255 else int(g))
        out[p+2]=0 if b<0 else (255 if b>255 else int(b))
import os
dst=os.path.expanduser('tools/back_bright_result.png')
Image.frombytes('RGB',(ow,oh),bytes(out)).save(dst)
print("→", dst)
PY
