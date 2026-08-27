# Surface Go 2 カメラ有効化 — 記録【フロントカメラ実用化まで完了】

> このファイルは実機で作業しながら書いた記録です。時系列の判断や、
> 途中で外した仮説とその理由もそのまま残してあります。
> 環境は Surface Go 2 (Model 1927) / Kubuntu 26.04 / kernel 7.0.0-30-generic
> （linux-surface カーネルではなく Ubuntu 標準）です。

Kubuntu 26.04 / カーネル 7.0.0-30-generic (Ubuntu標準、linux-surfaceカーネル不使用) の
Surface Go 2 (Model 1927) で、IPU3カメラが `cam --list` に一切現れなかった問題の記録。

**2026-08-26 に、原因特定からブラウザで使える状態までを一日で完了した。**

| 項目 | 状態 |
|---|---|
| フロントカメラ (OV5693) | ✅ **実用可能。** 向き・色とも正常（§9-1 で反転を修正済み） |
| リアカメラ (OV8865) | ✅ 専用アプリから実用可能。AF も効く。既定モードでは停止するため PipeWire からは非表示 |
| IR カメラ (OV7251) | ⏸ スコープ外 |

「検証済みの事実」と「未検証の見込み」は区別して書いてある。後者を事実として扱わないこと。

---

## 1. 何が壊れていて、何をしたか

壊れていたのは **dw9719 VCM ドライバ 1箇所**だった。それ以外の層はすべて正常。

| 層 | 状態 | 根拠 |
|---|---|---|
| IPU3ファームウェア | ✅ | `loaded firmware version irci_...` |
| ipu3-cio2 / ipu_bridge | ✅ | `Connected 3 cameras` |
| int3472 (カメラPMIC) | ✅ | `TPS68470 REVID: 0x21` |
| センサードライバ 3種 | ✅ | ov5693 / ov8865 / ov7251 |
| **dw9719 VCM** | ✅ 修正済み | `i2c_device_id` テーブルを追加。§3・§4 |
| async notifier complete | ✅ | csi2 に device node、センサーに ENABLED リンク |
| libcamera 0.7.0 | ✅ | 前後2台を認識 |
| PipeWire / ポータル連携 | ✅ | 追加設定不要で動いた。§7 |
| Vivaldi からの利用 | ✅ | フラグ1つ。§7 |
| 明るさ | ✅ 調整済み | AGC 目標輝度を変更。§8 |
| 左右の向き | ✅ フロントは修正済み | リアは未対応。§9-1 |
| リアカメラの映像 | ⚠️ モード依存 | フル解像度モードなら実写できる。§9-2 |

実施した恒久的な変更は3つだけ:

1. **DKMS 3本** — dw9719（§4）、ov5693 の向き補正（§9-1）、ov8865 のゲイン粒度（§9-2b）
2. **WirePlumber でリアカメラを非表示化**（§7.3）
3. **libcamera の IPU3 チューニングファイルを追加**（§8）— ov5693.yaml / ov8865.yaml とも target 0.40
4. **専用カメラアプリ surface-camera**（§8.5）

いずれも1コマンドで撤去でき、起動を壊すリスクは無い。撤去方法は §10 にまとめてある。

---

## 2. 症状（修正前の記録）

```
$ cam --list
[...] INFO Camera camera_manager.cpp:340 libcamera v0.7.0
Available cameras:
```

デバッグログの決定的な1行:

```
DEBUG DeviceEnumerator device_enumerator.cpp:118 Skip ipu3-csi2 0: no device node
```

`media-ctl -p` (cio2側) の要点:

- `ipu3-csi2 0`〜`3`: device node name が無い
- `ov5693 4-0036` / `ov8865 2-0010` / `ov7251 3-0060`: すべて `0 link`
- csi2 → cio2 のリンクのみ `ENABLED,IMMUTABLE` で正常

⚠️ **media ノードの番号は起動ごとに入れ替わる。** 固定だと思ってはいけない。
調査中、ある起動では media1 = cio2、再起動後は media0 = cio2 になった。毎回こう特定すること:

```bash
for m in /dev/media*; do
  media-ctl -p -d $m 2>/dev/null | grep -q 'ipu3-cio2' && echo "cio2 = $m"
done
```

---

## 3. 原因（確定）

### 3.1 メカニズム

ipu3-cio2 は、**すべての非同期サブデバイスが揃った時点（notifier complete）で初めて**
「センサー → csi2 のリンク作成」と「csi2 subdevノードの登録」を行う。

- ov8865 は自身のサブ notifier で VCM (dw9719) を待つ
- dw9719 が probe されない → ov8865 のサブ notifier が完了しない
- → 親（cio2）の complete が永久に来ない
- → **リア用部品の不在でフロント・IRも道連れで死ぬ**

### 3.2 なぜ dw9719 が probe されなかったか

デバイス側の modalias は `i2c:dw9719`。しかしモジュールが公開するエイリアスは `of:*` のみだった。
`drivers/media/i2c/dw9719.c` の `struct i2c_driver` に `.of_match_table` しか無く、
`.id_table` と `MODULE_DEVICE_TABLE(i2c, ...)` が存在しなかったため。

1. **自動ロードされない** — modalias `i2c:dw9719` に対応するモジュールが無い
2. **手動ロード後もbindしない** — `id_table` が無く、この機体には OF ノードも ACPI コンパニオンも
   無い（ov8865 が `i2c_new_client_device()` で動的に作ったデバイスのため）のでマッチ手段がゼロ

### 3.3 試して駄目だったこと（再試行不要）

| 試行 | 結果 |
|---|---|
| `modprobe dw9719` | ロードは成功するが自動bindしない |
| `echo ... > .../dw9719/bind` | `No such device` (ENODEV)。現行カーネルの `bind_store` は書き込み時にも `driver_match_device()` を通すため、強制bindはマッチ機構を迂回しない |
| `.../dw9719/new_id` | 存在しない。i2cバスは動的ID追加を実装していない |
| `/sys/kernel/debug/devices_deferred` | 空。probe待ちですらない |

→ ゼロビルドの逃げ道は無く、モジュールの再ビルドが唯一の道だった。

### 3.4 上流の状況（タグ単位で検証済み）

| タグ | `MODULE_DEVICE_TABLE(i2c, ...)` |
|---|---|
| v6.17 | あり |
| v6.18 | あり |
| v6.19 | **なし** |
| v7.0 | **なし** |

linux-surface wiki の記載（6.18→6.19 のリグレッション）は正しく、**カーネル 7.0 でも未修正**。
wiki は Surface Pro 5 文脈だが、原因はドライバ側なので機種非依存。

v6.19 での変更は DW9718S / DW9800K のサポート追加と OF マッチングの導入で、
その過程で i2c テーブルが巻き添えで削除されている。これが §4.2 の落とし穴に直結する。

参考:
- https://github.com/EberhartLeberhart/surface-pro5-just-works （SP5向け、独語）
- https://github.com/linux-surface/linux-surface/discussions/1352

