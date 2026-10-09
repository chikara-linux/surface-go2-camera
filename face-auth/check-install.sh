#!/bin/bash
# 顔認証まわりの導入状態を点検する。システム更新のあとに実行する。
#
# 何かが壊れていれば終了コード 1。読み取りのみで、修復はしない。
#
# 背景: 2026-09-04、カーネル更新（7.0.0-30 → 31）後の再起動で発光体の
# モジュールが読み込まれず、顔認証が失敗するようになった。原因は
# /etc/modules-load.d/ による早すぎる読み込みで、症状は「認証は起動するが
# 発光体が点かない」という分かりにくいものだった。更新のたびに全体を
# 機械的に点検できるようにする。
export LC_ALL=C

REPO="${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
SRC="$REPO/ir-face/boy-howdy/howdy/src"
INST=/usr/lib/x86_64-linux-gnu/howdy
NG=0

ok()   { printf '  \033[32m OK \033[0m %s\n' "$1"; }
ng()   { printf '  \033[31m NG \033[0m %s\n' "$1"; NG=1; }
note() { printf '       %s\n' "$1"; }

echo "== 改変したファイルがリポジトリと一致しているか =="
if [ ! -d "$SRC" ]; then
  note "boy-howdy のソースツリーが見つからないので、この項目は飛ばす。"
  note "確認したい場合: REPO=/path/to/camera $0"
  note "（公開リポジトリには patches/ のみ入っており、ソースは含まれない）"
fi
for f in compare.py recorders/ipu3_ir_reader.py recorders/video_capture.py; do
  if [ ! -d "$SRC" ]; then
    break
  elif [ ! -f "$SRC/$f" ]; then
    ng "$f — リポジトリ側が無い"
  elif [ ! -f "$INST/$f" ]; then
    ng "$f — インストールされていない"
  elif diff -q "$SRC/$f" "$INST/$f" >/dev/null; then
    ok "$f"
  else
    ng "$f — 内容が違う（更新で上書きされた可能性）"
    note "戻す: sudo install -m 644 -o root -g root '$SRC/$f' '$INST/$f'"
  fi
done

echo
echo "== PAM =="
if [ -f /usr/lib/x86_64-linux-gnu/security/pam_howdy.so ]; then
  ok "pam_howdy.so"
else
  ng "pam_howdy.so が無い"
fi
if grep -q '^auth.*pam_howdy.so' /etc/pam.d/kde-fingerprint 2>/dev/null; then
  ok "/etc/pam.d/kde-fingerprint に顔認証が入っている"
else
  ng "/etc/pam.d/kde-fingerprint に pam_howdy の行が無い"
fi
# PIN の経路が無効化されていないことの確認。ここが壊れると締め出される。
if grep -qE '^\s*auth.*pam_howdy.so' /etc/pam.d/kde 2>/dev/null; then
  ng "/etc/pam.d/kde に有効な pam_howdy の行がある（PIN と会話が衝突する）"
  note "この構成で三度ロックアウトした。kde-fingerprint 側に置くこと"
else
  ok "/etc/pam.d/kde は無改変（PIN の経路が安全）"
fi

echo
echo "== 設定 =="
if [ -f "$SRC/config.ini" ]; then
  if diff -q "$SRC/config.ini" /etc/howdy/config.ini >/dev/null 2>&1; then
    ok "config.ini がリポジトリと一致"
  else
    ng "config.ini — リポジトリと内容が違う"
    note "差分: diff '$SRC/config.ini' /etc/howdy/config.ini"
  fi
fi
for k in recording_plugin certainty consecutive_matches eye_reflection_threshold; do
  v=$(grep -E "^$k\s*=" /etc/howdy/config.ini 2>/dev/null | head -1)
  [ -n "$v" ] && ok "$v" || ng "$k が config.ini に無い"
done

echo
echo "== 発光体（DKMS + udev）=="
KVER=$(uname -r)
if dkms status 2>/dev/null | grep -q "tps68470-irled.*$KVER.*installed"; then
  ok "DKMS が現行カーネル($KVER)向けにビルド済み"
