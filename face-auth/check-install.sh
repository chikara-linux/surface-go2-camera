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
