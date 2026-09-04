#!/usr/bin/env python3
"""他人受入率(FAR)の測定。協力者が得られたときに実行する。

このリポジトリの顔認証は、本人側の性能しか測れていない。
「顔の似た他人が通ってしまわないか」は第三者がいないと測れず、
既知の未検証事項として残っている。本スクリプトはそれを埋めるためのもの。

測り方の要点:

  * 本人と協力者を**同一セッション**で測る。照明も発光体の状態も揃うので、
    別々の日に測った値を比べるより意味がある。
  * 判定は compare.py と同じ条件で行う。すなわち
        類似度 >= certainty  かつ  網膜反射 >= eye_reflection_threshold
    類似度だけを見ると、実際には反射ゲートで落ちるものまで数えてしまう。
  * 距離ごとに測る。登録距離から外れるほど本人の類似度は下がるので、
    本人と他人の差が最も詰まる条件を知りたい。

プライバシー:

  * 画像も顔の埋め込みも**一切保存しない**。メモリ上で数値にしてから捨てる。
  * 登録モデル (/etc/howdy/models/*.dat) と設定は読むだけ。変更しない。
  * 端末に出るのは集計値のみ。--log を付けた場合も数値だけを追記する。

使い方:
    ./impostor_test.py <本人のユーザ名> [--frames N] [--log FILE]
"""
import sys, os, json, argparse

# howdy の配置はディストリで異なる。見つかったものを使う
for _d in ("/usr/lib/x86_64-linux-gnu/howdy", "/usr/lib/howdy",
           "/usr/local/lib/howdy", "/lib/security/howdy"):
    if os.path.isdir(_d):
        sys.path.insert(0, _d)
        break
else:
    sys.exit("howdy が見つかりません。sys.path を手で通してください。")
import configparser
import cv2
import numpy as np
import paths_factory
from recorders.video_capture import VideoCapture

DISTANCES = [
    ("近", "腕を曲げて画面に近づけた状態"),
    ("中", "普段ロック解除している距離"),
    ("遠", "腕を伸ばして持った状態"),
]


def eye_reflection(gray, face):
    """compare.py と同一実装。弱いほうの目の局所コントラストを返す。"""
    x, y, w, h = (int(v) for v in face[:4])
    lm = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
    H, W = gray.shape[:2]
    r = max(3, int(w * 0.05))
    out = []
    for i in (0, 1):
        cx, cy = int(lm[i][0]), int(lm[i][1])
        patch = gray[max(0, cy - r):min(H, cy + r + 1),
                     max(0, cx - r):min(W, cx + r + 1)]
        out.append(float(patch.max()) - float(np.median(patch)) if patch.size else 0.0)
    return min(out)


def rearm(cap):
    """発光体を点け直す。

    TPS68470 の WLEDTO による**ハードウェアのタイムアウトで約 14 秒で消灯する**。
    認証本体は 1 秒未満なので通常は問題にならないが、本スクリプトのように
    Enter 待ちを挟むと測定の合間に消える。消えたまま測ると顔が検出されず
    「測定なし」になる。

    点灯中は値の変更がラッチされないので、0 を書いてから点け直す。
    """
    r = getattr(cap, "internal", None)
    if r is None or not hasattr(r, "illuminator_on"):
        return
    try:
        r.illuminator_off()
    except Exception:
        pass
    r.illuminator_on()


def warmup(cap, n):
    """AE を収束させるために n フレーム捨てる。

    近距離では飽和した状態から始まるのでゲインが下がりきるまで 9 フレーム前後
    かかる。収束前を測ると類似度が実力より低く出る（実測 0.62、収束後 0.87）。
    """
    for _ in range(n):
        try:
            cap.read_frame()
        except Exception:
            return


def measure(cap, det, rec, encs, clahe, cfg, n):
    """n フレーム測って (類似度リスト, 反射リスト, 受入数, 顔なし数) を返す。"""
    sims, refls, accepted, noface = [], [], 0, 0
    for _ in range(n):
        frame, gs = cap.read_frame()
        gs = clahe.apply(gs)
        h, w = frame.shape[:2]
        det.setInputSize((w, h))
        _r, faces = det.detect(frame)
        if faces is None:
            noface += 1
            continue
        for face in faces:
            feat = rec.feature(rec.alignCrop(frame, face))
            sim = max(rec.match(feat, e.reshape(1, -1),
                                cv2.FaceRecognizerSF_FR_COSINE) for e in encs)
            refl = eye_reflection(gs, face)
            sims.append(sim)
            refls.append(refl)
            if sim >= cfg["certainty"] and refl >= cfg["eye"]:
                accepted += 1
    return sims, refls, accepted, noface


def stats(v):
    if not v:
        return "測定なし"
    a = np.asarray(v)
    return f"最小 {a.min():.3f} / 中央 {np.median(a):.3f} / 最大 {a.max():.3f}"


