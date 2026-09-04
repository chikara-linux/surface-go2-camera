# IR カメラ (OV7251)

Surface Go 2 の赤外線カメラを Linux で使うための読み出し層。

linux-surface の wiki では全機種 🚫 だが、**libcamera を経由せず V4L2 を
直接叩けば映る**。ここにはその手順と、実際に動くリーダーが入っている。

## libcamera から見えない理由

センサー固有の問題ではなく、構造的な上限。

```c
// src/libcamera/pipeline/ipu3/ipu3.cpp
for (unsigned int id = 0; id < 4 && numCameras < 2; ++id)
```

**ImgU が2基しかないため、3本目のセンサーに到達しない。**
`LIBCAMERA_LOG_LEVELS=*:DEBUG cam --list` を取ると、csi2 0 と 1 の登録後に
走査が終わり、`ov7251` という文字列がログに一度も出ないことが確認できる。

加えて OV7251 は `Y10`（モノクロ）で、IPU3 の Bayer 前提のフォーマット表にも
無い。いずれにせよ libcamera 側での解決は望み薄。

## 必要な3つの処理

V4L2 で直接読めるが、そのままでは使えない。

1. **リンクとフォーマットの設定**。`media-ctl` で毎回設定する。再起動で消える
2. **`ip3y` の展開**。10bit モノクロ IPU3 パック形式で、32バイト = 25画素。
   OpenCV も ffmpeg も解釈できない
3. **自動露出**。センサーは持っていない。既定のゲイン 16 では真っ黒

`ipu3_ir_reader.py` がこの3つを担い、`cv2.VideoCapture` と同じ
`grab` / `read` / `release` を提供する。

## 最短の再現手順

```bash
M=/dev/media1      # cio2 を持つ media デバイス。番号は起動ごとに変わる
media-ctl -d $M -l '"ov7251 3-0060":0 -> "ipu3-csi2 2":0 [1]'
for P in '"ov7251 3-0060":0' '"ipu3-csi2 2":0' '"ipu3-csi2 2":1'; do
  media-ctl -d $M -V "$P [fmt:Y10_1X10/640x480]"
done
v4l2-ctl -d /dev/v4l-subdev8 --set-ctrl=gain=1023 --set-ctrl=exposure=1700
v4l2-ctl -d /dev/video12 --set-fmt-video=width=640,height=480,pixelformat=ip3y \
  --stream-mmap --stream-count=10 --stream-to=ir.raw
```

**⚠️ デバイス番号は起動ごとに変わる。** 実際、再起動で `media0` が imgu、
`media1` が cio2 に入れ替わり、video ノードも `/dev/video2` から
`/dev/video12` に変わった。決め打ちすると動かなくなる。
リーダーは毎回トポロジから解決している。

1フレームは `DIV_ROUND_UP(640,50)*64 * 480 = 399,360` バイト。

## 露出の落とし穴

`exposure` の上限は probe 時の値のまま頭打ちになる。
**`vertical_blanking` を明示的に「書く」と再計算される。**

| vblank | 露出上限 | 概算 fps |
|---|---|---|
| 1244（既定） | 1704〜3684 | 30 |
| 5000 | 5460 | 9 |
| 10000 | 10460 | 5 |

暗所ではこれで光量を稼げる。ただし露出が長いほどブレる。

## 発光体がある場合とない場合

[../dkms/tps68470-irled](../dkms/tps68470-irled) で赤外線照明が使える場合、
必要な処理が変わる。**暗所向けの対策をそのまま残すと、かえって精度が落ちる。**

| 処理 | 発光体なし | 発光体あり |
|---|---|---|
| CLAHE | 必須 | **有害・無効化** |
| ぼかし | 必須 | 不要 |
| 時間平均 | 12〜16枚 | 3枚 |
| 露出 | 上限 1704 | 上限 800 |
| ゲイン | 1023（張り付き） | 96 前後 |
| 測光 | 画面全体 | **中央領域** |

リーダーは点灯状態を検出して自動的に切り替える。

**測光方式は特に重要。** 発光体を使うと顔だけが明るく背景は真っ黒になるため、
画面全体の平均で測ると黒い背景に引きずられて目標に永久に届かず、
ゲインが上がり続けて顔が白飛びする。30秒で検出不能になった。
中央領域で測り、飽和率 8% を超えたら露出を下げるガードを入れている。

## 依存

`v4l-utils` `python3-numpy` `python3-opencv`