---

## 4. 対処: DKMS による dw9719 差し替え

### 4.1 ソース取得

`apt source linux` は数GBあり 128GB SSD / 5W CPU には重い。**1ファイルだけで足りる。**

```bash
curl -sSLO https://raw.githubusercontent.com/torvalds/linux/v7.0/drivers/media/i2c/dw9719.c
```

**取得したソースが Ubuntu の配布バイナリと一致することを srcversion で確認すること**（重要）。
無改変でビルドした `.ko` と配布物の srcversion がどちらも `BFB5DD8610548B09B7CD8FD` で一致した。
Ubuntu はこのファイルに独自パッチを当てていないので、upstream v7.0 をそのまま土台にしてよい。

### 4.2 ⚠️ パッチ内容の落とし穴（最重要）

**git 履歴から v6.18 のテーブルをそのまま復元してはいけない。** v6.18 のテーブルはこう:

```c
static const struct i2c_device_id dw9719_id_table[] = {
	{ "dw9719" },
	{ "dw9761" },
	{ }
};
```

`driver_data` が省略されている＝`0`。これは v6.18 では無害だった。当時の enum は
`DW9719 = 0, DW9761 = 1` で、しかも model は match data ではなく INFO レジスタの読み取りだけで
決まっていた（v6.18 に `i2c_get_match_data()` の呼び出しが無い）。

**v6.19 以降では壊れる。** enum が拡張され `DW9718S` が 0 になった:

```c
enum dw9719_model {
	DW9718S,	/* = 0 */
	DW9719,
	DW9761,
	DW9800K,
};
```

probe は `dw9719->model = i2c_get_match_data(client)` でモデルを決め、`DW9718S` の場合は
**チップID検出を丸ごと飛ばして**（`goto props`）DW9718S 用のレジスタ配置
（`DW9718S_PD` / `DW9718S_CONTROL` / `DW9718S_VCM_CURRENT`）を書きにいく。

つまり `{ "dw9719" }` と書くと **bind はするが間違ったレジスタで VCM を叩く**状態になり、
bind しないより悪い。しかもエラーが出ないので気づきにくい。

### 4.3 適用したパッチ

`driver_data` を明示する。これで probe は `default:` を通って `DW9719_INFO` を読み、
チップ自身に型を名乗らせる。

```c
static const struct i2c_device_id dw9719_id_table[] = {
	{ "dw9718s", DW9718S },
	{ "dw9719",  DW9719 },
	{ "dw9761",  DW9761 },
	{ "dw9800k", DW9800K },
	{ }
};
MODULE_DEVICE_TABLE(i2c, dw9719_id_table);
```

加えて `struct i2c_driver` に `.id_table = dw9719_id_table,` を追加
（`.driver.of_match_table` は残したまま。OF 側の経路を壊さないこと）。

差分は `/usr/src/dw9719-fix-1.0/i2c-id-table.patch` に保存済み。

### 4.4 DKMS 構成

`.ko.zst` を直接リネームして上書きする方法は**採らない**。in-tree のモジュールを
壊すと復旧が面倒で、内蔵デバイスが使えなくなる事故になりうる。DKMS は
`/lib/modules/<ver>/updates/dkms/` という正規の優先パスに置くので、性質が異なる。

```
/usr/src/dw9719-fix-1.0/
├── dkms.conf
├── Makefile
├── dw9719.c              # 修正済みソース
├── i2c-id-table.patch    # 差分の記録
└── dw9719-patched.ko     # 事前ビルド版。DKMS は使わないので削除して構わない
```

`dkms.conf`:

```
PACKAGE_NAME="dw9719-fix"
PACKAGE_VERSION="1.0"

BUILT_MODULE_NAME[0]="dw9719"
DEST_MODULE_NAME[0]="dw9719"
DEST_MODULE_LOCATION[0]="/updates/dkms"

MAKE[0]="make KDIR=${kernel_source_dir} KVER=${kernelver}"
CLEAN="make clean KDIR=${kernel_source_dir} KVER=${kernelver}"

AUTOINSTALL="yes"
```

`Makefile`（DKMS はビルドディレクトリ内で make を実行するので `$(CURDIR)` を使う。
`$(PWD)` だと環境によってずれる）:

```makefile
obj-m := dw9719.o

KVER ?= $(shell uname -r)
KDIR ?= /lib/modules/$(KVER)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
```

導入:

```bash
sudo apt install dkms
sudo cp -r ~/dw9719-fix-1.0 /usr/src/
sudo dkms install -m dw9719-fix -v 1.0
```

この機体は Secure Boot を無効にしてあるので、DKMS の未署名モジュールが
そのままロードできる。有効な環境では MOK 登録が別途必要。

---

## 5. 検証結果（2026-08-26 実測）

```bash
modinfo dw9719 | grep -E 'filename|alias:.*i2c'
```
→ `filename: /lib/modules/7.0.0-30-generic/updates/dkms/dw9719.ko.zst`
　 `srcversion: EA628EB84D9E7D737978F2C`（パッチ版。無改変版の `BFB5DD...` ではない）
　 `alias: i2c:dw9719` ほか3件 ✅

```bash
ls -l /sys/bus/i2c/devices/i2c-INT347A:00-VCM/driver
```
→ `-> ../../../../../../bus/i2c/drivers/dw9719` ✅

```bash
media-ctl -p -d /dev/media0 | grep -E 'ov5693|ov8865|device node'
```
→ csi2 全ポートに device node、ov8865 / ov5693 が `[ENABLED]`、
　 `dw9719 2-000c` が subdev として登場 ✅

```bash
cam --list
```
→ `Internal back camera` / `Internal front camera` の2台 ✅

再起動後もすべて維持されることを確認済み。
`journalctl -k` の新規エラーは無く、増えたのは out-of-tree taint の1行のみ。

### 5.1 モデル判定が DW9719 であることの確認

`driver_data` が正しければ probe は `DW9719_INFO` を読み、値が合わなければ
`Error unknown device id` を出して `-ENXIO` で失敗する。**bind が成功している以上、
チップが自分を DW9719 と名乗った**ということ。
傍証として `v4l2-ctl --list-ctrls` の `focus_absolute max=1023` が
ソースの `DW9719_MAX_FOCUS_POS` と一致。

**カーネル更新後は毎回この `filename` を確認すること。** `kernel/` 配下の無改変版に
戻っていたら DKMS の再ビルドが失敗している。

---

## 6. NV12 の生データを画像として確認する方法

`cam --file` が吐くのは NV12 の生バイト列で、拡張子を `.jpg` にしても JPEG にはならない。
1フレーム = 幅×高さ×1.5 バイト。

真っ黒かどうかを見るだけなら変換不要:

```bash
python3 -c "d=open('out.bin','rb').read(); print('max =', max(d))"
```

画像として見るには `tools/nv12topng.py`（Pillow のみで動く。
このマシンには numpy も ffmpeg も入っていない）:

```bash
python3 tools/nv12topng.py out.bin 1280 720
```

