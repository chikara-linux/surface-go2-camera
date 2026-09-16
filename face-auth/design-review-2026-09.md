# 起動ロジックの再検討（2026-09-16）

赤外線顔認証の「起動ロジック」——いつカメラを回し、いつ回さないか——を
ゼロから組み直し、現行実装（主案）と突き合わせた記録。

要件は次の二つで、**どちらも等価に重い。**

| | 要件 | 指標 |
|---|---|---|
| 前 | 解除したいときに速やかにカメラが起動し、解除される | 契機 → 発光 のリードタイム |
| 後 | 関係ないタイミングでは起動しない | 説明のつかない発光の件数 |

対象は起動ロジックのみ。顔の判定（一致度・網膜反射・連続一致）は扱わない。

---

## 1. 事実の棚卸し

判断に使った事実。出典は末尾。「実測」は 2026-09-06〜16 の 10 日間、
`irwatch`（発光体の常時記録）と journal から取った。

### 1-1. 観測データ（10 日間）

| 項目 | 値 |
|---|---|
| 発光回数 | 218 |
| うち注入（howdy-wake）起因 | 45（journal の注入ログ） |
| うち自力開始・タッチ起因 | 約 170 |
| 解除直後の二重起動 | **1 件**（9/6 07:49、修正済み） |
| 説明のつかない発光 | **0 件**（グリーター不在の 8 件はすべて検証スクリプト） |
| 点灯時間 | 中央 1.3 秒、最大 7.1 秒 |

**後の要件は、現行実装で既に満たされている。** 主案の欠点は前の要件側にある。

### 1-2. リードタイム（契機 → 発光）

| 経路 | n | 中央 | 最小 | 最大 |
|---|---|---|---|---|
| サスペンド復帰 | 114 | **3.9 秒** | 0.9 | 16.1 |
| 画面点灯 → 注入 | 44 | **2.6 秒** | 1.9 | 15.4 |

