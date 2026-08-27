dw9719-fix — カメラが1台も認識されない問題の修正

症状:
  cam --list に何も出ない。libcamera のデバッグログに
    Skip ipu3-csi2 0: no device node
  が出て、media-ctl で見ると csi2 に device node が無く、
  センサー3つ (ov5693 / ov8865 / ov7251) はエンティティとしては見えるが
  すべて 0 link になっている。

原因:
  drivers/media/i2c/dw9719.c の struct i2c_driver に .id_table と
  MODULE_DEVICE_TABLE(i2c, ...) が無く、of_match_table しかない。
  この機体の VCM は ov8865 が i2c_new_client_device() で動的に作るので
  OF ノードも ACPI コンパニオンも持たず、マッチする手段がゼロになる。

  ipu3-cio2 は「すべての非同期サブデバイスが揃った時点」で初めて
  センサー→csi2 のリンクと csi2 の subdev ノードを作る。ov8865 は自身の
  サブ notifier で VCM を待つため、dw9719 が probe されないと complete が
  永久に来ない。結果として VCM を使わない前面・IR まで巻き添えで死ぬ。

  タグ単位で確認: v6.17 あり / v6.18 あり / v6.19 なし / v7.0 なし。
  v6.19 で DW9718S / DW9800K のサポートと OF マッチングが入った際に
  巻き添えで消えたと思われる。

⚠️ 落とし穴:
  git 履歴から v6.18 のテーブルをそのまま復元してはいけない。v6.18 は

      { "dw9719" },
      { "dw9761" },

  と driver_data を省略しており、当時は enum が DW9719 = 0 で、しかも
  model は INFO レジスタの読み取りだけで決まっていた（v6.18 に
  i2c_get_match_data() の呼び出しが無い）ので無害だった。

  v6.19 以降は enum の先頭が DW9718S になり、probe が
  i2c_get_match_data() でモデルを決める。DW9718S だとチップID検出を
  丸ごと飛ばして（goto props）DW9718S 用のレジスタ配置を書き込む。
  bind はするがエラーも出さずに間違ったレジスタで VCM を叩くので、
  bind しないより悪い。

修正（i2c-id-table.patch）:
  driver_data を明示した i2c_device_id テーブルを追加し、
  .id_table を設定する（.driver.of_match_table は残す）。
  これで probe は default: を通って DW9719_INFO を読み、
  チップ自身に型を名乗らせる。

導入:
  sudo cp -r dkms/dw9719-fix /usr/src/dw9719-fix-1.0
  sudo dkms install -m dw9719-fix -v 1.0
  再起動

撤去:
  sudo dkms remove -m dw9719-fix -v 1.0 --all
  ※ 将来 upstream で修正が入ったら必ず外すこと。in-tree が直った状態で
     updates/dkms の古い版が優先されると、かえって障害になる。

検証:
  modinfo dw9719 | grep -E 'filename|alias:.*i2c'
    → filename が updates/dkms 配下、alias に i2c:dw9719 が出れば成功
  ls -l /sys/bus/i2c/devices/i2c-INT347A:00-VCM/driver
    → dw9719 へのシンボリックリンク
  cam --list
    → カメラが列挙される

  DW9718S ではなく DW9719 と判定されていることは、bind が成功した事実が
  示している。driver_data が正しければ probe は DW9719_INFO を読み、
  値が合わなければ -ENXIO で失敗するため。

試して駄目だったこと（再試行不要）:
  modprobe dw9719              ロードはするがバインドしない
  echo ... > .../dw9719/bind   ENODEV。bind_store も driver_match_device() を通る
  .../dw9719/new_id            存在しない。i2c バスは動的ID追加を実装していない
  /sys/kernel/debug/devices_deferred   空。probe 待ちですらない