⚠️ **全ゼロの映像は「黒」ではなく「緑一色」として見える。**
Y=U=V=0 を BT.601 で RGB に変換すると RGB(0, 135, 0) になるため。
ブラウザやプレイヤーで画面が緑一色になったら、それは「映像が来ていない」のサイン。

---

## 7. アプリ連携（PipeWire / ポータル / Vivaldi）

### 7.1 PipeWire 側は追加設定不要だった

`pipewire-libcamera` / `libspa-0.2-libcamera` が入っていれば、`wpctl status` の時点で
両カメラが `[libcamera]` として公開される。`xdg-desktop-portal-kde` の Camera ポータルも
`IsCameraPresent = true` を返す。

`gstreamer1.0-tools` は未導入だが、Python の GStreamer バインディングは使えるので
検証はこれでできる:

```bash
python3 -c "
import gi; gi.require_version('Gst','1.0')
from gi.repository import Gst; Gst.init(None)
p=Gst.parse_launch('pipewiresrc path=NODE_ID num-buffers=5 ! videoconvert ! pngenc ! multifilesink location=t_%d.png')
p.set_state(Gst.State.PLAYING)
p.get_bus().timed_pop_filtered(30*Gst.SECOND, Gst.MessageType.EOS|Gst.MessageType.ERROR)
p.set_state(Gst.State.NULL)"
```

`NODE_ID` は `wpctl status` の Sources に出る番号。**この番号は再起動やサービス再起動で変わる。**

### 7.2 Vivaldi

Vivaldi 8.1.4087.70 で `vivaldi://flags/#enable-webrtc-pipewire-camera` を **Enabled** にする。
これだけで動く。設定ファイルを直接編集する場合は Vivaldi 停止中に
`~/.config/vivaldi/Local State` の `browser.enabled_labs_experiments` に
`"enable-webrtc-pipewire-camera@1"` を追加する（バックアップ: 同ディレクトリの `.bak-20260826`）。

Firefox は**このマシンには入っていない**。`/usr/bin/firefox` は
「snap を入れてください」と表示するだけのスタブ。
`media.webrtc.camera.allow-pipewire` の作業は Firefox を導入しない限り不要。

### 7.3 リアカメラを PipeWire から隠す

隠さないと、`{video: true}` のような既定デバイス指定で**列挙順が先のリアが選ばれ、
緑一色になる**。Google Meet 等でも初期状態でリアが選ばれてしまう。

`~/.config/wireplumber/wireplumber.conf.d/50-hide-back-camera.conf`:

```
monitor.libcamera.rules = [
  {
    matches = [
      { device.product.name = "ov8865" }
    ]
    actions = {
      update-props = {
        node.disabled = true
      }
    }
  }
]
```

適用は `systemctl --user restart wireplumber`。

⚠️ **`device.disabled` では効かない。** `monitor.libcamera.rules` はデバイス生成時と
ノード生成時の両方で評価されるが、**デバイス生成の段階では `device.product.name` が
まだ `(null)`** でマッチしない。`ov8865` が入るのはノード生成時なので `node.disabled` を使う。
（`WIREPLUMBER_DEBUG=D wireplumber` のログで両段階のマッチ判定を突き合わせて確認した）

成功時のログ:

```
s-monitors-libcamera: libcam nodelibcamera_input.__SB_.PCI0.LNK0 disabled
```

### 7.4 動作確認用ページ

`tools/webtest/index.html` に、外部通信なしで完結する確認ページがある。
デバイスごとにボタンが出て、緑一色を自動判定する。`file://` ではなく localhost で開くこと
（secure context が必要）:

```bash
cd tools/webtest && python3 -m http.server 8765 --bind 127.0.0.1
```

---

## 8. 明るさのチューニング

### 8.1 何が起きていたか

libcamera が `ov5693.yaml` を見つけられず `uncalibrated.yaml` にフォールバックしていた。
そこでは AGC の目標輝度 `relativeLuminanceTarget` が既定の **0.16**
（`libipa/agc_mean_luminance.cpp` の `kDefaultRelativeLuminanceTarget`）になる。

`cam --metadata` で実測した結果:

| 設定 | 露出 | アナログゲイン | 平均輝度 |
|---|---|---|---|
| 既定 (0.16) | 33167 µs（上限） | **1.125** | 29.8 |
| 0.30 | 33167 µs（上限） | **7.94**（センサー上限） | 44.4 |

露出はどちらも上限に張り付いており、違うのはゲインだけ。
**既定では余っているゲインをほとんど使わず、暗いまま放置していた。**
0.45 まで上げても 0.30 と変わらない（露出・ゲインとも上限に到達するため）。

### 8.2 対処

`/usr/share/libcamera/ipa/ipu3/ov5693.yaml` を新規作成する（原本は
`tools/ov5693.yaml`）:

```yaml
%YAML 1.1
---
version: 1
algorithms:
  - Af:
  - Agc:
      relativeLuminanceTarget: 0.30
  - Awb:
  - BlackLevelCorrection:
  - ToneMapping:
...
```

```bash
sudo cp tools/ov5693.yaml /usr/share/libcamera/ipa/ipu3/
systemctl --user restart wireplumber
```

適用されたかは wireplumber のログで確認できる:

```
INFO IPAProxy ipa_proxy.cpp:180 Using tuning file /usr/share/libcamera/ipa/ipu3/ov5693.yaml
```

**2026-08-26 時点でこれを適用し、明るい場所で問題ない写りになることを確認済み。確定とする。**

暗所ではゲイン上限まで使うためノイズは増える。明るい場所ではゲインが自動で下がる。

### 8.3 一時的に試す方法

`/usr/share` を触らずに値を変えて試せる:

```bash
LIBCAMERA_IPU3_TUNING_FILE=/path/to/test.yaml cam -c2 --capture=60 --metadata
```

**AE の収束には 50 フレーム前後かかる。** 5フレーム程度では収束前の値を見ることになるので、
明るさを評価するときは必ず 60 フレーム程度撮って末尾を見ること。

### 8.4 フレームレートを下げても明るくならない【検証済み・否定】

「30fps だと露出が 33ms で頭打ちなので、フレームレートを落とせば露出を伸ばせる」という
見込みは**外れだった。** `FrameDurationLimits` を 30 / 15 / 10 fps 相当で指定しても、
FrameDuration・ExposureTime・明るさのいずれも変化しない:

| 要求 | FrameDuration | ExposureTime | AnalogueGain | 平均輝度 |
|---|---|---|---|---|
| 30fps | 33311 | 33167 | 7.94 | 42.9 |
| 15fps | 33311 | 33167 | 7.94 | 41.4 |
| 10fps | 33311 | 33167 | 7.94 | 41.9 |

**`FrameDurationLimits` は広告されているだけで実装されていない。**
IPU3 IPA は `ipa/ipu3/ipu3.cpp` の 284 行目でこのコントロールを ControlInfoMap に登録するが、
リクエストから読む箇所がどこにも無い。最大露出は configure 時に固定される:

