ov8865-fix — 背面カメラの AE ハンチング（明暗の往復）を止める DKMS パッケージ

症状:
  背面カメラで、露出が上限に張り付きゲインで調整する領域に入ると、
  約1.2秒周期で明るさが往復する。前面カメラでは起きない。

原因（実測で特定）:
  ドライバが analogue_gain の step を 128 と宣言していた。
    ov8865  min=128 max=2048 step=128  → 16段（1.0x, 2.0x, ... 16.0x）だけ
    ov5693  min=1   max=127  step=1    → 127段（前面が安定なのはこのため）
  ゲインレジスタ 0x3508/0x3509 は 13bit で 128 が 1.0 倍、つまりハードウェアは
  1/128 刻みで 1921 段設定できる。step=128 はそれを不必要に潰していた。
  AGC が 1.3 倍を要求してもハードは 1.0 か 2.0 しか出せず、暗すぎ↔明るすぎを
  往復する。これがハンチングの正体。

修正（gain-step.patch）:
  step を 128 から 1 に変えるだけ。

切り分けの記録（再試行不要）:
  ・オートフォーカスは無関係。Af あり/なしで振幅・周期とも同一だった
  ・AGC の目標輝度も無関係。0.20〜0.40 のどれでも同一だった
  ・露出とゲインの遅延差 (sensorDelays) も原因ではない。露出は 33237 に
    固定されたまま一切動かず、両センサーとも sensorDelays は空で同条件なのに
    前面は同じゲイン領域で完全に安定していた。libcamera の再ビルドは不要

導入:
  sudo cp -r ~/ov8865-fix-1.1 /usr/src/
  sudo dkms install -m ov8865-fix -v 1.1
  再起動（ov8865 の rmmod は cio2 の notifier を崩すため）

撤去:
  sudo dkms remove -m ov8865-fix -v 1.1 --all
    ↑ 効かない場合は  sudo dkms remove ov8865-fix/1.1 --all

検証（root 不要）:
  1. 粒度が変わったか
     v4l2-ctl -d /dev/v4l-subdev7 --list-ctrls | grep analogue_gain
       → step=1 になっていれば成功
  2. ハンチングが止まったか
     cam -c1 -s width=2560,height=1920 --capture=80 --metadata \
       | grep -oE 'AnalogueGain = [0-9.]+'
       → 収束後にゲインが一定値に落ち着けば成功

superseded/vblank-stale-value.patch:
  別件で試した版。コントロールの現在値がモード切替に追従しないバグは
  実在したが、3264x1836 の停止は治らなかったので撤去した。