プロセス自体の起動は 0.8 秒（`import cv2` が 407 ms、
「[点灯までの時間の内訳](README.md#点灯までの時間の内訳)」）。
**残りは待ち時間で、設計が左右できる部分。**

復帰経路の分布。76/114 件が 3.0〜4.5 秒に集中している。

    0.0- 2.0 秒:   7
    2.0- 3.0 秒:   2
    3.0- 3.5 秒:  29
    3.5- 4.5 秒:  47   ← resume_delay の待ち（後述）
    4.5- 6.0 秒:   8
    6.0-10.0 秒:   9
    10.0-20.0 秒:  12   ← 自力で始まらず、利用者が触るまで待った回

### 1-3. kscreenlocker（Plasma/6.6 のソースで確認）

| # | 事実 | 出典 |
|---|---|---|
| K1 | 非対話認証を始めるのは `PamAuthenticators::startAuthenticating()` だけ。`state == Authenticating` か `graceLocked` なら何もしない。全認証器の `tryUnlock()` を呼ぶ | pamauthenticators.cpp |
| K2 | **`PamAuthenticator::tryUnlock()` は無条件に `m_unlocked = false` にしてから `authenticate()` を投げる** | pamauthenticator.cpp |
| K3 | 失敗が前回の失敗から 2 秒以内なら回数を数え、**4 回目で `m_unavailable = true`。戻す処理は無い**（グリーターの寿命まで） | pamauthenticator.cpp |
| K4 | `PamWorker::authenticate()` は `m_nextAttemptAllowedTime` より前なら `failed()` を出して戻る（Bug 515299 の修正。**6.6.5 に入っており、Ubuntu の 6.6.5 パッケージは自前の backport を落としている＝修正済み**） | pamworker、changelog.Debian |
| K5 | グリーターは `Qt.quit()` を受けると `isUnlocked()` を確認し、**偽なら「Greeter tried to quit without being unlocked」を出して終了しない** | greeterapp.cpp |
| K6 | 正常終了は `exitCode == 0 && NormalExit` のみ。**SIGTERM は exit 1 で異常扱い。** 異常終了は 4 回目で `EmergencyWindow`。カウンタは解錠時にしか戻らない | ksldapp.cpp、greeter/main.cpp |
| K7 | ロック中に `lock(Immediate)` が来ると `SIGUSR1` を送る。グリーター側は `graceLockEnded()` → `setGraceLocked(false)` と `locked` プロパティの書き込みだけ。**認証は再開しない** | ksldapp.cpp、greeterapp.cpp |
| K8 | D-Bus の `Lock()` / `SetActive(true)` は `lock(Immediate)`。`SimulateUserActivity()` は KIdleTime を突くだけ | interface.cpp |
| K9 | `PrepareForSleep(false)`（復帰）は何もしない | ksldapp.cpp |
| K10 | **画面（QScreen）が追加されるたびに `handleScreen()` が新しい QQuickView を作り、QML を読み直す。** 削除時は view を破棄 | greeterapp.cpp |

### 1-4. Plasma Mobile のロック画面（6.6.5、実機と upstream master で同一）

| # | 事実 |
|---|---|
| M1 | `startAuthenticating()` の呼び出し元は 3 つだけ: `Component.onCompleted`、`tryPassword()`、`graceLockTimer`（対話型失敗の 3 秒後） |
| M2 | `onFailed(kind != 0)` は `return`。**非対話の失敗（顔認証タイムアウト）では何も再開しない** |
| M3 | `tryPassword()` は `startAuthenticating()` を呼んでから `respond(password)` する。空 PIN でも送る |
| M4 | `org.kde.plasma.private.mobileshell.dpmsplugin` を import 済み。`DPMSUtil` は `dpmsTurnedOn(screen)` / `dpmsTurnedOff(screen)` を持つ。**使っているのは `onDpmsTurnedOff`（キーパッドを閉じる）だけ** |
| M5 | `PasswordBar.qml` の TextField は `onEditingFinished: if (textField.focus) root.enter()`。focus は「キーパッドが開いていてキーボードモード」のとき |
| M6 | 通知や充電で画面を点ける仕組みは mobileshell に無い（`turnDpmsOn` の呼び出し元なし。`doubleTapWakeup` 設定のみ） |

### 1-5. pam_howdy / compare.py（実機の設定）

| # | 事実 |
|---|---|
| H1 | `workaround = off`。**pam_howdy 自身の Enter 送出（`EnterDevice`）は使われていない** |
| H2 | 子には `PYTHONPATH` と `PATH` だけを渡す。`HOME` の有無で PAM 起動を判定している |
| H3 | `resume_delay = 3`。`/run/howdy-resume-timestamp` から 3 秒未満なら残りを待つ。**74/85 件が「resumed 1s ago, waiting 2s」** |
| H4 | compare.py のガード: 直前成功(5s) → ロック中 → 画面点灯(2s 待つ) → 撮影権。PAM 起動時のみ |
| H5 | `MIN_SUCCESS_DELAY = 3.5`。注入の印があるときだけ、起動から 3.5 秒経つまで成功を報告しない |

### 1-6. ハードウェア・センサー

| # | 事実 |
|---|---|
| S1 | `iio-sensor-proxy` 稼働。加速度（`AccelerometerTilt`）と照度（`LightLevel`、lux）が D-Bus で取れる。**近接センサーは無い** |
| S2 | 蓋（Type Cover）の開閉は logind に届いていない（10 日間で 0 件） |
| S3 | 画面点灯の契機は電源ボタンとサスペンド復帰の二つ。それ以外は観測されていない |

### 1-7. 参照モデル: Windows Hello

「デバイスが起きてサインイン画面に達したとき」にカメラを起動する。
歩み寄りで起こす Presence Sensing は**専用の近接センサーが前提**で、
Surface Go 2 には無い。つまり Windows でも本機の起動条件は
「画面が点いてロック画面が出ている」であり、主案の前提と同じ。

---

## 2. 制約の再検証

「なぜその制約があるのか」「本当に守るべきか」を一つずつ当たった。

| # | 制約 | 根拠 | 判定 |
|---|---|---|---|
| C1 | グリーターを殺して作り直すのは 3 回まで | K6。SIGTERM は exit 1 で必ず異常扱い | **有効。** 回避不能 |
| C2 | ロック画面は触れるまで非対話認証を再開しない | M1・M2 | **有効だが不正確だった。** 正しくは「Component.onCompleted・tryPassword・graceLockTimer 以外では再開しない」。**画面（出力）が付け直されると QML が作り直され、Component.onCompleted が走る**（K10）。これが復帰経路の「自力で始まる」の正体（1-2、38/40 件で QML 生成の痕跡） |
| C3 | 常駐プロセスはセッションを解錠できない | polkit `auth_admin_keep` | **有効** |
| C4 | Enter でなければ認証は始まらない | M1（tryPassword が唯一の外部契機） | **有効。** ただし理由が「空 PIN の送信が引き金」ではなく「tryPassword が startAuthenticating を呼ぶから」 |
| C5 | D-Bus `Lock()` / SIGUSR1 で再開できるのでは | K7・K8 | **できない。** 新規に確認して却下 |
| C6 | 失敗遅延の窓に成功が落ちると解錠できない（Bug 515299） | §9-4-21 | **機序の説明を訂正する（下記）。** 実測の相関と対処（`MIN_SUCCESS_DELAY`）は有効 |
| C7 | 2 秒以内の失敗が 4 回続くと認証器が死ぬ | K3 | **新規の制約。** 今後どの設計でも守る |
| C8 | PAM 起動の判定は親プロセスではなく環境変数で | H2 | **有効** |
| C9 | 復帰後は表示が点くまで待つ（`resume_delay = 3`） | pam_howdy のコメント「表示より先に会話が始まる」 | **前提が崩れている。** 実測では pam_howdy が待ち始めた時点で画面は既に点いている（howdy-wake の検知が先）。compare.py 側に「画面が点くまで 2 秒待つ」ガードが入った今、二重に待っている |
| C10 | `/etc/pam.d/kde` は無改変 | 利用者の要件。三度の締め出しの教訓 | **有効。** `pam_unix nodelay` で失敗遅延を消す案は `common-auth`（全システム共通）を触ることになり却下 |

### C6 の訂正: 9/5 の締め出しの機序

§9-4-21 では「失敗遅延中に届いた認証を黙って破棄する（515299）」と
説明したが、**515299 の修正は当時すでに入っていた**（K4）。
ソースを追った結果、関与している機構は次の二つ。

1. **`tryUnlock()` が `m_unlocked` を無条件に偽にする**（K2）。
   顔認証の成功で `m_unlocked = true` になったあと、何かが
   `startAuthenticating()` を呼ぶと偽に戻る。その状態で `Qt.quit()` が
   来ると K5 により**終了を拒否し続ける**。9/5 のログ「Greeter tried to
   quit without being unlocked」×6 はまさにこれ。
2. 成功の直後に `startAuthenticating()` を呼ぶ何か——**成功の 1〜3 ms 後に
   `qml: attempt password` が出る**（10 日間の成功 194 件のうち 48 件、
   直後に `unix_chkpwd: no password supplied`）。呼び出し元は
   `tryPassword()` で、QML 側の候補は M5（TextField の focus 喪失で
   `editingFinished` → `enter()`）。キーボードモードでキーパッドを
   開いていた回に限られるため、頻度が 25〜50% になる。

つまり**「解除直後の二重起動」と「9/5 の締め出し」は同じ根から出ている。**
現行の対処（直前成功ガード、`MIN_SUCCESS_DELAY`）はどちらも症状を抑えて
いるが、根は QML 側にある。正確な発火順序（`succeeded` の処理と
`tryPassword` のどちらが先か）までは実機で計装しないと確定できない。

---

## 3. ゼロベースの再構築（対案）

### 3-1. 契機の候補

「解除したい」を示す信号として何が取れるか。

| 信号 | 取り方 | 前（速さ） | 後（誤起動） | 判定 |
|---|---|---|---|---|
| 画面が点いた | DPMS（sysfs / KWin） | ◎ 即時 | ◎ 通知や充電では点かない（M6） | **採用** |
| 出力が付け直された | グリーターの QML 再生成（K10） | ◎ | ◎ | 既に自動（復帰経路） |
| 電源ボタン | logind | ○ | △ カバンの中の誤押下 | 画面点灯に包含される |
| 画面に触れた | KWin | ○ | ◎ | 既に自動（tryPassword） |
| 持ち上げた（加速度） | iio-sensor-proxy | ○ ボタン不要 | **×** 鞄の揺れで画面を点けてしまう | 却下（後を悪化） |
| 暗い＋静止（照度＋加速度） | iio-sensor-proxy | − | ◎ 鞄の中の誤起動を抑える | 保留（10 日間で誤起動 0 件、必要が無い） |
| 蓋の開閉 | logind | − | − | 取れない（S2） |

**結論: 契機は「ロック中に画面が点いた」で正しい。** Windows Hello も同じ。

### 3-2. 認証を始める手段の候補

契機を取ったあと、どうやって `startAuthenticating()` に到達させるか。

| 手段 | 副作用 | 制約 | 判定 |
|---|---|---|---|
| A. uinput で Enter（主案） | 空 PIN が対話型を 1 回失敗させ、失敗遅延の窓と `graceLockTimer` の再開を生む。1.2 秒の装置認識待ち | C4・C6・C7 | 動いている。副作用を 3 つのガードと `MIN_SUCCESS_DELAY` で抑えている |
| B. グリーターの再起動 | 3 回で TTY 落ち | C1 | **却下** |
| C. D-Bus `Lock()` / SIGUSR1 | 認証は再開しない | C5 | **却下**（今回ソースで確認） |
| D. **ロック画面 QML に `onDpmsTurnedOn` → `startAuthenticating()` を足す** | 空 PIN なし。失敗遅延の窓なし。装置認識待ちなし | C7（絞りを入れる） | **対案の核** |
| E. kscreenlocker（C++）に同等の機能を足す | D と同じ効果。改変対象が大きい | − | D の上流版として検討 |
| F. pam 側で常駐し自前で回す | PAM の会話モデルと合わない | − | 却下 |
| G. 非対話失敗のたびに再開（`onFailed(kind≠0)` → retry） | **画面が点いている限り 5 秒ごとにカメラが回る**（消灯まで最大 300 秒） | 後の要件に反する。C7 | **却下** |

D が成立する根拠:

- 必要な信号（`DPMSUtil.dpmsTurnedOn`）は**ロック画面 QML が既に import している**（M4）
- `startAuthenticating()` は `state == Authenticating` なら無視する（K1）ので、
  復帰経路（QML 再生成で既に走っている）と重なっても二重にならない
- 対話型認証器にも `tryUnlock()` が行くが、プロンプト待ちの状態に戻るだけ。
  直前に PIN を間違えていた場合は「too soon」で `failed(kind=0)` が出て
  「PIN が違います」が一瞬出る。`!waitingForAuth` かつ前回から 3 秒以上で
  絞れば実用上は起きない
- plasma-mobile は既にローカルでパッチを当ててビルドしている
  （`6.6.5-0ubuntu0.1+chikara2`、パッチ 2 本）。**新しい経路ではない**

### 3-3. 対案の全体

```
[契機]                        [開始]                      [ガード]              [判定]
画面点灯（DPMS on）─┐
出力の付け直し ─────┼→ QML が startAuthenticating ─→ pam_howdy ─→ compare.py ─→ 顔判定
画面に触れる ───────┘        （既存の経路）             resume_delay=0    画面点灯 / 撮影権 / 直前成功
```

主案との差分は三つ。

| | 主案 | 対案 |
|---|---|---|
| 画面点灯の契機を誰が拾うか | 常駐サービス（sysfs を poll） | ロック画面 QML（KWin の DPMS 信号） |
| どう始めるか | uinput で Enter → 空 PIN | `startAuthenticating()` を直接 |
| 成功直後の空 PIN 送信 | ガードで吸収 | `tryPassword()` 側で `authenticator.unlocked` なら送らない |

### 3-4. 対案の見積り

| 経路 | 主案（実測中央） | 対案（見積） | 根拠 |
|---|---|---|---|
| 画面点灯 | 2.6 秒 | **約 1.0 秒** | 1.2 秒の装置認識待ちと 0.3 秒の余裕が消える。残りはプロセス起動 0.8 秒 |
| 復帰 | 3.9 秒 | **約 1.5〜2.0 秒** | `resume_delay` の 2 秒待ちが消える（C9）。QML 再生成 → 起動 0.8 秒 |
| 後の要件 | 誤起動 0 件 | 同等以上 | 契機は同じ。空 PIN 起因の再開（graceLockTimer）が無くなる |

---

## 4. 主案と対案の比較

| 観点 | 主案（常駐サービス＋Enter） | 対案（QML パッチ） |
|---|---|---|
| 前: リードタイム | 2.6 / 3.9 秒 | 約 1.0 / 1.5〜2.0 秒 |
| 後: 誤起動 | 0 件（実測） | 同等（契機が同じ） |
| 締め出しの根 | 空 PIN → 失敗遅延の窓。`MIN_SUCCESS_DELAY` で回避 | 空 PIN が無いので窓が無い。成功直後の送信も止める |
| 部品 | howdy-wake（220 行）、uinput、印 2 つ、ガード 4 つ | QML 数十行、ガード 3 つ |
| 改変対象 | 自前のスクリプトのみ | **plasma-mobile のパッケージ**（既存のローカルビルド） |
| 更新への耐性 | 上流更新の影響を受けない | 上流更新ごとにパッチの再適用が要る（既存の 2 本と同じ運用） |
| 上流還元 | 無理（回避策） | **可能。** 成功直後の空 PIN 送信はバグとして、DPMS 再開は機能として提案できる |
| 未検証の点 | なし（10 日稼働） | `dpmsTurnedOn` が本機で確実に届くか。復帰時の発火順序 |

**どちらか一方が全面的に勝ってはいない。** 主案は動いており、後の要件を
満たしている。対案は前の要件で 1.5〜2 秒速く、副作用の根を絶つが、
パッケージのパッチという保守コストを負う。

---

## 5. 統合した最終案

良い部分を取る。**段階を踏み、各段階で実測してから次へ進む。**
どの段階でも後の要件（誤起動 0 件）を `irwatch` で確認する。

### 第 0 段: 待ち時間の是正（主案のまま、リスク最小）

`resume_delay` を 3 → 0 にする。compare.py の「画面が点くまで 2 秒待つ」
ガードが同じ役割を既に果たしている（C9）。復帰経路で約 2 秒、
114/158 件が速くなる。**主案の構造は一切変えない。**

確認: 復帰 → 発光の中央値が 3.9 秒から 2 秒前後に下がること。
カメラの起動失敗が増えないこと（10 日間でカメラ起因のエラーは 0 件）。

### 第 1 段: 成功直後の空 PIN 送信を止める（QML パッチ 1）

`LockScreenState.qml` の `tryPassword()` で、`authenticator.unlocked` が
真なら何もしない。**締め出しと解除直後の二重起動の根を絶つ。**
compare.py の直前成功ガードと `MIN_SUCCESS_DELAY` は残す（多重防御）。

上流へ「非対話認証の成功後に空 PIN を送って `m_unlocked` を戻してしまう」
として報告する材料になる。

### 第 2 段: 画面点灯で直接再開する（QML パッチ 2）

`LockScreen.qml` の `DPMSUtil` に `onDpmsTurnedOn` を足し、
ロック中・`!waitingForAuth`・前回から 3 秒以上（C7）のときだけ
`lockScreenState.restartNoninteractive()` → `startAuthenticating()`。

**実装時に見つけた穴（2026-09-16 同日）。** `PamAuthenticators` の状態は
`startAuthenticating()` で `Authenticating` になり、**対話型の失敗でしか `Idle` に
戻らない**（顔認証の時間切れでは戻らない）。そして `startAuthenticating()` は
`Authenticating` の間は何もしない。つまり電源ボタンやサスペンドでロックして
最初の顔認証が時間切れになったあとは、`onDpmsTurnedOn` から
`startAuthenticating()` を呼んでも**無反応**だった。注入の実測もこれを裏付ける
（即時に再開した 30 件は状態が `Idle` のグリーター＝自動ロックの猶予中に生成され
最初の認証が走らなかったもの、3.9 秒かかった 12 件は `Authenticating`）。

対処: 状態が `Authenticating` なら `authenticator.cancel()` で対話型の会話だけを
打ち切る。会話は `PAM_CONV_ERR` で終わり、pam_unix は検証に入る前に返るので
**失敗遅延は付かない**（空 PIN との違い）。その失敗で状態が `Idle` に戻り、QML の
`onFailed` で「PIN が違います」を出さずに `startAuthenticating()` する。
打ち切りの失敗が届かない場合の保険に 1.5 秒のタイマーを置く。

同時に howdy-wake は**注入を止めて観測だけ**にし、`irwatch` と併せて
2 週間見る。誤起動 0 件・リードタイム 1 秒前後が確認できたら、
注入・印・`MIN_SUCCESS_DELAY` を撤去する。ガードは画面点灯・撮影権・
直前成功の 3 つを残す。

### 第 3 段: 上流へ

パッチ 1 を plasma-mobile にバグとして、パッチ 2 を機能として提案する。
取り込まれればローカルパッチが不要になる。

### やらないこと

- **加速度・照度による抑止。** 10 日間で誤起動 0 件。必要になったときのために
  取り方だけ 1-6 に残す
- **持ち上げ検知で画面を点ける。** 後の要件を悪化させる
- **`pam_unix nodelay`。** `common-auth` を触ることになる（C10）

---

## 6. 却下した案と、再検討する条件

**同じ検討を繰り返さないために。** 前提が変わったら見直す。

| 案 | 却下の理由 | 再検討する条件 |
|---|---|---|
| グリーターの再起動を契機にする | 異常終了 3 回で TTY 落ち（K6） | kscreenlocker が正常終了の再起動 API を持ったら |
| D-Bus `Lock()` / `SetActive` / SIGUSR1 | 認証を再開しない（K7・K8） | `graceLockEnded()` が `startAuthenticating()` を呼ぶようになったら |
| 非対話失敗のたびに再開（retry ループ） | 画面が点いている限りカメラが回り続ける。C7 | 後の要件を放棄する場合のみ |
| 持ち上げ検知（加速度） | 鞄の中で画面を点けてしまう | 近接センサーを持つ機種に移ったら |
| 暗所・静止での抑止（照度＋加速度） | 誤起動が 0 件で必要が無い | 誤起動が観測されたら（`irwatch` で greeter あり・注入なし・成功印なしの点灯） |
| `pam_unix nodelay` | `common-auth` 全体に効く。PIN の総当たり耐性を下げる | `/etc/pam.d/kde` の無改変という要件を外す場合のみ |
| ガードの順序変更 | 4 つで 10 ms、1.3%。成功経路では全部走る | なし（構造的） |
| カメラを import より前に開く | 認証は速くならず、撮影していないのに光る | なし |
| pam 側の常駐化 | PAM の会話モデルに反する | なし |
| kscreenlocker（C++）への変更 | QML で足りる | QML から DPMS が取れなくなったら |
| `resume_delay` を残す | 表示は既に点いている（C9） | compare.py の画面ガードを外す場合 |

---

## 7. 出典

kscreenlocker（Plasma/6.6 ブランチ）:
`greeter/pamauthenticator.cpp`、`greeter/pamauthenticators.cpp`、
`greeter/greeterapp.cpp`、`greeter/main.cpp`、`ksldapp.cpp`、`interface.cpp`
— <https://github.com/KDE/kscreenlocker/tree/Plasma/6.6>

plasma-mobile（master、実機 6.6.5 と同一）:
`shell/contents/lockscreen/LockScreenState.qml`、`LockScreen.qml`、`PasswordBar.qml`
— <https://invent.kde.org/plasma/plasma-mobile>

KDE Bugzilla:
[515299](https://bugs.kde.org/show_bug.cgi?id=515299)（too soon、6.6.5 で修正）、
[476567](https://bugs.kde.org/show_bug.cgi?id=476567)（猶予中の非対話認証、6.1.1）、
[499637](https://bugs.kde.org/show_bug.cgi?id=499637) /
[499893](https://bugs.kde.org/show_bug.cgi?id=499893)（復帰後に指紋が出ない、6.4.3）、
[508247](https://bugs.kde.org/show_bug.cgi?id=508247)（復帰後の指紋、WORKSFORME）

Ubuntu: `libkscreenlocker6 6.6.5-0ubuntu0.1` の changelog
「Drop included upstream_fix-kde-bug-515299.patch」

Windows Hello:
[Windows Hello face authentication](https://learn.microsoft.com/windows-hardware/design/device-experiences/windows-hello-face-authentication)、
[Wake on approach](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/sensors-presence-wake-on-approach)

KWin: [MR 2985 inputmethod: hide virtual keyboard when not used with touch](https://invent.kde.org/plasma/kwin/-/merge_requests/2985)

Boy-Howdy `howdy/src/pam/main.cc`、`main.hh`、`enter_device.cc`（ローカル）

実機: `~/.local/state/irwatch.log`、journal（2026-09-06〜16）、
`investigation/hikitugi` §9-4-21〜24