```c
/* \todo take VBLANK into account for maximum exposure time */
context_.configuration.agc.maxExposureTime = maxExposure * context_.configuration.sensor.lineDuration;
```

上流にも TODO として残っている。つまり**現状の明るさが上限**で、これ以上は
libcamera 側の実装が入るまで打つ手がない。

> 検証の際は、コントロールが本当に適用されているかを先に確かめること。
> `cam --script` に `TestPatternMode: 2` を渡してカラーバーが出れば機構は正常。
> これを確認せずに「効かない」と判断すると、スクリプトの書き方の問題と切り分けられない。

`cam --script` の書式:

```yaml
properties:
  - loop: 1
frames:
  - 0:
      FrameDurationLimits: [ 66666, 66666 ]
```

## 8.5 専用カメラアプリ surface-camera

2026-08-27 に自作。Snapshot を置き換える。ソースとパッケージは
`~/surface-camera/`、詳細は `/usr/share/doc/surface-camera/README`。

#### なぜ専用アプリが要ったか

**PipeWire 経由では 1280x720 が上限**で、Snapshot などポータル経由のアプリは
そこで頭打ちになる。`libcamerasrc` で libcamera を直接叩くと **2560x1920** まで使える。

さらに副産物として、PipeWire を経由しないので **WirePlumber で隠してある
背面カメラもアプリからは使える**（ブラウザには見せないまま）。

#### 設計上の落とし穴（すべて実機で踏んだ）

| 罠 | 正解 |
|---|---|
| 既定の `src` パッドは view-finder ロールで 1280x720 が上限。超えると `Internal data stream error` | リクエストパッド `src_0` に `stream-role=still-capture` を指定する |
| `camera-name` はバックスラッシュ (`\_SB_.PCI0.LNK1`) を含み、`parse_launch` の文字列に埋め込むとエスケープが壊れる | 要素を作ってプロパティに直接設定する |
| 撮影用の枝を `tee` で常設するとバッファを握り続け、5MP で 28.6fps → 19.2fps に落ちる | 単一の枝にして表示シンクの `last-sample` から取り出す。シンクが1枚保持するコストは実測ゼロ |
| `valve drop=true` は caps イベントごと止めるため下流が初期化されない | valve を使わない |
| パイプライン再構築時、前のがカメラを手放す前に次を作ると失敗する | `get_state()` で NULL 遷移を待ち、さらに 250ms 空ける |
| `libcamerasrc` の `exposure-value` / `brightness` / `gamma` は IPU3 では黙って無視される | `cam --list-controls` に出ないものは効かない。明るさは AGC の目標輝度でしか変えられない |
| 背面は 1632x1224 以下を要求すると §9-2 の停止を踏む | 背面の解像度候補は 2560x1920 と 2048x1536 だけにする |

#### 機能

シャッター / 解像度切替 / QR 継続スキャン（別スレッドでデコード）/
セッション内のサムネイルとギャラリー / 明るさプリセット / 前面・背面切替。
背面は `videoflip` で左右反転を補正（コストは実測ゼロ）。

#### カメラの列挙

`libcameraprovider`（PipeWire ではなく libcamera 直結の GStreamer プロバイダ）を使う。
表示名がそのまま `camera-name` に渡せる ID で、`api.libcamera.Location` に
`CameraLocationFront` / `CameraLocationBack` が入る。

## 9. 残っている課題

### 9-1. 左右反転 — フロントは解決済み / リアは未対応

**フロント (OV5693) は 2026-08-26 に DKMS パッチで解決した。** リア (OV8865) は同じ症状が
残っているが、そもそも §9-2 の制約があるため未対応。

#### 症状

出力が水平方向に鏡像になっていた。フロント・リアとも。

#### 客観的な確認方法

見た目の印象ではなく、画面内の基準物で判定すること。**文字が最も確実。**

- **文字** — 商品パッケージの「Pasco」「70%」など。正しく読めるかどうか
- **時計** — 撮影時刻から針の角度を計算して照合できる。
  リアの判定では、21:11 に対する正しい向き（短針 275.5°・長針 66°）に対し、
  検出された暗い方向が 293° と 91° で、反転時の期待値（294° / 84.5°）と一致した。
  解析は「中心を指定して角度ごとに半径方向の平均輝度を取り、暗い方向を針とみなす」だけ。
  中心推定を自動化すると壁を拾って外すので、**中心と半径は手で与えること**

#### 原因

カメラモジュールが物理的に反転実装されているが、それが ACPI からもドライバからも
libcamera に伝わっていない。`ov5693_mode_configure()` は反転ビットを立てず、
`horizontal_flip` の default も 0、`camera_sensor_rotation` も 0。

#### 対処が一筋縄でいかない理由

1. `Orientation` は**アプリが設定するもの**でブラウザは設定しない
2. libcamera は `CameraSensorLegacy::setFormat()` で configure のたびに
   HFLIP へ明示的に 0 を書く。**外部から v4l2-ctl で立てても上書きされる**し、
   ドライバのコントロール既定値を変えるだけでも無意味
3. 反転すると Bayer 位相が水平1画素ぶんずれて色が壊れる（下記）

#### 反転で色が壊れる仕組み

R と B の**両方**が G より高くなる（実測 R/G 1.38 / B/G 1.32）のが特徴。
R↔B の入れ替わりではなく位相ずれの症状である。BGGR が GBRG にずれた状態を
BGGR として読むと、想定 R と想定 B の両方に実際の G が入り、想定 G には B と R が入る。
**マゼンタに転んで G だけ沈む。**

#### ⚠️ 試して駄目だったこと：MODIFY_LAYOUT を立てるだけ（再試行不要）

libcamera は `sensor/camera_sensor_legacy.cpp:373` で、ドライバが HFLIP/VFLIP に
`V4L2_CTRL_FLAG_MODIFY_LAYOUT` を立てている場合だけ `flipsAlterBayerOrder_` を true にし、
`bayerOrder()`（970行目）で Bayer 配列を補正する。そこで `ov5693.c` にこのフラグを
追加する DKMS パッチを作って試した。

**結果: 効果ゼロ。** フラグは立った（`v4l2-ctl --list-ctrls` が
`flags=modify-layout, has-min-max` を返す）が、測定値は前後でほぼ完全に一致した。

理由は **IPU3 パイプラインハンドラが `bayerOrder()` を一度も呼ばないから**。
`pipeline/ipu3/` の `ipu3.cpp` `cio2.cpp` `imgu.cpp` `frames.cpp` すべてで参照ゼロ
（比較: `pipeline/rpi/common/pipeline_base.cpp` は呼ぶ）。
このパッチは `~/ov5693-fix-1.0/superseded/modify-layout.patch` に記録として残してある。

#### 解決した方法（採用・動作確認済み）

鍵は `CIO2Device::configure()` の構造。**libcamera は `setFormat()` が書き戻した
mbus code から CIO2 の出力フォーマットを決める**:

