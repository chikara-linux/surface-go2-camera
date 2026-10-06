# 前面カメラの画質 — 改造した IPU3 IPA

ビデオ通話で前面カメラの顔が暗く、色の抜けた「暗視カメラのような」絵に
なっていた件の対処（2026-10-06）。

## 何が原因だったか

1. **トーンカーブがほぼ直線。** libcamera 0.7.x の IPU3 の IPA はガンマを 1.1 に
   固定している（ソースに「暫定」の注記あり）。一般的な画像は 2.2 前後で、
   中間調（肌）が沈む
2. **逆光。** 天井の照明が画面に入ると、全体平均で露出を決める自動露出が
   露光を絞り、顔が暗くなる。あとから持ち上げると暗部のノイズでザラつく

色の並び（Bayer）や色合いは正しかった。RAW と ISP 出力を同じ撮影から取り、
肌と壁の色の比で確かめた。

## 選んだ設定

スマートフォンのプレビュー画面を作例に、実機で見比べて決めた。

| 項目 | 値 | 効果 |
|---|---|---|
| `Agc: relativeLuminanceTarget` | 0.59（従来 0.40） | 露出 1.47 倍。壁がスマホと同じ明るさに来る値を実測で求めた |
| `ToneMapping: gamma` | 2.8（従来 1.1 固定） | 顔の中間調を持ち上げる |

暖色寄せと彩度も試したが、**逆光への耐性と実用性で露出＋ガンマだけを採った**。

## 仕組み

標準パッケージには触らない。

| ファイル | 置き場所 |
|---|---|
| 改造版 `ipa_ipu3.so` と `BUILT_AGAINST` | `/usr/local/lib/libcamera/ipa/` |
| `configuration.yaml` | `/etc/libcamera/`（改造版を先に探させる） |
| チューニング `../tuning/ov5693.yaml` | `/usr/share/libcamera/ipa/ipu3/` |

- 改造は `ipu3-tone-mapping-gamma.patch` だけ。ToneMapping がチューニングの
  `gamma` を読む。キー名は上流の開発版（libipa の Gamma）と同じなので、
  上流がリリースして Ubuntu に入れば、このチューニングのまま標準で動く。
  書かなければ従来の 1.1（背面カメラなどは変わらない）
- 改造版は署名が無いので、libcamera が別プロセス（`ipu3_ipa_proxy`）で動かす。
  負荷の増加は実測で 1 コアの 2.3%
- Ubuntu が libcamera を更新して IPA の取り決めが変わると、改造版は選ばれず
  **自動で標準の IPA に戻る**（カメラは動き、ガンマが 1.1 に戻るだけ）。
  `check-install.sh` が版の不一致を知らせるので、そのとき再ビルドする

## 作り直す・入れ直す

```bash
sudo apt install --no-install-recommends meson libyaml-dev python3-ply python3-jinja2
./build.sh          # 入っている libcamera と同じ版のソースで ipa_ipu3.so だけ作る（約 20 分）
```

`build-without-gles2.patch` はビルドだけの修正。0.7.0 のビルド定義は EGL の
ヘッダしか見ずに `egl.cpp` を組み込み、GLES2 の開発ファイルが無い環境で
止まる。IPA の中身には関係しない。

## 戻す

```bash
sudo rm /etc/libcamera/configuration.yaml && systemctl --user restart wireplumber
```

## 却下した案

| 案 | 理由 | 再検討の条件 |
|---|---|---|
| 上流の新版を待つ | 0.7.2 にもまだ入っていない。LTS は新版を入れないので実質 2028 年 | Ubuntu の libcamera に上流の Gamma が入ったら、改造版をやめてチューニングだけにする |
| 後処理して仮想カメラで渡す | CPU を常時 1 コアの 71% 消費（直接なら 5%） | なし |
| パッケージ一式を自前ビルドして hold | セキュリティ更新が止まる。この方式なら IPA 1 ファイルで済む | なし |
| 暖色寄せ・彩度 | 試したが、露出＋ガンマだけの方が実用的と判断 | 色に不満が出たら（色補正行列は上流でも IPU3 未対応。自前の改造が要る） |
