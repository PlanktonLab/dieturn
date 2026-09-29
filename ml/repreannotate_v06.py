#!/usr/bin/env python3
"""用 v0.6 重新產生尚未標註 job 的預標框,逐 job 上傳回 CVAT。

為什麼要逐 job:CVAT 的 task 層標註上傳是**整個取代**。直接對 task 上傳會把
已完成 job 的人工標註全部清掉。job 層上傳的影響範圍只有該 job。

已經有人工標註的 job(例如進行中的 job 2)會先把既有框讀回來,與新預標合併後
再上傳,不會弄丟。合併用旋轉框 IoU 去重,衝突時保留人工框。

門檻沿用第一輪的 conf 0.70:預標的價值只在模型可靠的區間,低信心框會讓標註者
誤以為那裡有待轉格 —— 誤收一個框比漏一個框傷害大得多。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from cvat_sdk import make_client
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parent
FORMAT = "Ultralytics YOLO Oriented Bounding Boxes 1.0"
CLASS_NAMES = {0: "waiting_zone", 1: "other_box"}
W = H = 768


def merge_obb(boxes, iou_thr=0.5):
    """旋轉框 NMS。boxes = [(優先度, conf, [8 個正規化座標])],優先度高的先留。"""
    kept = []
    for prio, conf, pts in sorted(boxes, key=lambda b: (-b[0], -b[1])):
        poly = Polygon([(pts[i], pts[i + 1]) for i in range(0, 8, 2)])
        if not poly.is_valid or poly.area <= 0:
            continue
        if any((poly.intersection(k).area / (poly.area + k.area - poly.intersection(k).area)) >= iou_thr
               for k in kept if poly.intersects(k)):
            continue
        kept.append(poly)
        yield pts


def read_scale_dets(run_dir: Path) -> dict[str, list[tuple[float, list[float]]]]:
    """從 predict.py 的 detections.csv 讀出 {影像檔名: [(conf, 正規化 8 座標)]}"""
    import csv
    out: dict[str, list] = {}
    p = run_dir / "detections.csv"
    if not p.is_file():
        return out
    for r in csv.DictReader(p.open(encoding="utf-8")):
        pts = [v for xy in r["corners"].split(";") for v in map(float, xy.split(","))]
        norm = [min(max(v / (W if i % 2 == 0 else H), 0.0), 1.0) for i, v in enumerate(pts)]
        out.setdefault(r["image"], []).append((float(r["conf"]), norm))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--task", type=int, default=2)
    ap.add_argument("--jobs", type=int, nargs="+", required=True, help="要重新預標的 job id")
    ap.add_argument("--runs", type=Path, nargs="+",
                    default=[ROOT / f"runs/preanno_v06_{s}" for s in (768, 1024, 1280)])
    ap.add_argument("--images", type=Path, default=ROOT / "label_round2/images")
    ap.add_argument("--host", default="http://localhost")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dry-run", action="store_true")
    A = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    dets: dict[str, list] = {}
    for r in A.runs:
        for img, lst in read_scale_dets(r).items():
            dets.setdefault(img, []).extend(lst)
    print(f"三尺度合計 {sum(len(v) for v in dets.values())} 個框,分佈在 {len(dets)} 張圖")

    c = make_client(host=A.host, port=A.port)
    c.api_client.set_default_header("Authorization", f"Token {A.token}")
    task = c.tasks.retrieve(A.task)
    frames = {f.frame if hasattr(f, "frame") else i: f.name
              for i, f in enumerate(task.get_frames_info())}

    for jid in A.jobs:
        job = c.jobs.retrieve(jid)
        lo, hi = job.start_frame, job.stop_frame
        names = [frames[f] for f in range(lo, hi + 1)]

        # 既有的人工標註(若有)先讀回來,優先度 1
        existing: dict[str, list] = {}
        with tempfile.TemporaryDirectory() as td:
            ex = Path(td) / "cur.zip"
            try:
                job.export_dataset(format_name=FORMAT, filename=str(ex), include_images=False)
                # Windows 上 ZipFile 不關會鎖住檔案,TemporaryDirectory 清理時會 PermissionError
                with zipfile.ZipFile(ex) as z:
                    for n in z.namelist():
                        if not n.startswith("labels/"):
                            continue
                        stem = Path(n).stem
                        for line in z.read(n).decode("utf-8").splitlines():
                            parts = line.split()
                            if len(parts) == 9:
                                existing.setdefault(f"{stem}.jpg", []).append(
                                    (1, 1.0, [float(v) for v in parts[1:]]))
            except Exception as e:
                print(f"  job {jid}: 讀既有標註失敗({type(e).__name__}),視為空")

        n_exist = sum(len(v) for v in existing.values())
        out_dir = Path(tempfile.mkdtemp())
        (out_dir / "labels/train").mkdir(parents=True)
        (out_dir / "images/train").mkdir(parents=True)
        n_new = 0
        for name in names:
            cand = existing.get(name, []) + [(0, cf, pts) for cf, pts in dets.get(name, [])]
            lines = []
            for pts in merge_obb(cand):
                lines.append("0 " + " ".join(f"{v:.6f}" for v in pts))
            n_new += len(lines)
            (out_dir / "labels/train" / f"{Path(name).stem}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            shutil.copy2(A.images / name, out_dir / "images/train" / name)
        names_yaml = "\n".join(f"  {k}: {v}" for k, v in sorted(CLASS_NAMES.items()))
        (out_dir / "data.yaml").write_text(
            f"path: .\ntrain: images/train\nnames:\n{names_yaml}\n", encoding="utf-8")

        zp = ROOT / f"label_round2/preanno_v06_job{jid}.zip"
        with zipfile.ZipFile(zp, "w") as z:
            z.write(out_dir / "data.yaml", "data.yaml", compress_type=zipfile.ZIP_DEFLATED)
            for name in names:
                z.write(out_dir / "images/train" / name, f"images/train/{name}",
                        compress_type=zipfile.ZIP_STORED)
                z.write(out_dir / "labels/train" / f"{Path(name).stem}.txt",
                        f"labels/train/{Path(name).stem}.txt", compress_type=zipfile.ZIP_DEFLATED)
        shutil.rmtree(out_dir, ignore_errors=True)

        print(f"  job {jid} (frames {lo}-{hi}, state={job.state}): "
              f"既有人工 {n_exist} → 合併後 {n_new} 框  {zp.stat().st_size/1024/1024:.0f} MB", flush=True)
        if not A.dry_run:
            job.import_annotations(format_name=FORMAT, filename=str(zp))
            print(f"    已上傳", flush=True)


if __name__ == "__main__":
    main()