```cpp
ret = sensor_->setFormat(&sensorFormat, transform);   /* code はここで書き戻る */
...
const auto &itInfo = mbusCodesToPixelFormat.find(sensorFormat.code);
outputFormat->fourcc = output_->toV4L2PixelFormat(itInfo->second);
```

つまり**ドライバが正しい Bayer 配列を報告しさえすれば libcamera は自動で追従する。**
`/dev/video0` は ip3G / ip3g / ip3b / ip3r の4種すべてに対応済み。

そこで `ov5693.c` に2点の変更を入れた（`~/ov5693-fix-1.0/orientation-fix.patch`）:

1. **HFLIP コントロールの意味を反転** — コントロール 0（libcamera が常に書く値）で
   センサーの水平反転が有効になるようにした。`s_stream()` で
   `__v4l2_ctrl_handler_setup()` が呼ばれるので、ストリーム開始のたびに適用される
2. **報告する mbus code を実効的な読み出し順に追従** — flip の状態から
   BGGR / GBRG / GRBG / RGGB を算出して返す

導入（バージョンは 1.0 のまま中身を差し替える方式。撤去不要）:

```bash
sudo cp ~/ov5693-fix-1.0/ov5693.c /usr/src/ov5693-fix-1.0/
sudo dkms build   -m ov5693-fix -v 1.0 --force
sudo dkms install -m ov5693-fix -v 1.0 --force
# 再起動
```

#### 検証結果（2026-08-26）

再起動後、`v4l2-ctl --list-subdev-mbus-codes` が `MEDIA_BUS_FMT_SGBRG10_1X10` を
返すようになった（従来は SBGGR10）。

通常 → mirror → 通常 の3回測定:

| | R/G | B/G | ColourGains | 色温度 |
|---|---|---|---|---|
| 通常1 | 0.943 | 1.032 | [1.11, 3.74] | 2869 |
| mirror | 0.942 | 1.076 | [1.13, 3.70] | 2881 |
| 通常2 | 0.933 | 1.058 | [1.12, 3.90] | 2845 |

修正前は mirror だけが R/G 1.384・ColourGains [0.55, 0.55]・色温度 1623 と破綻していた。
**3つとも整合するようになった。**

基準物を入れた実写（`tools/verify_final.png`、
拡大は `verify_final_zoom.png`）で最終確認:

- **向き** — パッケージの「70%」が正しく読める。反転は解消
- **色** — 赤いパッケージ RGB (176, 81, 81)、観葉植物の葉 (128, 141, 138) で G が最大、
  白い壁 (231, 223, 224) でほぼ中性。R↔B の入れ替わりなし

> 暗いシーンだと AWB が青寄りに振れて「色が壊れたか」と誤認しやすい。
> **必ず基準物を入れて判定すること。** 色温度と ColourGains も併せて見るとよい
> （正常時は CT 3400〜3700 / ColourGains 概ね [1.3, 2.6] 前後）。

#### リアカメラ (OV8865) は未対応。ただし必要なパッチはフロントより単純【実測済み】

反転は残っている（実写でパッケージの文字が裏返る）。

**ov8865 の反転は Bayer 配列を変えないことを実測で確認した。**
フル解像度モード（出力 2048x1536）で 通常 → mirror → 通常 の3回測定:

| | R/G | B/G | ColourGains | 色温度 |
|---|---|---|---|---|
| 通常1 | 1.039 | 0.990 | [1.313, 2.692] | 3192 |
| mirror | 1.059 | 0.984 | [1.313, 2.754] | 3180 |
| 通常2 | 1.044 | 0.992 | [1.307, 2.769] | 3191 |

3つとも整合。フロントで出た「R と B の両方が G より高くなる」症状は出ない。
ビニングモード（1632x1224）でも同様だったので、**モード依存ではなくセンサー依存**。

両ドライバとも同じ ISP 補正ビット（`FORMAT2_FLIP_HORZ_ISP_EN` +
`FORMAT2_FLIP_HORZ_SENSOR_EN`）を対で立てているのに挙動が違う。理由は不明。

→ **リア用のパッチは HFLIP の意味を反転させるだけでよい。**
　 §9-1 でフロントに入れた mbus code の追従は不要（入れるとかえって壊す）。
　 ただしリアは §9-2 の制約で常用できないため、優先度は低い。

### 9-2. リアカメラ (OV8865) — 停止の切り分けが進んだ。回避策あり・根本原因は未特定

**センサーは正常。** 壊れているのは、フル解像度以外のセンサーモードでの転送。

#### モード別の挙動（V4L2 で直接 CIO2 から吸った結果）

| センサーモード | hts | vts | ビニング | pll2_binning | レジスタ列 | 5フレーム要求の結果 |
|---|---|---|---|---|---|---|
| 3264x2448（フル） | 3888 | 2470 | なし | false | native | ✅ **完走** |
| 3264x1836 | 3888 | 2470 | なし | false | native | ❌ 1フレームで停止 |
| 1632x1224 | 1923 | 1248 | あり | true | binning | ❌ 1フレームで停止 |
| 800x600 | 1250 | 640 | あり | true | binning | ❌ 1フレームで停止 |

フロント (OV5693) を同じ手順で 2592x1944 で流すと完走するので、手順の問題ではない。

**libcamera は 1280x720 出力に対して 1632x1224 を選ぶ。** これが停止する側なので、
リアは「1フレーム目だけ出して固まる」→ 全ゼロ → 緑一色、に見えていた。

#### 除外できた原因

- **バッファ溢れではない。** 800x600 は sizeimage が 1024×600 = 614,400 で 4096 の倍数、
  つまり**余白ゼロ**。溢れれば必ず `payload length` 警告が出るはずだが、警告は出ず、
  ちょうど 614,400 バイトを受け取って停止した
- **CIO2 側の問題ではない。** 失敗時の割り込み挙動を `/proc/interrupts` の IRQ 138
  （ipu3-cio2）で観測した。ある条件では **12秒間まったく増えない**（＝データが届いていない）。
  CIO2 が固まっているのではなく、受け取るものが無い
- **CIO2 の LOP 構築のバグではない。** `cio2_vb2_buf_init()` を全モードのサイズで
  追ったが、`pages` / `lops` / dummy page の配置に境界の破綻はない

> ⚠️ 以前の記録にあった「差 3584 バイトの正体を追う」は**筋が悪い。**
> `received` の 2,588,672 は `PFN_UP(sizeimage) × 4096`、すなわちバッファのページ容量
> そのもので、3584 は単なる残り余白。DMA が割り当てを使い切って打ち切られただけ。
>
> ⚠️ 「取得ファイルが sizeimage より 512 バイト少ない」も無意味。
> `timeout` で kill した際のページ境界フラッシュの産物。

#### 症状の正体：CSI-2 のフレーム同期エラー

失敗時、カーネルログに大量に出る（撮影10秒あたり 150 件前後）:

```
ipu3-cio2 0000:00:14.3: CSI-2 receiver port 0: frame sync error
ipu3-cio2 0000:00:14.3: DMA output error on CSI2 buses: 0x1
```

