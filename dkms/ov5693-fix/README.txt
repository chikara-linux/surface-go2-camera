ov5693-fix — Surface Go 2 フロントカメラの左右反転を修正する DKMS パッケージ

このモジュールが解決すること:
  カメラモジュールが物理的に反転実装されているため、素の状態では映像が
  左右反転して出る。libcamera の Orientation はアプリが設定するものなので
  ブラウザには効かず、外部から v4l2-ctl で HFLIP を立てても libcamera が
  configure のたびに 0 で上書きする。そこでドライバ側で吸収する。

変更点（orientation-fix.patch）:
  1. HFLIP コントロールの意味を反転させ、コントロール 0（= libcamera が
     常に書き込む値）でセンサーの水平反転が有効になるようにした
  2. 反転すると Bayer 位相が1画素ずれるため、報告する media bus code を
     実際の読み出し順に追従させた（BGGR / GBRG / GRBG / RGGB）
     libcamera の CIO2Device::configure() は setFormat() 後の code から
     CIO2 の出力フォーマットを決めるので、これで自動的に整合する

superseded/modify-layout.patch:
  最初に試した版。HFLIP/VFLIP に V4L2_CTRL_FLAG_MODIFY_LAYOUT を立てるだけの
  もので、IPU3 では効果がなかった（IPU3 パイプラインは bayerOrder() を
  一度も呼ばないため）。記録として残してある。再試行しないこと。

導入（既存の 1.0 を中身ごと差し替える）:
  sudo cp ov5693.c /usr/src/ov5693-fix-1.0/
  sudo dkms build -m ov5693-fix -v 1.0 --force
  sudo dkms install -m ov5693-fix -v 1.0 --force
  再起動

撤去:
  sudo dkms remove -m ov5693-fix -v 1.0 --all
    ↑ 効かない場合は  sudo dkms remove ov5693-fix/1.0 --all

検証:
  bash tools/test_mirror_color.sh 2 1280 720
  通常撮影の R/G・B/G が 0.97 前後のままで、かつ画が反転していなければ成功。
