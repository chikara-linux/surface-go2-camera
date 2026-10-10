# sudo と polkit（GUI の管理者認証）で顔認証を使う（2026-10-10）

それまでは「画面ロックの解除のみ」に限っていた（README「位置づけ」）。利用者の
判断で、sudo と polkit にも広げる。

## 使い方

**パスワード欄を空のまま Enter を押すと顔認証になる。** パスワードを打てば
従来どおり。何もしなければカメラは動かない。

| 場面 | 操作 |
|---|---|
| 端末で `sudo` | `[sudo: authenticate] パスワード:` で何も打たずに Enter → 赤外線が光り、通れば実行 |
| GUI の管理者認証（Discover、システム設定など） | パスワード欄を空のまま OK |

## 方式と理由

PAM の auth を次の順にする（`etc/pam.d-sudo` ほか）。

    auth  [success=2 auth_err=ignore default=die]  pam_unix.so nullok   ← まずパスワード
    auth  [success=1 default=ignore]  pam_howdy.so         ← 違う・空なら顔
    auth  requisite                   pam_deny.so
    auth  required                    pam_permit.so
    auth  optional                    pam_cap.so

**顔認証を自動で走らせない（Enter が同意になる）。** 理由は三つ。

1. **背後で動くプログラムが sudo を呼んでも、利用者の操作なしには通らない。**
   顔認証を先頭に置くと、端末の前に座っているだけで、スクリプトの中の sudo が
   パスワードの入力なしに root になる（赤外線が光るだけ）
2. **polkit のダイアログで、何を許可するのかを読む前に通ってしまわない**
3. パスワードで入る人に、顔認証の待ち（最長 5 秒）をかけない

**顔認証に進むのは、パスワードが「違う／空」と確定したとき（`auth_err`）だけ。**
問い合わせ自体ができなかったとき（`sudo -n`、polkit のダイアログでキャンセル、
端末が無い等）は `default=die` でその場で拒否する。最初の設計（`default=ignore`）では、
**`sudo -n` を呼ぶスクリプトが何も押さずに顔で root になり、polkit のダイアログを
キャンセルしても顔で許可されていた。**導入前のレビュー（2026-10-10）で見つけて塞いだ。
sudo-rs は `-n` でも PAM の認証を呼ぶ（問い合わせを拒否する形で）ことをソースで確認済み。

**パスワードを先に評価する。** pam_howdy が壊れても（読み込めない・カメラが無い・
DKMS が外れた）パスワードの経路は影響を受けない。`default=ignore` なので失敗は
次の行へ流れるだけ。sudo を壊して管理者権限を失う危険を、構造で小さくしている。

空のパスワードで pam_unix が失敗すると失敗遅延（約 2 秒）が登録されるが、
Linux-PAM は認証全体が失敗したときにしか待たせないので、顔で通れば待たない。

## 変えたもの

| 部品 | 変更 | 理由 |
|---|---|---|
| pam_howdy（`patches/05`） | 子プロセスに `HOWDY_PAM_SERVICE`（PAM サービス名）を渡す | compare.py が「どこから呼ばれたか」を知るため |
| pam_howdy（`patches/05`） | `openlog()` をやめ、`pam_howdy: ` を付けて施設を明示して書く | `openlog()` はプロセス全体の設定で、顔認証のあとの sudo の監査ログ（誰が何を実行したか）が `pam_howdy` 名義になり、`sudo` で探す監査から漏れていた |
| compare.py（`patches/01`） | ロック画面専用の処理（4 つのガード、失敗の通知、成功報告の遅延、親の監視）を `kde-fingerprint` のときだけ働かせる | sudo/polkit は解錠中に呼ばれるので、「解錠済みなら退く」ガードで必ず弾かれていた。root で動くので /run/user/0 の印や通知も意味が無い |
| `/etc/pam.d/sudo`・`sudo-i`・`polkit-1` | 上の auth 順 | — |
| polkit の helper | サンドボックスの緩和（`etc/polkit-agent-helper-howdy.conf`） | 下記 |

変数が無い（古い pam_howdy）か空なら、従来どおりロック画面として扱う。

## polkit の helper のサンドボックス

polkit 127 は PAM を `polkit-agent-helper@.service`（root）で走らせる。強い
サンドボックスがあり、既定では `/dev/null` 以外のデバイスに触れず、`/sys` も
読み取り専用。pam_howdy が起動する compare.py はカメラも照明も使えない。

開けるのは次の 3 つだけ。ほかの制限（ネットワーク遮断、ホーム不可視、
システムの読み取り専用、システムコールの絞り込み、W^X など）はそのまま。

- `PrivateDevices=no` ＋ `DeviceAllow=char-video4linux rw` / `char-media rw`
  （デバイスは cgroup の許可リストで絞ったまま）