つまりセンサーは送信しているが、**CSI-2 のフレーム開始／終了パケットの並びが壊れている。**
最初の1バッファだけ完結し、以後は完結しない。

観測方法（root 不要）:

```bash
# 割り込みが増え続けるか＝データが届いているか
awk '/^ *138:/{s=0;for(i=2;i<=5;i++)s+=$i;print s}' /proc/interrupts
# エラーの数
journalctl -k --since "1 min ago" | grep -c 'frame sync error'
```

`v4l2-ctl --poll-for-event=frame_sync` も試したが、**動作するモードでもイベントが
1件も取れなかった**ので観測手段として使えない。割り込みカウンタのほうが確実。

#### モード別の実測

| モード | ビニング | vblank | VTS | フレーム | 同期エラー | DMAエラー |
|---|---|---|---|---|---|---|
| 3264x2448 | なし | 22（既定） | 2470 | **100** | 0 | 0 |
| 3264x1836 | なし | 22（持ち越し） | 1858 | 1 | 119 | あり |
| 3264x1836 | なし | 256（持ち越し） | 2092 | **100** | 56 | 0 |
| 3264x1836 | なし | 634（既定） | 2470 | 1 | **0** | 0 |
| 1632x1224 | あり | 22〜256 を掃引 | – | すべて 1 | 150前後 | あり |
| 800x600 | あり | 既定 | 640 | 1 | 162 | あり |

**3264x2448 だけが常に安定。** ビニングモードは vblank を何にしても必ず失敗する。
3264x1836 は**条件によって成功も失敗もする**（VTS との単調な関係にはなっていない。
同期エラー 0 なのに 1 フレームしか完結しない条件もある）。

#### 見つけたバグ：コントロールがモード切替に追従しない【修正済み・ただし症状は治らず】

モードを切り替えても、**コントロールの現在値が前のモードのまま残っていた。**

| | 3264x2448 | 1632x1224（修正前） |
|---|---|---|
| horizontal_blanking | min=max=624, value=624 | min=max=**291**, value=**624** ← 自分の範囲外 |
| vertical_blanking | default=22, value=22 | default=**24**, value=**22** ← 前モードの値 |

原因は `ov8865_set_fmt()`（2741行目付近）。`__v4l2_ctrl_modify_range()` の第5引数は
**既定値であって現在値ではない**ため、範囲と既定は更新されるが `ctrl->val` は残る。
vblank は書き込み可能で `s_stream()` の `__v4l2_ctrl_handler_setup()` から
`ov8865_vts_configure()` 経由でハードウェアに適用されるので、**古い VTS が書かれていた。**

修正は mainline の imx219 などと同じ形で、`modify_range` の直後に
`__v4l2_ctrl_s_ctrl()` で現在値を新モードの既定値に戻すだけ:

```c
	vblank_def = mode->vts - mode->output_size_y;
	__v4l2_ctrl_modify_range(sensor->ctrls.vblank, OV8865_TIMING_MIN_VTS,
				 OV8865_TIMING_MAX_VTS - mode->output_size_y,
				 1, vblank_def);
	__v4l2_ctrl_s_ctrl(sensor->ctrls.vblank, vblank_def);
```

パッチは `~/ov8865-fix-1.0/vblank-stale-value.patch` に保存してある。

**効果（実測）:**

- vblank の現在値が追従するようになった（1632x1224 で 22→24、3264x1836 で 22→634）
- **挙動が決定的になった。** 同じ測定を2回繰り返して結果が一致するようになった
- hblank は read-only のため `__v4l2_ctrl_s_ctrl()` が通らず値は古いまま。ただし
  `s_ctrl` に HBLANK の分岐が無くハードウェアには書かれないので、表示上の問題のみ

**効果がなかった点:**

- **3264x1836 は動くようにならなかった。** むしろ一貫して失敗するようになった
  （修正前は直前の状態によって 100 フレーム完走することがあった）
- vblank を 64〜634 まで掃引しても全て 1 フレーム。以前の成功時と同じ VTS 2092 を
  再現しても失敗する。**つまり VTS は真の変数ではなかった**
- ビニングモードは修正前後で変わらず失敗
- 回避策のフル解像度は無傷（修正後も 100 フレーム完走・同期エラー 0）

**⚠️ この DKMS パッケージは撤去した。** 利用者から見た改善がゼロで、時々動いていた構成を
失い、カーネル更新のたびに再ビルドされる DKMS が増えるだけだったため。
`ov5693` の `MODIFY_LAYOUT` 版と同じ判断（上流的には正しいが、この機体では何も変わらない）。

ソースとパッチは `~/ov8865-fix-1.0/` に残してある。**再導入しても症状は治らない。**

#### ここで止めた理由

3264x1836 の成功／失敗を分ける変数を特定できなかった。VTS ではなく、同じ設定でも
実行の履歴によって結果が変わる。隠れた状態がどこかに残っていると見られるが、
これ以上は**センサーのレジスタを実際に読む**しかない。それには i2c-tools と root、
そして OV8865 のデータシートが要る。当て推量で進めても検証できないため打ち切った。

再開するなら、まずレジスタダンプの手段を確保すること。`ov8865_mode_configure()` が
書いた値と、`size_auto` がセンサー内部で計算したクロップ値を読めれば、
モードテーブルの宣言値と実際の出力のずれを直接確認できる。

#### 回避策（動作確認済み）

**1632x1224 より大きい出力を要求すればフル解像度モードが選ばれ、停止しなくなる。**

```bash
cam -c1 -s width=2048,height=1536 --capture=90
```

→ 90フレーム連続で取得できた（約11fps）。停止も payload 警告も無し。

#### 明るい場所での実写確認（2026-08-26 実施・成功）

| 項目 | 結果 |
|---|---|
| フレーム数 | 90/90 完走 |
| payload 警告 | 0 件 |
| 収束後のアナログゲイン | 2.55（上限 16.0 に対して余裕あり） |
| 平均輝度 | frame0 1.2 → frame30 76.2 → frame89 121.0 |

露出・色とも正常な実景が撮れた（`tools/back_bright_result.png`）。
AE の収束に 15〜30 フレームかかるので、**先頭フレームは捨てる前提で扱うこと。**

撮れた絵はぼけていた。露出 33 ms の手持ちによるブレの可能性が高いが、ピントの可能性も残る。
本体を固定して撮り直せば切り分けられる。**未実施。**

#### センサーが正常であることの確認

`v4l2-ctl` でゲインと露出を手動で上げ、CIO2 の raw を復元すると**実景が写る**:

```bash
v4l2-ctl -d /dev/v4l-subdev7 --set-ctrl=vertical_blanking=2000
v4l2-ctl -d /dev/v4l-subdev7 --set-ctrl=analogue_gain=2048
v4l2-ctl -d /dev/v4l-subdev7 --set-ctrl=exposure=3000
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=2 --stream-to=raw.bin
python3 tools/decode_ipu3_raw.py raw.bin 3264 2448
```

