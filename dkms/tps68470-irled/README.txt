tps68470-irled — IR 発光体 (赤外線照明) を LED クラスデバイスとして公開する

症状:
  IR カメラ (OV7251) は映るが、赤外線照明が点かない。
  暗所では顔が写らず、明るい室内でも「日光が入る時間帯」でないと使えない。
  LED クラスにも GPIO にも i2c にも、それらしいデバイスが出てこない。

  linux-surface の wiki では Surface Go / Book / Laptop の全機種で
  IR カメラが 🚫。発光体を点けた報告は 2026 年 9 月時点で存在しない。

原因:
  発光体は TPS68470 PMIC 内蔵のフラッシュ LED ドライバに接続されている。
  ところが include/linux/mfd/tps68470.h は Linux が実装した範囲しか
  定義しておらず、フラッシュ制御ブロックが丸ごと欠落している。

    0x06-0x10  クロック      定義あり
    0x14-0x27  GPIO          定義あり
    0x28-0x3A  フラッシュ    ★ 定義なし
    0x3C-0x48  レギュレータ  定義あり

  「ヘッダに無い」を「チップに無い」と読み替えてしまうと、この経路は
  最初から探索対象から外れる。実際そうなっていた。

  Windows は iactrllogic64.sys ("Intel Control Logic Driver", ACPI\INT3472)
  の tps68470::Tps68470Flash クラスでこの領域を操作している。

  なお ACPI には発光体の記述が一切ない。DSDT/SSDT 全36テーブルを確認済み。
  カメラ2 (IR) にはコンパニオンデバイスの宣言すら無く、GPIO 記述子も無い。
  ACPI を辿っても永久に見つからない。

対処:
  既存の int3472-tps68470 が作った regmap を dev_get_regmap() で共有し、
  LED クラスデバイスとして brightness (0-7) を公開する。

    /sys/class/leds/tps68470::ir_illuminator/brightness

  regmap を共有する理由は安全性。カーネルが管理しているチップに
  ユーザー空間から i2c で直接書き込むと整合性が壊れる。
  （開発中に実際に PMIC を停止させ、カメラが応答しなくなった）
  カーネル内なら regmap のロックでアクセスが直列化される。

  触るのは 0x29 / 0x2c-0x2f / 0x30 / 0x34-0x36 のみ。
  GPIO (0x14-0x27) とレギュレータ (0x3c-0x48) には触れない。

点灯手順 (Windows ドライバの TorchPowerOn を逆アセンブルして復元):

    0x29 <- 0x01        有効化ビット
    0x2c <- 0x00
    0x2d <- 電流
    0x2f <- 0x00        ストロボ駆動モードではここに電流
    0x2e <- 0x00
    0x30 <- 0x07        自動消灯までの時間
    0x34 <- 電流        LED1
    0x35 <- 電流        LED2
    0x36 <- 0x45        点灯ビットなしで一度
    0x36 <- 0x65        点灯

⚠️ 落とし穴:

  1. 0x29 は強度ではなく有効化ビット。1 を書く。
     ここに強度を書くと読み返しが 0 になり、点灯しない。

  2. 0x36 の bit4 (0x10) を立てると点灯しない。
     0x65 (bit3,4 なし) と 0x6d (bit3) は点灯、0x75 (bit4) は不発。
     別の LED 出力が選ばれるものと思われる。

  3. 電流は下位3ビットのみ有効 (0-7)。
     0x08 / 0x10 / 0x20 は下位3bitが0なので消灯する。

  4. 点灯したまま電流を書き換えても反映されない。
     消灯 -> 電流設定 -> 点灯 の順でないとラッチされない。

  5. 0x30 (タイムアウト) を書かないと約1.3秒で自動消灯する。
     0x07 で約14秒。これがハードウェアの上限で、過熱防止の安全機構。
     無効化はできないし、すべきでもない。長時間使うなら明示的に再点灯する。

     認証は1秒未満で終わるため、これに気づかず長く見逃していた。
     短時間の検証しかしていないと踏まない。

導入:
    sudo cp -r dkms/tps68470-irled /usr/src/tps68470-irled-1.0
    sudo dkms add -m tps68470-irled -v 1.0
    sudo dkms install -m tps68470-irled -v 1.0
    echo tps68470-irled | sudo tee /etc/modules-load.d/tps68470-irled.conf
    sudo cp face-auth/etc/99-tps68470-irled.rules /etc/udev/rules.d/
    sudo udevadm control --reload
    sudo modprobe tps68470-irled

  udev ルールは brightness を video グループへ開放する。
  画面ロッカーなど非特権プロセスから点灯するために必要。
  開放するのは「明るさを変える」権限だけで、i2c 全体ではない。

撤去:
    sudo dkms remove -m tps68470-irled -v 1.0 --all
    sudo rm -f /etc/modules-load.d/tps68470-irled.conf \
               /etc/udev/rules.d/99-tps68470-irled.rules

上流について:
  正しい形は MFD にセルを足して drivers/leds/ に置くこと。
  Daniel Scally が 2023 年に leds-tps68470.c を投稿しているが
  (LWN: https://lwn.net/Articles/926867/) 議論が止まりマージされていない。
  本モジュールはその前段としてのローカル実装。
