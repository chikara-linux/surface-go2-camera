# surface-go2-camera

Surface Go 2 (Model 1927) の IPU3 カメラを Linux で実用にするための一式。
Kubuntu 26.04 / kernel 7.0.0-30-generic（linux-surface ではなく Ubuntu 標準）で確認。

素の状態では `cam --list` にカメラが1台も出ない。ここには原因を追った記録と、
実際に動くようにするためのドライバパッチ・設定・専用アプリが入っている。

IR カメラと赤外線照明も動く。**照明のほうは上流に実装が無い**ため、
[dkms/tps68470-irled](dkms/tps68470-irled) に自作のカーネルモジュールを置いている。

## 現在の状態

| | 状態 |
|---|---|
| 前面 (OV5693) | 実用可能。向き・色・明るさとも調整済み |
| 背面 (OV8865) | 専用アプリから実用可能。AF も効く |
| IR (OV7251) | 実用可能。赤外線照明も点灯する |
| 赤外線照明 | **点灯する。上流未実装** |
| 顔認証 | 画面ロックの解除に使用可能 |

## 見つけたドライバの不具合

いずれも実機で切り分けたもの。詳細と根拠は [docs/investigation.md](docs/investigation.md)。

**dw9719 に `i2c_device_id` テーブルが無い** — v6.19 で失われ v7.0 でも未修正。
ACPI 経由で作られる VCM デバイスにバインドできず、async notifier が完了せず、
**カメラが1台も列挙されない**。VCM を使わない前面・IR まで巻き添えになる。

> ⚠️ git 履歴から v6.18 のテーブルをそのまま復元すると `driver_data` が `0` になり、
> v6.19 で追加された `DW9718S` に化ける。probe がチップ検出を飛ばして別チップの
> レジスタ配置を書き込むので、バインドしないより悪い。`driver_data` を明示すること。

**ov5693 のモジュールが反転実装** — 映像が左右反転する。反転すると Bayer 位相が
1画素ずれるが、IPU3 パイプラインは `bayerOrder()` を呼ばないので libcamera 側では
補正されない。ドライバで HFLIP の意味を反転し、報告する Bayer 配列を追従させた。

**ov8865 の analogue_gain が 16 段しかない** — ドライバが `step=128` と宣言していた。
レジスタは 13bit で 128 が 1.0 倍、つまりハードは 1/128 刻みで 1921 段いける。
AGC が中間値を要求してもハードが出せず、**明暗が約1.2秒周期で往復する**。
`step` を 1 にするだけで止まる（振れ幅 0.51 → 0.008）。

## 中身

```
dkms/dw9719-fix/     カメラが1台も認識されない問題
dkms/ov5693-fix/     前面の左右反転と Bayer 配列
dkms/ov8865-fix/     背面の AE ハンチング（ゲイン粒度）
tuning/              libcamera IPU3 の AGC 目標輝度（既定 0.16 は暗すぎる）
wireplumber/         壊れたモードを踏む背面を PipeWire から隠すルール
app/                 専用カメラアプリ surface-camera（Kirigami / PyQt6）
tools/               NV12/raw の復元、各種切り分けスクリプト
docs/                調査記録
```

各 `dkms/*/README.txt` に、その修正の原因・根拠・検証手順と、
**試して駄目だった案とその理由**を書いてある。

## IR カメラと赤外線照明

| | |
|---|---|
| [dkms/tps68470-irled](dkms/tps68470-irled) | 赤外線照明を LED クラスデバイスとして公開する |
| [ir-camera](ir-camera) | IR カメラの読み出し層と、libcamera から見えない理由 |
| [face-auth](face-auth) | 顔認証（Windows Hello 相当）の組み込み |

赤外線照明は TPS68470 PMIC 内蔵の **WLED（フラッシュ）出力**に繋がっている。
レジスタは TI のデータシートで公開されており、ドライバ (`leds-tps68470.c`) も
2023 年に書かれているが、**mainline に取り込まれていない**
([LWN](https://lwn.net/Articles/926867/))。そのため
`include/linux/mfd/tps68470.h` にも該当領域 (0x28-0x3A) の定義が無く、
Linux には点ける手段が存在しない状態が続いている。

なお linux-surface の issue #739 はセンサー側のストロボを扱っているが、
**そちらを追っても光らない**。詳細は
[dkms/tps68470-irled/README.txt](dkms/tps68470-irled/README.txt)。

## 導入

```bash
sudo apt install dkms
for d in dw9719-fix ov5693-fix ov8865-fix; do
  v=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' dkms/$d/dkms.conf)
  sudo cp -r dkms/$d /usr/src/$d-$v
  sudo dkms install -m $d -v $v
done
sudo cp tuning/*.yaml /usr/share/libcamera/ipa/ipu3/
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cp wireplumber/*.conf ~/.config/wireplumber/wireplumber.conf.d/
# 再起動
```

アプリは `app/packaging/` から `dpkg-deb --build --root-owner-group` で組める。

## なぜ専用アプリが要るのか

**PipeWire 経由では 1280x720 が上限。** SPA プラグインが view-finder ロールで
ノードを作るため、ポータル経由のアプリはそこで頭打ちになる。
`libcamerasrc` のパッドに `stream-role=still-capture` を指定すると
**2560x1920** まで使える。副産物として、PipeWire を経由しないので
WirePlumber で隠してある背面カメラもアプリからは使える。

UI は Kirigami、映像は appsink で受けた NV12 を QVideoFrame に包んで
QML の VideoOutput に流している。**GL 経路は意図的に避けている** —
`qml6glsink` は見た目には動くが、設定変更のたびに GL コンテキストが累積し
4〜5 回で abort する。libcamerasrc の要素を使い回すのも駄目で、
カメラを開き直すと `libgstlibcamera` が停止済みのリクエストに触れて落ちる。
設定変更のたびに要素ごと作り直し、GL を使わない構成だけが安定した
（切替 19 回で確認）。詳細は `app/packaging/usr/share/doc/surface-camera/README`。

## 撤去

すべて元に戻せる。手順は [docs/investigation.md](docs/investigation.md) の §10。

## ライセンス

`dkms/*/[a-z]*.c` は Linux カーネルのソースに手を入れたもので GPL-2.0。
それ以外（アプリ・スクリプト・記録）は MIT とする。
