#!/bin/bash
# dpkg が何かを入れ替えた直後に導入状態を点検し、崩れていれば通知する。
#
# systemd のユーザー側 path unit（patch-watch.path）が /var/lib/dpkg/status の
# 変化で起動する。root のフックは使わない。点検そのものは check-install.sh。
#
# 背景: plasma-mobile と howdy に自前の改変がある。Discover や apt の更新で
# 黙って上書きされると、顔認証の起動ロジックが素の挙動に戻る。戻っても
# 締め出しはしない設計だが、戻ったことは知らせる必要がある。
export LC_ALL=C.UTF-8
HERE="$(cd "$(dirname "$0")" && pwd)"

# dpkg の書き込みが終わるのを少し待つ（status は複数回書かれる）
sleep 20

out="$("$HERE/check-install.sh" 2>&1)"; rc=$?
ng="$(printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g' | grep -E ' NG ' | sed -E 's/^ *NG *//')"

# 前回と同じ結果なら黙る。段階的に導入している最中、未導入の項目で毎回
# 鳴らないようにするため。変化（新しく崩れた / 直った）だけを知らせる。
STATE="$HOME/.local/state/patch-watch.last"
mkdir -p "$(dirname "$STATE")"
prev="$(cat "$STATE" 2>/dev/null || true)"
printf '%s' "$ng" > "$STATE"
if [ "$ng" = "$prev" ]; then
  exit 0
fi
if [ -z "$ng" ]; then
  notify-send -a "導入点検" "パッケージ更新後の点検: すべて正常に戻りました" 2>/dev/null || true
  exit 0
fi
notify-send -u critical -a "導入点検" "パッケージ更新後の点検で問題があります" \
  "$(printf '%s\n\n詳細: check-install.sh' "$(printf '%s\n' "$ng" | head -6)")" 2>/dev/null || true
printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g'
exit "$rc"
