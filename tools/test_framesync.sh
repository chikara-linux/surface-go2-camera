#!/bin/bash
# 停止時にセンサーがフレームを送り続けているかを frame_sync イベントで判定する。
# 使い方: test_framesync.sh <width> <height>
set -u
W=${1:-1632}; H=${2:-1224}
CIO2=$(for m in /dev/media*; do media-ctl -p -d $m 2>/dev/null | grep -q 'ipu3-cio2' && echo $m; done)
VID=$(media-ctl -p -d $CIO2 2>/dev/null | grep -A3 'entity.*ipu3-cio2 0' | grep -oE '/dev/video[0-9]+' | head -1)
CSI=$(media-ctl -p -d $CIO2 2>/dev/null | grep -A3 'entity.*ipu3-csi2 0' | grep -oE '/dev/v4l-subdev[0-9]+' | head -1)
echo "cio2=$CIO2  video=$VID  csi2=$CSI  mode=${W}x${H}"

media-ctl -d $CIO2 -V "\"ov8865 2-0010\":0 [fmt:SBGGR10_1X10/${W}x${H}]" >/dev/null 2>&1
media-ctl -d $CIO2 -V "\"ipu3-csi2 0\":0 [fmt:SBGGR10_1X10/${W}x${H}]" >/dev/null 2>&1
media-ctl -d $CIO2 -V "\"ipu3-csi2 0\":1 [fmt:SBGGR10_1X10/${W}x${H}]" >/dev/null 2>&1
v4l2-ctl -d $VID --set-fmt-video=width=$W,height=$H,pixelformat=ip3b >/dev/null 2>&1

D=$(mktemp -d)
# ストリームを背後で開始（多めに要求して止まっても待たせる）
timeout 20 v4l2-ctl -d $VID --stream-mmap --stream-count=60 --stream-to=$D/s.bin >$D/stream.log 2>&1 &
SPID=$!
sleep 1
# frame_sync を 12 秒間ポーリングして数える
timeout 12 v4l2-ctl -d $CSI --poll-for-event=frame_sync >$D/ev.log 2>&1
wait $SPID 2>/dev/null

N=$(grep -ci 'frame_sync\|sequence' $D/ev.log 2>/dev/null || echo 0)
SEQ=$(grep -oE 'sequence: *[0-9]+' $D/ev.log | tail -1)
GOT=$(stat -c%s $D/s.bin 2>/dev/null || echo 0)
SI=$(v4l2-ctl -d $VID --get-fmt-video 2>/dev/null | grep -oE 'Size Image *: [0-9]+' | grep -oE '[0-9]+$')
echo "  frame_sync イベント数: $N   最終 $SEQ"
echo "  取得バイト: $GOT  (= $(python3 -c "print('%.2f'%($GOT/$SI))" 2>/dev/null) フレーム)"
echo "  --- イベントログ冒頭 ---"; head -6 $D/ev.log
rm -rf $D