def run_subject(cap, det, rec, encs, clahe, cfg, n, who, is_owner, warm):
    print(f"\n{'=' * 62}\n  {who} の測定\n{'=' * 62}")
    result = {}
    for label, hint in DISTANCES:
        print(f"\n  [{label}距離] {hint}")
        if not is_owner:
            print("  ※ 通そうとして構いません。角度や表情を変えて試してください。")
        input("      準備ができたら Enter（測定中はカメラを見続けてください）: ")
        rearm(cap)                      # 待っている間に発光体が消えている
        warmup(cap, warm)               # AE の収束を待ってから測る
        sims, refls, acc, noface = measure(cap, det, rec, encs, clahe, cfg, n)
        result[label] = (sims, refls, acc)
        print(f"      類似度  {stats(sims)}")
        print(f"      反射    {stats(refls)}")
        print(f"      受入    {acc} / {len(sims)} フレーム")
        if noface:
            print(f"      顔を検出できず {noface} フレーム")
        if not sims:
            print("      **測定できていません。** 発光体が点いているか、"
                  "顔が画角に入っているか確認してください。")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("user")
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--rehearsal", action="store_true",
                    help="本人が両方の役を演じる動作確認。結果に印を付ける")
    ap.add_argument("--warmup", type=int, default=25,
                    help="AE 収束のために捨てるフレーム数")
    ap.add_argument("--log")
    args = ap.parse_args()

    config = configparser.ConfigParser()
    config.read(paths_factory.config_file_path())
    cfg = {
        "certainty": config.getfloat("video", "certainty", fallback=0.7),
        "eye": config.getfloat("video", "eye_reflection_threshold", fallback=15.0),
        "det": config.getfloat("video", "detection_threshold", fallback=0.7),
    }

    print(__doc__.split("使い方:")[0])
    print(f"  判定条件: 類似度 >= {cfg['certainty']}  かつ  反射 >= {cfg['eye']}")
    print(f"  各距離 {args.frames} フレーム（AE 収束のため先頭 {args.warmup} フレームは捨てる）\n")
    print("  協力者の方へ: 顔の画像も特徴量も保存されません。")
    print("  画面に出るのは数値の統計だけで、そこから顔を復元することはできません。")
    if input("\n  同意のうえで開始しますか [yes/N]: ").strip().lower() not in ("yes", "y"):
        print("  中止しました。")
        return 1

    det = cv2.FaceDetectorYN.create(paths_factory.face_detector_path(), "", (320, 320),
                                    score_threshold=cfg["det"], nms_threshold=0.3)
    rec = cv2.FaceRecognizerSF.create(paths_factory.face_recognizer_path(), "")
    encs = []
    for model in json.load(open(paths_factory.user_model_path(args.user))):
        for e in model["data"]:
            encs.append(np.array(e, dtype=np.float32))
    print(f"\n  登録テンプレート {len(encs)} 件を読み込みました。")

    cap = VideoCapture(config)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    try:
        owner = run_subject(cap, det, rec, encs, clahe, cfg, args.frames,
                            f"本人 ({args.user})", True, args.warmup)
        other = run_subject(cap, det, rec, encs, clahe, cfg, args.frames,
                            "リハーサル（本人が協力者役）" if args.rehearsal
                            else "協力者", False, args.warmup)
    finally:
        cap.release()

    print(f"\n{'=' * 62}\n  結果\n{'=' * 62}")
    print(f"\n  {'距離':<6}{'本人 最小':>12}{'協力者 最大':>14}{'差':>9}{'協力者の受入':>14}")
    worst_gap, total_acc, total_frames = None, 0, 0
    for label, _h in DISTANCES:
        o_s, _o_r, _o_a = owner[label]
        x_s, _x_r, x_a = other[label]
        if not o_s or not x_s:
            print(f"  {label:<6}{'測定なし':>12}")
            continue
        o_min, x_max = float(np.min(o_s)), float(np.max(x_s))
        gap = o_min - x_max
        worst_gap = gap if worst_gap is None else min(worst_gap, gap)
        total_acc += x_a
        total_frames += len(x_s)
        print(f"  {label:<6}{o_min:>12.3f}{x_max:>14.3f}{gap:>9.3f}"
              f"{x_a:>10} / {len(x_s):<4}")

    print()
    if args.rehearsal:
        print("  === リハーサル（本人が両方を演じた）。"
              "受入が多く出るのが正常で、他人受入率ではない。 ===")
    if total_acc:
        rate = total_acc / max(1, total_frames) * 100
        print(f"  **協力者が {total_acc} フレームで受け入れられました（{rate:.1f}%）**")
        if not args.rehearsal:
            print("  閾値の引き上げを検討してください。"
                  "ただし本人側の最小値を下回らせないこと。")
    else:
        print("  協力者は 1 フレームも受け入れられませんでした。")
    if worst_gap is not None:
        print(f"  本人最小と協力者最大の差、最悪ケース: {worst_gap:+.3f}")
        if worst_gap < 0.05:
            print("  差が小さい。閾値の位置に余裕がありません。")

    print("\n  ※ 1 名の結果です。他人受入率の推定には人数が要ります。")
    print("  ※ 顔立ちの似た人ほど値が上がります。血縁者がいれば別途試す価値があります。")

    if args.log:
        with open(args.log, "a") as f:
            tag = "REHEARSAL " if args.rehearsal else ""
            f.write(f"# {tag}frames={args.frames} warmup={args.warmup} "
                    f"certainty={cfg['certainty']} eye={cfg['eye']}\n")
            for label, _h in DISTANCES:
                o_s, _r, _a = owner[label]
                x_s, _xr, x_a = other[label]
                if o_s and x_s:
                    f.write(f"{label}\towner_min={np.min(o_s):.3f}\t"
                            f"other_max={np.max(x_s):.3f}\taccepted={x_a}/{len(x_s)}\n")
        print(f"\n  集計値を {args.log} に追記しました（顔データは含まれません）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