⚠️ **パック済みバイト列の最大値を「画素値」と読み違えないこと。**
IPU3 パック 10bit は 32バイトに 25画素なので、生バイトの max が 240 でも
実際の画素値は 10bit で 40 程度、ということが起こる。必ず復元してから判断する。

⚠️ **`timeout` で kill した場合、書き出しファイルはページ境界で切れる。**
「sizeimage より 512 バイト少ない」といった端数はその産物で、意味は無い。

#### 未着手

- 上記「残った容疑」の検証。`size_auto` の垂直クロップ経路
- PipeWire/ブラウザ経由でフル解像度モードを使わせる方法。
  現状ブラウザは 1280x720 以下を要求するので停止側のモードになる。
  **このため §7.3 の非表示化は外さないこと**
- 左右反転（§9-1 参照。`ov8865.c` の HFLIP 反転だけでよい）
- ぼけの原因切り分け（ブレか合焦か）

### 9-2b. リアの AE ハンチング — 解決済み（2026-08-27）

背面カメラで、露出が上限に張り付いてゲインで調整する領域に入ると、
**約1.2秒周期で明るさが往復**していた。前面では起きない。

#### 原因：ドライバがゲインの粒度を潰していた

| センサー | analogue_gain | 実質の段数 |
|---|---|---|
| ov5693（前面） | min=1 max=127 **step=1** | 127段 |
| ov8865（背面） | min=128 max=2048 **step=128** | **16段のみ** |

ゲインレジスタ 0x3508/0x3509 は 13bit で 128 が 1.0 倍、つまりハードウェアは
**1/128 刻みで 1921 段**設定できる。`step=128` はそれを整数倍（1.0x / 2.0x / …）
だけに潰していた。AGC が 1.3 倍を要求してもハードは 1.0 か 2.0 しか出せず、
暗すぎ↔明るすぎを往復する。

前面で起きないのは、粒度が細かいことに加え、露出に余裕がありゲインが
1.0 のまま動かない場面が多いため。背面は 15fps で露出が 33237us に張り付き、
**ゲインでしか調整できない**ので常に踏む。

#### 修正

`ov8865.c` の 1 トークン。DKMS `ov8865-fix/1.1`（`gain-step.patch`）。

```c
-	v4l2_ctrl_new_std(handler, ops, V4L2_CID_ANALOGUE_GAIN, 128, 2048, 128,
+	v4l2_ctrl_new_std(handler, ops, V4L2_CID_ANALOGUE_GAIN, 128, 2048, 1,
 			  128);
```

#### 実測

| | 修正前 | 修正後 |
|---|---|---|
| ゲイン範囲 | 1.13〜1.64 | 1.227〜1.234 |
| 振れ幅 | 0.51 | **0.008** |
| 方向反転 | 11回/50 | 1回/45 |

1.227 は従来の16段では原理的に取れない値。

#### ⚠️ 試して駄目だったこと（再試行不要）

すべて実測で否定した。**この順で疑ったが全部外れ。**

| 容疑 | 否定した根拠 |
|---|---|
| オートフォーカスの干渉 | チューニングから `Af` を外しても振幅・周期とも同一 |
| AGC の目標輝度 | 0.20 / 0.25 / 0.30 / 0.40 のどれでも同一 |
| `sensorDelays` の不備 | **libcamera の再ビルドを検討したが不要だった。** 露出は 33237 固定で一切動かず、両センサーとも sensorDelays は空で同条件なのに、前面は目標輝度を上げて同じゲイン領域に入れても完全に安定していた |
| 商用電源のフリッカー | 切り分けようとしたが、照明を消すと暗すぎてゲインが飽和し判定できなかった。上の前面との比較で不要になった |

> 決め手は「**前面を強制的にゲイン領域に入れて比較する**」ことだった。
> 目標輝度を 1.0〜4.0 まで上げると前面もゲイン 1.94 を使うようになるが、
> 振れ幅 0.00〜0.06 で完全に安定していた。同じ照明・同じ解像度で
> 背面だけが振動する以上、共通要因（libcamera 側）ではありえない。
>
> **照明を変えて動作点を作ろうとすると失敗する。** 明→ゲイン未使用、
> 暗→ゲイン飽和、と両極に振れて中間に入らない。目標輝度を動かして
> 動作点を作るほうが確実。

### 9-3. 色味

`ov5693.yaml` に AWB/CCM の校正値が無いため、緑〜シアンに転ぶ。
§8 の変更は明るさだけで、色は直らない。IPU3 向け ISP チューニングは上流でも未成熟。

なお PipeWire 経由の絵は `cam` 直叩きより色が自然だった（肌色がまともに出る）。
実用上は問題ないレベル。

### 9-4. IR カメラ (OV7251)

エンティティと device node は出たが、csi2 2 へのリンクは `[]` で未接続。
wiki の対応表では全機種 🚫。Surface Book 2 の成功事例（discussion #1352）は
手動 media-ctl リンク + 10bit packed Bayer の自前デコード + IR LED の sysfs 叩き +
v4l2loopback という重装備。**スコープ外。**

### 9-5. 上流への報告 — 調査済み。dw9719 は既報のため見送り

2026-08-26 に linux-surface のリポジトリを検索したところ、**dw9719 の件は既に複数報告されていた。**
`repo:linux-surface/linux-surface dw9719` で Issue が 48 件ヒットする。

| Issue | 内容 |
|---|---|
| #2172 | 「dw9719 VCM driver cannot bind on ACPI systems (missing i2c_device_id)」。原因はこれで既出 |
| #2223 | 「A non-responsive VCM (dw9719) blocks all IPU3 cameras from registering」 |
| #2225 | 「dw9719 VCM driver no longer binds on 6.19+ kernels」。**`driver_data` 付きの正しいテーブルが既に投稿されている**（§4.3 と同一内容） |

つまり原因・回帰・正しいパッチのすべてが既出で、こちらから足せるものはほとんど無い。

唯一まだ誰も指摘していないのは、**#2172 が提案しているテーブルが `driver_data` なしのベタ書きで、
6.19+ では `DW9718S` に化ける**という点（§4.2）。これを指摘する短いコメント案を
`（未投稿のためリポジトリには含めない）` に用意したが、**投稿は見送った。**
既に #2225 に正しい版があるため実害に至る人は限られる、という判断。

新規 Issue 用に書いた長文の下書きは `tools/report/01-dw9719-issue.md` に残してある。
重複と判明したので**投稿しないこと。**

#### 未調査：残り2件

これらは既存報告の有無を確認していない。報告を検討するなら**下書きを書く前に検索すること**
（今回は下書きを先に書いてから重複が判明した）。

- **OV8865 のモード依存の停止**（§9-2）— 1632x1224 と 3264x1836 で1フレーム目に停止し、
  3264x2448 なら完走する。回避策つきなので、同じ症状の人には価値がある可能性
- **OV5693 の反転実装と Bayer 位相**（§9-1）— IPU3 パイプラインが `bayerOrder()` を
  呼ばないため libcamera 側で補正されない。OV8865 では位相がずれないという対比も含む

