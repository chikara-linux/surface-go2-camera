#!/bin/bash
# 左右反転で色が崩れるかを切り分ける。
# 通常 → mirror → 通常 の順に撮り、1回目と3回目が一致すればシーンは安定していたと言える。
# その条件下で mirror だけ色が違えば、反転が原因と確定できる。
set -u
D=$(mktemp -d); cd "$D" || exit 1
OUT=~/camera-fix-evidence
CAM=${1:-2}; W=${2:-1280}; H=${3:-720}

shot() {  # $1=向き $2=ラベル
  local o=""; [ "$1" != "none" ] && o="-o $1"
  timeout 120 cam -c$CAM -s width=$W,height=$H $o --capture=45 --metadata --file=f.bin > m.txt 2>&1
  local cg=$(grep -oE 'ColourGains = \[ [0-9.]+, [0-9.]+ \]' m.txt | tail -1)
  local ct=$(grep -oE 'ColourTemperature = [0-9]+' m.txt | tail -1)
  local ex=$(grep -oE 'ExposureTime = [0-9]+' m.txt | tail -1)
  local ag=$(grep -oE 'AnalogueGain = [0-9.]+' m.txt | tail -1)
  echo "  $ct / $ex / $ag"
  echo "  $cg"
  python3 - "$W" "$H" "$OUT/color_$2.png" "$1" <<'PY'
import sys
from PIL import Image, ImageOps
W,H,dst,orient=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3],sys.argv[4]
fs=W*H*3//2; d=open('f.bin','rb').read(); n=len(d)//fs
if not n: print("  取得失敗"); raise SystemExit
f=d[(n-1)*fs:n*fs]; y,uv=f[:W*H],f[W*H:]
ow,oh=W//2,H//2; out=bytearray(ow*oh*3)
for j in range(oh):
    sy=j*2; row=(sy>>1)*(W//2)*2
    for x in range(ow):
        sx=x*2; Y=y[sy*W+sx]; o=row+(sx>>1)*2
        U,V=uv[o]-128,uv[o+1]-128
        r=Y+1.402*V; g=Y-0.344136*U-0.714136*V; b=Y+1.772*U
        p=(j*ow+x)*3
        out[p]=0 if r<0 else (255 if r>255 else int(r))
        out[p+1]=0 if g<0 else (255 if g>255 else int(g))
        out[p+2]=0 if b<0 else (255 if b>255 else int(b))
im=Image.frombytes('RGB',(ow,oh),bytes(out))
im.save(dst)
# mirror 版は見た目を揃えるため戻してから統計を取る（色は反転で変わらない）
st=ImageOps.mirror(im) if orient=='mirror' else im
px=list(st.getdata()); k=len(px)
r=sum(p[0] for p in px)/k; g=sum(p[1] for p in px)/k; b=sum(p[2] for p in px)/k
print("  平均RGB (%5.1f, %5.1f, %5.1f)   R/G %.3f  B/G %.3f" % (r,g,b, r/g if g else 0, b/g if g else 0))
PY
  rm -f f.bin m.txt
}
echo "カメラ $CAM / ${W}x${H}"
echo "[1/3] 通常 (1回目)";  shot none   normal1
echo "[2/3] mirror";        shot mirror mirror
echo "[3/3] 通常 (2回目)";  shot none   normal2
rm -rf "$D"
echo
echo "判定: 通常1と通常2 の R/G・B/G が近ければシーンは安定。"
echo "      その上で mirror だけずれていれば反転が原因。3つとも近ければ前回の差は被写体変化。"