else
  ng "DKMS が現行カーネル($KVER)向けにビルドされていない"
  note "直す: sudo dkms install -m tps68470-irled -v 1.0 -k $KVER"
fi
if [ -w /sys/class/leds/tps68470::ir_illuminator/brightness ] 2>/dev/null || \
   [ -e /sys/class/leds/tps68470::ir_illuminator/brightness ]; then
  perm=$(stat -c '%U:%G %a' /sys/class/leds/tps68470::ir_illuminator/brightness)
  case "$perm" in
    *:video\ 66*) ok "LED が存在し権限も正しい ($perm)" ;;
    *)            ng "LED はあるが権限が違う ($perm、期待は root:video 664)"
                  note "udev ルールを確認: /etc/udev/rules.d/99-tps68470-irled.rules" ;;
  esac
else
  ng "LED が無い — モジュールが読み込まれていない"
  note "直す: sudo modprobe tps68470-irled"
fi
# modules-load.d に戻っていないか。ここに置くと起動順序の競合で必ず失敗する。
if [ -e /etc/modules-load.d/tps68470-irled.conf ]; then
  ng "/etc/modules-load.d/tps68470-irled.conf が復活している"
  note "デバイスが用意される前に読み込まれて失敗する。udev の bind で読むこと"
else
  ok "modules-load.d に置かれていない"
fi
if grep -q 'ACTION=="bind"' /etc/udev/rules.d/99-tps68470-irled.rules 2>/dev/null; then
  ok "udev の bind ルールがある"
else
  ng "udev の bind ルールが無い（再起動で発光体が消える）"
fi

