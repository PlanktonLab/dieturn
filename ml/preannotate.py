#!/usr/bin/env python3
"""用 v0.5 產生第二輪標註的預標框,輸出 CVAT 可匯入的 YOLO-OBB 結構。

策略 F(多尺度聯集):對每張圖跑 imgsz 768/1024/1280 三次,框做旋轉 IoU NMS 合併。
實測見 docs/PHASE0_STRATEGIES.md。

門檻刻意設高(預設 0.7)。預標對熟練標註者的唯一價值是省下畫旋轉框的機械時間
(畫一個約 15-20 秒、確認微調一個約 3-5 秒),那個價值只在模型可靠的高信心區存在。
低信心框不是幫助而是雜訊:誤收一個框會教模型「機車停等區就是待轉格」,
正好打在 model card 記載的第一名誤判來源上。漏框的代價遠低於汙染標註的代價。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

import torch
from shapely.geometry import Polygon
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
CLASS_NAMES = {0: "waiting_zone", 1: "other_box"}


def merge_obb(boxes: list[tuple[float, list[float]]], iou_thr: float) -> list[tuple[float, list[float]]]:
    """旋轉框 NMS:按信心排序,IoU 超過門檻的視為同一個框,保留信心最高的那個。"""
    kept: list[tuple[float, list[float], Polygon]] = []
    for conf, pts in sorted(boxes, key=lambda b: -b[0]):
        poly = Polygon([(pts[i], pts[i + 1]) for i in range(0, 8, 2)])
        if not poly.is_valid or poly.area <= 0:
            continue
        dup = False
        for _, _, kp in kept:
            inter = poly.intersection(kp).area
            if inter and inter / (poly.area + kp.area - inter) >= iou_thr:
                dup = True
                break
        if not dup:
            kept.append((conf, pts, poly))
    return [(c, p) for c, p, _ in kept]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, default=ROOT / "weights/dieturn-yolo26s-obb-v0.5.pt")
    ap.add_argument("--src", type=Path, default=ROOT / "label_round2")
    ap.add_argument("--conf", type=float, default=0.70)
    ap.add_argument("--imgsz", type=int, nargs="+", default=[768, 1024, 1280])
    ap.add_argument("--iou", type=float, default=0.50, help="跨尺度合併的旋轉框 IoU 門檻")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="0")
    A = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    images = sorted((A.src / "images").glob("*.jpg"))
    if not images:
        raise SystemExit(f"{A.src / 'images'} 裡沒有圖,先跑 build_label_round2.py")
    dev = A.device if A.device != "auto" else ("0" if torch.cuda.is_available() else "cpu")
    print(f"權重={A.weights.name}  {len(images)} 張  conf={A.conf}  尺度={A.imgsz}  device={dev}")

    model = YOLO(A.weights)
    per_image: dict[str, list[tuple[float, list[float]]]] = {p.stem: [] for p in images}
    for sz in A.imgsz:
        n = 0
        for i in range(0, len(images), A.batch):
            chunk = images[i : i + A.batch]
            res = model.predict(chunk, imgsz=sz, conf=A.conf, device=dev, verbose=False)
            for src, r in zip(chunk, res):
                if r.obb is None or len(r.obb) == 0:
                    continue
                for c, corners in zip(r.obb.conf.tolist(), r.obb.xyxyxyxy.tolist()):
                    per_image[src.stem].append((float(c), [v for xy in corners for v in xy]))
                    n += 1
            if i % (A.batch * 25) == 0:
                print(f"  imgsz {sz}: {min(i+A.batch, len(images))}/{len(images)}", end="\r", flush=True)
        print(f"  imgsz {sz}: {n} 框" + " " * 20)

    lbl_dir = A.src / "labels" / "train"
    if lbl_dir.exists():
        shutil.rmtree(lbl_dir.parent)
    lbl_dir.mkdir(parents=True)

    W = H = 768  # 全部是 grid=3 的 z20 拼接圖
    total, with_box, raw = 0, 0, 0
    for p in images:
        boxes = per_image[p.stem]
        raw += len(boxes)
        merged = merge_obb(boxes, A.iou)
        lines = []
        for _, pts in merged:
            norm = [min(max(v / (W if i % 2 == 0 else H), 0.0), 1.0) for i, v in enumerate(pts)]
            lines.append("0 " + " ".join(f"{v:.6f}" for v in norm))
        (lbl_dir / f"{p.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        total += len(merged)
        with_box += bool(lines)

    names = "\n".join(f"  {k}: {v}" for k, v in sorted(CLASS_NAMES.items()))
    (A.src / "data.yaml").write_text(f"path: .\ntrain: images/train\nnames:\n{names}\n", encoding="utf-8")
    (A.src / "train.txt").write_text(
        "\n".join(f"images/train/{p.name}" for p in images) + "\n", encoding="utf-8")

    # CVAT 匯入包。**影像必須放進去**,不能只給 labels/。
    # datumaro 的 yolo_ultralytics_oriented_boxes 載入是以影像為驅動的:
    # 先列舉 images/<subset>/ 下的影像,再從影像路徑推導 labels/<subset>/<同名>.txt,
    # 而且要讀影像的實際尺寸才能把正規化座標還原成像素。
    # 只給 labels/ 會得到 "Can't find 'train' subset image folder"。
    # (datumaro 的 dataset_meta.json 只放 label map、不放尺寸,所以沒有省掉影像的方法。)
    anno_zip = A.src / "cvat_preanno_full.zip"
    with zipfile.ZipFile(anno_zip, "w") as z:
        z.write(A.src / "data.yaml", "data.yaml", compress_type=zipfile.ZIP_DEFLATED)
        for p in images:
            z.write(p, f"images/train/{p.name}", compress_type=zipfile.ZIP_STORED)
            z.write(lbl_dir / f"{p.stem}.txt", f"labels/train/{p.stem}.txt",
                    compress_type=zipfile.ZIP_DEFLATED)

    # 建 task 用的影像包
    img_zip = A.src / "cvat_images.zip"
    with zipfile.ZipFile(img_zip, "w", zipfile.ZIP_STORED) as z:
        for p in images:
            z.write(p, p.name)

    print(f"\n三尺度共 {raw} 框 → 去重後 {total} 框,{with_box}/{len(images)} 張有預標 "
          f"({with_box/len(images):.1%})")
    print(f"標註檔: {lbl_dir}")
    print(f"CVAT 標註匯入包: {anno_zip}  ({anno_zip.stat().st_size/1024:.0f} KB)")
    print(f"CVAT 影像包:     {img_zip}  ({img_zip.stat().st_size/1024/1024:.0f} MB)")

    # 每組的預標密度,用來檢查取樣是否如預期
    import csv as _csv
    man = {r["image"]: r for r in _csv.DictReader((A.src / "manifest.csv").open(encoding="utf-8"))}
    from collections import Counter
    g_img, g_box = Counter(), Counter()
    for p in images:
        g = man[p.name]["group"]
        n = len((lbl_dir / f"{p.stem}.txt").read_text(encoding="utf-8").strip().splitlines())
        g_img[g] += 1
        g_box[g] += n
    print("\n每組預標密度:")
    for g in sorted(g_img):
        print(f"  {g:<18} {g_img[g]:>4} 張 → {g_box[g]:>4} 個預標框 ({g_box[g]/g_img[g]:.2f}/張)")


if __name__ == "__main__":
    main()
