#!/bin/bash
# polkit-agent-helper と同じサンドボックスで compare.py を root で走らせる試験。
# polkit 自体には触らない。基のユニットと、導入予定の緩和を重ねて systemd-run に渡す。
#
#   sudo ./polkit-sandbox-test.sh                 … 緩和あり（導入予定の形）
#   sudo ./polkit-sandbox-test.sh --no-dropin     … 緩和なし（いまの polkit の状態）
#   ./polkit-sandbox-test.sh --show [--no-dropin] … 渡す設定を表示するだけ（root 不要）
#
# 終了コード 0 なら、その状態の polkit で顔認証が通る見込み。
set -u
export LC_ALL=C.UTF-8
HERE="$(cd "$(dirname "$0")" && pwd)"
DROPIN="$HERE/etc/polkit-agent-helper-howdy.conf"
SHOW=0
for a in "$@"; do
  case "$a" in
    --no-dropin) DROPIN="" ;;
    --show) SHOW=1 ;;
  esac
done

# 基のユニットに緩和を重ねる。systemd のドロップインと同じく、緩和にある項目は
# 上書きし、DeviceAllow と ReadWritePaths だけは積み上げる。重複した -p の解釈を
# systemd-run に任せず、ここで一つに決めてから渡す。
merge() {
  python3 - "$DROPIN" <<'PY'
import subprocess, sys

def items(text):
    out, on = [], False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            on = (line == "[Service]")
            continue
        if not on or not line or line.startswith("#") or "=" not in line:
            continue
        out.append(tuple(line.split("=", 1)))
    return out

base = items(subprocess.run(["systemctl", "cat", "polkit-agent-helper@.service"],
                            capture_output=True, text=True).stdout)
drop = items(open(sys.argv[1]).read()) if sys.argv[1] else []
skip = {"ExecStart", "StandardInput", "StandardOutput", "SuccessExitStatus", "Type"}
accum = {"DeviceAllow", "ReadWritePaths"}
override = {k for k, _ in drop if k not in accum}
for k, v in base:
    if k in skip or k in override:
        continue
    print(f"{k}={v}")
for k, v in drop:
    print(f"{k}={v}")
PY
}

mapfile -t settings < <(merge)
if [ "$SHOW" = 1 ]; then
  printf '%s\n' "${settings[@]}"
  exit 0
fi

props=()
for s in "${settings[@]}"; do props+=(-p "$s"); done
label=なし; [ -n "$DROPIN" ] && label=あり
echo "== サンドボックスの設定 ${#settings[@]} 項目で実行（緩和: $label）=="
printf '   %s\n' "${settings[@]}" | grep -E 'Private|Device|ReadWrite|MemoryDeny|ProtectKernelTunables'
echo "   カメラを見ていてください"
systemd-run --quiet --wait --pipe --collect "${props[@]}" \
  /usr/bin/env -i PYTHONPATH=/usr/lib/x86_64-linux-gnu/howdy PATH=/usr/local/bin:/usr/bin:/bin \
  HOWDY_PAM_SERVICE=polkit-1 /usr/bin/python3 /usr/lib/x86_64-linux-gnu/howdy/compare.py chikara
rc=$?
case $rc in
  0)  echo "== 終了コード 0: 顔認証に成功 ==" ;;
  11) echo "== 終了コード 11: 時間切れ（顔が一致しなかった・カメラを見ていなかった）==" ;;
  *)  echo "== 終了コード $rc: カメラが開けない等の失敗 ==" ;;
esac
exit $rc