なお §9-1 のパッチ（HFLIP の意味を反転させる）は**そのままでは上流に通らない。**
「このモジュールは反転実装」という事実をドライバに埋め込む処置であり、
本来は ACPI やボード情報で扱うべきもの。報告するなら事実の共有として書くこと。

## 10. 元に戻す方法

3つの変更はそれぞれ独立に撤去できる。**起動を壊すたぐいのリスクは無い。**
GRUB もカーネルパラメータも触っていない。

```bash
# 1. dw9719 ドライバ
sudo dkms remove -m dw9719-fix -v 1.0 --all

# 2. リアカメラの非表示化
rm ~/.config/wireplumber/wireplumber.conf.d/50-hide-back-camera.conf
systemctl --user restart wireplumber

# 3. 明るさチューニング
sudo rm /usr/share/libcamera/ipa/ipu3/ov5693.yaml /usr/share/libcamera/ipa/ipu3/ov8865.yaml
systemctl --user restart wireplumber

# 4. リアのゲイン粒度（AE ハンチング対策）
sudo dkms remove -m ov8865-fix -v 1.1 --all

# 5. 専用カメラアプリ
sudo apt remove surface-camera
```

**将来 upstream で dw9719 が修正されたら DKMS 側を削除すること。**
in-tree に `.id_table` が入った状態で `updates/dkms` の古い版が優先されると、かえって障害になる。

---

## 11. 導入済みパッケージ

調査時に追加（合計約9MB）:

```
libcamera-tools libcamera-ipa libcamera0.7 libcamera-v4l2
gstreamer1.0-libcamera pipewire-libcamera libspa-0.2-libcamera
v4l-utils
```

修正時に追加:

```
dkms
```

`linux-headers-$(uname -r)` `gcc` `make` `xdg-desktop-portal-kde` は元から導入済み。
`linux-firmware-intel-graphics` も元から導入済み。
`/lib/firmware/intel/ipu3-fw.bin` が `ls` で見えないのは圧縮配置されているためで、
カーネルは問題なく読めている。**ファームウェア関連の作業は不要。**

未導入だが不要と判断したもの: `gstreamer1.0-tools`（Python バインディングで代替）、
`numpy` / `ffmpeg`（Pillow のみのスクリプトで代替）、`v4l2loopback`（現状不要）。

---

## 12. ファイルの置き場所

```
/usr/src/dw9719-fix-1.0/                        DKMS ソース一式
├── i2c-id-table.patch                          パッチ差分
└── dw9719-patched.ko                           事前ビルド版（削除可）
~/dw9719-fix-1.0/                               上と同じもの。作業用の控え

~/.config/wireplumber/wireplumber.conf.d/
└── 50-hide-back-camera.conf                    リアカメラ非表示化

/usr/share/libcamera/ipa/ipu3/ov5693.yaml       明るさチューニング（適用済み）

/usr/src/ov5693-fix-1.0/                        DKMS ソース（向き補正）
/usr/src/ov8865-fix-1.1/                        DKMS ソース（ゲイン粒度）
~/ov8865-fix-1.1/
├── gain-step.patch                             採用したパッチ
├── README.txt                                  原因・切り分け・検証手順
└── superseded/vblank-stale-value.patch         効果が無かった版（記録）

~/surface-camera/                               専用カメラアプリ
├── src/surface-camera                          本体（Python + GTK4）
├── build/                                      .deb の中身
└── surface-camera_1.9_all.deb
~/ov5693-fix-1.0/
├── orientation-fix.patch                       採用したパッチ
├── README.txt                                  導入・撤去・検証の手順
└── superseded/modify-layout.patch              効果が無かった版（記録）

tools/
├── ov5693.yaml                                 上記の原本
├── nv12topng.py                                NV12 → PNG 変換
├── decode_ipu3_raw.py                          CIO2 の IPU3 パック raw → PNG
├── front_frame3.png / front_frame4.png         最初に撮れた実写
├── compare_target_0.16.png                     明るさチューニング前後の比較
├── compare_target_0.30.png
├── test_back_bright.sh                         リアの回避策を検証するスクリプト
├── test_mirror.sh                              左右反転を比較するスクリプト（4枚撮る）
├── mirror_back_normal.png / _mirror.png        リアの通常と -o mirror
├── mirror_front_normal.png / _mirror.png       フロントの通常と -o mirror
├── test_mirror_color.sh                        反転時の色崩れを切り分けるスクリプト
├── verify_final.png / verify_final_zoom.png    向き修正後の最終確認（文字と色）
├── back_bright_result.png                      リアの実写（明るい場所・成功）
├── back_raw_fullres_decoded.png                リアの raw 復元（センサー正常の証拠）
├── back_raw_1632_decoded.png
└── webtest/index.html                          ブラウザ確認ページ

~/.config/vivaldi/Local State.bak-20260826      Vivaldi 設定のバックアップ
docs/investigation.md.bak-*                    この資料の更新前バックアップ
```

---

## 13. 次にやること

前面カメラが実用になった時点で当初の目的は達している。以降は任意。

1. **§9-2 の根本原因** — 背面が 1632x1224 / 3264x1836 で停止する理由。
   症状は CSI-2 のフレーム同期エラーと確定、CIO2 は無実。再開には
   センサーのレジスタダンプ手段（i2c-tools + root + データシート）が要る
2. **§9-5 の上流報告** — 報告価値のあるドライバ側の発見が3件たまった。
   dw9719 の `i2c_device_id`（既報だが `driver_data` の罠は未報告）、
   ov5693 の反転実装と Bayer 位相、**ov8865 のゲイン粒度**。
   3件目は step を 1 にするだけの明快な修正で、上流にそのまま出せる
3. **§9-3 の周辺減光** — IPU3 IPA にシェーディング補正が無いため補正できない。
   アプリ側で保存時にゲインマップを掛けることは可能だが校正作業が要る
4. §9-4 の IR は引き続き放置でよい

~~§8.4 のフレームレート検証~~ → 実施済み。効果なし（実装されていない）。
~~明るい場所でのリア実写確認~~ → 実施済み。成功。
~~ov5693 に MODIFY_LAYOUT を追加~~ → 実施済み。IPU3 では効果なし（§9-1）。
~~フロントの左右反転~~ → 解決済み。ov5693 の向き補正パッチ（§9-1）。
~~dw9719 の上流報告~~ → 既報につき見送り（§9-5）。
~~リアの AE ハンチング~~ → 解決（§9-2b、ゲイン粒度）。
~~専用カメラアプリ~~ → surface-camera として完成（§8.5）。
~~ov8865 の vblank 追従修正~~ → 実施済み。バグは直ったが症状は治らず、撤去（§9-2）。

いずれの変更も撤去が1コマンドで済み、起動を壊すたぐいのリスクは無い。
とはいえカーネルモジュールを差し替える作業なので、
スナップショットを取れる環境があるに越したことはない。