echo
echo "== 休止状態からの復帰（int3472-tps68470-fix）=="
# 上流のドライバは休止状態で切れた TPS68470 の設定を書き戻さず、復帰後に
# 赤外線・背面カメラが -121 で動かなくなる（2026-10-09）。DKMS 版が要る。
f=$(modinfo -F filename intel_skl_int3472_tps68470 2>/dev/null)
case "$f" in
  */updates/dkms/*) ok "TPS68470 のドライバは休止状態の復元つきの版" ;;
  "")               note "intel_skl_int3472_tps68470 が見つからない" ;;
  *)                ng "TPS68470 のドライバが上流のまま（休止状態から戻るとカメラが動かない）"
                    note "直す: ~/開発・検証/camera/tps68470-hibernate/dkms/int3472-tps68470-fix-1.0/README.md" ;;
esac

echo
echo "== 前面カメラ（ov5693-fix）=="
# 向き補正で水平反転を常時有効にしているため、ビニングモードでは映らない。
# allow_binning パラメータがあれば修正済みのモジュール（2026-10-06）。
if modinfo -F parm ov5693 2>/dev/null | grep -q '^allow_binning'; then
  ok "ov5693 はビニング無効化済みの版"
  if [ "$(cat /sys/module/ov5693/parameters/allow_binning 2>/dev/null)" = Y ]; then
    ng "allow_binning=Y で読み込まれている（640x480 などで黒くなる）"
    note "/etc/modprobe.d/ の ov5693 の options を確認"
  fi
else
  ng "ov5693 が古い版（640x480 などの要求で前面カメラが黒くなる）"
  note "直す: sudo cp ~/開発・検証/camera/ov5693-fix-1.0/ov5693.c /usr/src/ov5693-fix-1.0/ → dkms build/install → 再起動"
fi

echo
echo "== 前面カメラの画質（改造した IPU3 IPA とチューニング）=="
# チューニングの gamma は改造版の IPA でしか効かない。片方だけだと露出だけが
# 上がって背景が白く飛ぶ。両方揃っているか、Ubuntu の libcamera 更新で改造版が
# 使われなくなっていないかを見る（2026-10-06）。
IPADIR=/usr/local/lib/libcamera/ipa
TUN=/usr/share/libcamera/ipa/ipu3/ov5693.yaml
LCVER=$(dpkg-query -W -f='${Version}' libcamera0.7 2>/dev/null)
have_gamma=0; grep -qE '^\s*gamma:' "$TUN" 2>/dev/null && have_gamma=1
if [ -f "$IPADIR/ipa_ipu3.so" ]; then
  ok "改造版の IPA がある ($IPADIR/ipa_ipu3.so)"
  built=$(cat "$IPADIR/BUILT_AGAINST" 2>/dev/null)
  if [ "$built" = "$LCVER" ]; then
    ok "改造版は入っている libcamera と同じ版向け ($LCVER)"
  else
    ng "libcamera が更新された（改造版: ${built:-不明} / 入っている版: ${LCVER:-不明}）"
    note "取り決めが変わっていれば改造版は使われず、標準の IPA に戻っている（ガンマ 1.1）"
    note "再ビルド: ~/開発・検証/camera/libcamera-ipa/build.sh → 導入"
  fi
  if grep -qE '^\s*-\s*'"$IPADIR"'\s*$' /etc/libcamera/configuration.yaml 2>/dev/null; then
    ok "/etc/libcamera/configuration.yaml が改造版を指している"
  else
    ng "/etc/libcamera/configuration.yaml が無いか、改造版の場所を指していない"
  fi
  # 利用者側の設定ファイルがあると、/etc のものは丸ごと無視される
  if [ -f "$HOME/.config/libcamera/configuration.yaml" ]; then
    ng "~/.config/libcamera/configuration.yaml がある（/etc の設定が無視される）"
  fi
  [ "$have_gamma" = 1 ] && ok "チューニングに gamma がある" \
                        || ng "チューニングに gamma が無い（改造版が入っていても効かない）"
else
  if [ "$have_gamma" = 1 ]; then
    ng "チューニングに gamma があるのに改造版の IPA が無い（露出だけ上がり背景が白く飛ぶ）"
  else
    note "改造版の IPA は導入していない"
  fi
fi

echo
echo "== 復旧スイッチ（固まったときの保険）=="
QSDIR="$HOME/.local/share/plasma/quicksettings/org.kde.plasma.quicksetting.lockerrestart"
if [ -x /usr/local/bin/lockscreen-restart ]; then
  if [ -f "$REPO/surface-go2-camera/face-auth/lockscreen-restart.sh" ] && \
     ! diff -q "$REPO/surface-go2-camera/face-auth/lockscreen-restart.sh" \
               /usr/local/bin/lockscreen-restart >/dev/null 2>&1; then
    ng "lockscreen-restart — リポジトリと内容が違う"
  else
    ok "lockscreen-restart"
  fi
else
  ng "/usr/local/bin/lockscreen-restart が無い"
fi
# LC_ALL=C だと日本語の notify-send が失敗し、2 回目の警告が無言で消える
if grep -q '^export LC_ALL=C$' /usr/local/bin/lockscreen-restart 2>/dev/null; then
  ng "lockscreen-restart が LC_ALL=C を使っている"
  note "日本語の notify-send が失敗し、警告が出なくなる。C.UTF-8 にすること"
else
  ok "ロケール設定（通知が出せる）"
fi
if [ -f "$QSDIR/contents/ui/main.qml" ]; then
  p=$(grep -oE '"[^"]*lockscreen-restart[^"]*"' "$QSDIR/contents/ui/main.qml" | tr -d '"')
  if [ "$p" = /usr/local/bin/lockscreen-restart ]; then
    ok "タイルの参照先 ($p)"
  else
    ng "タイルの参照先が違う ($p)"
  fi
else
  ng "クイック設定タイルが無い"
fi
if kreadconfig6 --file plasmamobilerc --group QuickSettings --key enabledQuickSettings 2>/dev/null \
   | grep -q lockerrestart; then
  ok "タイルが有効になっている"
else
  ng "タイルが有効になっていない"
fi
# QML はプラグイン読み込み時に一度しか読まれない。plasmashell より新しければ未反映。
if [ -f "$QSDIR/contents/ui/main.qml" ] && pgrep -x plasmashell >/dev/null; then
  qml_t=$(stat -c %Y "$QSDIR/contents/ui/main.qml")
  sh_t=$(date -d "$(ps -o lstart= -p "$(pgrep -x plasmashell | head -1)")" +%s 2>/dev/null || echo 0)
  if [ "$sh_t" -gt 0 ] && [ "$qml_t" -gt "$sh_t" ]; then
    ng "QML が plasmashell より新しい — タイルは古い内容で動いている"
    note "plasmashell を再起動するか、次のログイン時に反映される"
  else
    ok "QML は plasmashell に読み込まれている"
  fi
fi

echo
echo "== plasma-mobile の自前パッチと更新保護 =="
PM_BASE=6.6.5-0ubuntu0.1          # パッチを当てている上流の版
PM_VER=$(dpkg-query -W -f='${Version}' plasma-mobile 2>/dev/null)
case "$PM_VER" in
  *+chikara*) ok "plasma-mobile は自前ビルド ($PM_VER)" ;;
  *)          ng "plasma-mobile が素の版に戻っている ($PM_VER)"
              note "当て直し: plasma-mobile-patch build → Timeshift → install" ;;
esac
LS=/usr/share/plasma/shells/org.kde.plasma.mobileshell/contents/lockscreen
for f in LockScreen.qml LockScreenState.qml; do
  if grep -q 'chikara: face-auth-lockscreen v1' "$LS/$f" 2>/dev/null; then
    ok "$f に起動ロジックのパッチが入っている"
  else
    ng "$f にパッチが無い（howdy-wake が注入に戻る。締め出しはしない）"
  fi
done
if apt-mark showhold 2>/dev/null | grep -qx plasma-mobile; then
  ok "plasma-mobile は hold（Discover / apt で上書きされない）"
else
  ng "plasma-mobile が hold されていない"
  note "直す: sudo apt-mark hold plasma-mobile plasma-mobile-tweaks"
fi
ARCH=$(apt-cache madison plasma-mobile 2>/dev/null | awk -F'|' '/Packages/ {gsub(/ /,"",$2); print $2}' | sort -V | tail -1)
if [ -n "$ARCH" ] && dpkg --compare-versions "$ARCH" gt "$PM_BASE"; then
  ng "アーカイブに新しい plasma-mobile がある ($ARCH > $PM_BASE)"
  note "パッチを新版に当て直す時期。hold のままだと上流の修正を受け取れない"
else
  ok "アーカイブの版はパッチの基準と同じ (${ARCH:-不明})"
fi
PDIR="$HOME/開発・検証/plasma-mobile-window-patch"
if [ -f "$PDIR/plasma-mobile-lockscreen-face-auth.patch" ]; then
  if diff -q "$PDIR/plasma-mobile-lockscreen-face-auth.patch" \
             "$REPO/surface-go2-camera/face-auth/plasma-mobile/plasma-mobile-lockscreen-face-auth.patch" >/dev/null 2>&1; then
    ok "パッチ置き場とリポジトリのパッチが一致"
  else
    ng "パッチ置き場とリポジトリのパッチが違う"
  fi
else
  ng "パッチ置き場に plasma-mobile-lockscreen-face-auth.patch が無い"
fi
if systemctl --user is-enabled patch-watch.path >/dev/null 2>&1; then
  ok "更新の監視 (patch-watch.path) が有効"
else
  ng "更新の監視 (patch-watch.path) が有効になっていない"
  note "直す: systemctl --user enable --now patch-watch.path"
fi

echo
echo "== 常駐サービス =="
if systemctl --user is-enabled howdy-wake.service >/dev/null 2>&1; then
  st=$(systemctl --user is-active howdy-wake.service)
  [ "$st" = active ] && ok "howdy-wake ($st)" || ng "howdy-wake が動いていない ($st)"
else
  ng "howdy-wake が有効になっていない"
fi

echo
if [ "$NG" = 0 ]; then
  echo "すべて正常。"
else
  echo "問題があります。上の NG を確認してください。"
fi
exit $NG