- `ReadWritePaths=` 照明の brightness（`/sys/devices/.../leds/tps68470::ir_illuminator`）

`polkit-sandbox-test.sh` で、polkit に触らずに同じサンドボックスで compare.py を試せる。

## sudo は sudo-rs

Ubuntu 26.04 の sudo は Rust 版の sudo-rs。PAM のサービス名は `sudo` / `sudo-i`。
端末の入力は空行も含めてそのまま PAM に渡す（特別扱いしない）ことをソースで確認。

## 安全性について（承知の上で広げる）

- **顔で root になれるようになる。** 以前の方針「顔認証はパスワードより弱く、
  突破されても得られる権限が増えない構成」を変える判断
- 他人受入率は未確定（協力者 1 名・分離しやすい条件のみ）。網膜反射（開眼・注視）と
  連続 3 フレームの一致で補っている
- 端末の前にいない状態でも、誰かが空 Enter を押して自分の顔を見せれば通る。
  ただしそれはロック解除と同じ条件で、解錠済みのセッションを持っている時点で
  ロック画面と同程度の権限は既に得ている
- SSH 経由の sudo では顔認証は使わない（`abort_if_ssh = true`）

## 机上で確かめたこと

| 観点 | 結果 |
|---|---|
| ロック画面の挙動 | `kde-fingerprint` は従来どおりロック画面扱い。古い pam_howdy でも同じ |
| sudo/polkit でガードに弾かれないか | 4 つのガードはロック画面のときだけ |
| root で動いたときの書き込み | 成功の印は try で包まれていて失敗しても無害。スナップショットは無効 |
| 通知 | ロック画面のときだけ（root からは利用者のセッションバスに届かない） |
| pam_howdy が壊れたら | パスワードが先なので sudo は使える |
| PAM ファイルの書き間違い | 導入前に `pamtester` で試験用サービスを叩く。導入時は root のシェルを開いておく |
| pam_howdy.so の差し替え | `install`（別の inode）で置く。cp で上書きすると、読み込み中のプロセスが落ちうる |
| パッケージ更新 | `/etc/pam.d/sudo`・`sudo-i` は sudo-common の conffile。更新時は dpkg が問い合わせ、既定は手元を残す。`check-install.sh` が差分と、展開元の common-auth との食い違いを知らせる |
| 展開元との一致 | `common-auth` の auth 行と、飛び先の数以外は一致（行末の空白だけ違う） |

## 導入と確認（2026-10-10）

5 段階で導入し、各段階で実機で確かめた。

| 段階 | 確認 | 結果 |
|---|---|---|
| 1 pam_howdy・compare.py | ロック画面で解除、`HOWDY_PAM_SERVICE=kde-fingerprint` が届く | 従来どおり |
| 2 試験用サービス（pamtester） | パスワード / 空 Enter / 顔を背ける / 問い合わせ不能（`< /dev/null`） | 光らず成功 / 光って成功 / 5 秒で失敗 / 光らず拒否 |
| 3 polkit と同じ防御で compare.py | 緩和なし / あり | カメラが見えず失敗 / 成功（照明も点灯） |
| 4 sudo | パスワード / 空 Enter / `sudo -n` / `sudo -i` 空 Enter | 光らず成功 / 光って成功 / 光らず拒否 / 光って成功 |
| 5 polkit（pkexec） | パスワード / 空で OK / キャンセル | 光らず成功 / 光って成功 / 光らず拒否 |

導入中に見つけて直した副作用:

- **sudo の監査ログの名乗り**（上記）。直したあと、監査ログは `sudo[pid]: chikara : ... COMMAND=...`、
  顔認証は `sudo[pid]: pam_howdy: Login approved` の形になった
- 9/4 の調査の残骸 `/etc/pam.d/howdy-probe`（`auth required pam_howdy.so` だけ＝顔だけで通る
  サービス）が残っていたので削除した

無関係と確かめたもの:

- `sudo -i` で出る `pam_systemd: Failed to check if /run/user/0/bus exists` はパスワードでも出る
  （sudo-rs の既存の挙動）
- polkit のダイアログの QML の警告、`unix-group:admin` の警告、libpng のエラーは以前から出ている
- ロック画面の「赤外線の消灯 → 解除の承認」の約 3 秒は以前からある（別件として調べる余地あり）

## 戻す

    sudo cp ~/開発・検証/camera/sudo-polkit-backup-2026-10-10/{sudo,sudo-i} /etc/pam.d/
    sudo rm /etc/pam.d/polkit-1
    sudo rm -r /etc/systemd/system/polkit-agent-helper@.service.d && sudo systemctl daemon-reload

pam_howdy・compare.py の変更はロック画面に影響しないので戻さなくてよい
（戻すなら同じ退避先の `pam_howdy.so.orig`・`compare.py.orig`）。
