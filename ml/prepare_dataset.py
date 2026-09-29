#!/usr/bin/env python3
"""把各標註者的資料整併成 YOLO OBB 訓練用的 train/val 目錄。

來源結構: datasets/labeled/<labeler>/images/*.jpg  +  datasets/labeled/<labeler>/labels/train/<stem>.txt
輸出結構: ml/dataset/images/{train,val}/*.jpg      +  ml/dataset/labels/{train,val}/*.txt

沒有對應 .txt 的圖片視為純背景負樣本(輸出空的 .txt)。

datasets/ 不隨 repo 散布(影像受來源條款限制),標註方式見 docs/ANNOTATION_GUIDE.md。
"""
from __future__ import annotations

import argparse
import random
import re
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 標註者目錄一律用 labeler_N 匿名編號(見 docs/ANNOTATION_GUIDE.md)。
# v1 是寫死的四人各 80 張;第二輪起改成掃 labeler_* ,人數不固定。
LABELER_GLOB = "labeler_*"
CLASS_NAMES = {0: "DieTurn"}
# CVAT 專案有 waiting_zone(0) 與 other_box(1) 兩個 label,但 v1 與第二輪都只標 class 0。
# 匯出檔若混入 class 1 就整行丟棄,不要報錯 —— 標註者遇到真正難以歸類的框時
# 可以標 other_box 當作「這裡有白框但不是待轉格」,那個資訊對單類別訓練沒有用途。
DROP_CLASSES = {1}
# 被影像邊緣切到的待轉格,CVAT 匯出的角點會略微超出 [0,1]。
# ANNOTATION_GUIDE 明確要求「露出超過一半就標」,所以這是正確的標註行為,不是錯誤。
# 小幅超出夾回 [0,1];超過 CLAMP_TOL 才報錯 —— 那才是標註流程真的壞掉。
CLAMP_TOL = 0.05
NL = chr(10)


def scene_of(stem: str) -> str:
    """A_taipei_urban_hires_042 -> A_taipei_urban_hires"""
    return re.sub(r"_\d+$", "", stem)


def collect(src: Path) -> list[tuple[Path, Path | None]]:
    items = []
    labelers = sorted(d for d in src.glob(LABELER_GLOB) if d.is_dir())
    if not labelers:
        raise SystemExit(f"{src} 底下找不到任何 {LABELER_GLOB} 目錄")
    for labeler in labelers:
        img_dir = labeler / "images"
        lbl_dir = labeler / "labels" / "train"
        if not img_dir.is_dir():
            raise SystemExit(f"找不到 {img_dir}")
        for img in sorted(img_dir.glob("*.jpg")):
            lbl = lbl_dir / f"{img.stem}.txt"
            items.append((img, lbl if lbl.is_file() and lbl.stat().st_size else None))
    return items


def check_label(lbl: Path) -> tuple[list[str], int, int]:
    """驗證 OBB 標註格式: class x1 y1 x2 y2 x3 y3 x4 y4 (正規化座標)。

    回傳 (要保留的行, 丟棄的行數, 夾過邊界的行數)。DROP_CLASSES 裡的類別整行丟棄,
    格式錯誤或未知類別仍然報錯 —— 那是標註流程壞掉的訊號,不該安靜吞掉。
    """
    kept, dropped, clamped = [], 0, 0
    for i, line in enumerate(lbl.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 9:
            raise SystemExit(f"{lbl}:{i} 欄位數 {len(parts)} != 9,不是 OBB 格式")
        cls = int(parts[0])
        if cls in DROP_CLASSES:
            dropped += 1
            continue
        if cls not in CLASS_NAMES:
            raise SystemExit(f"{lbl}:{i} 未知類別 {cls}")
        coords = [float(v) for v in parts[1:]]
        if any(not (-CLAMP_TOL <= v <= 1 + CLAMP_TOL) for v in coords):
            raise SystemExit(f"{lbl}:{i} 座標超出容許範圍: {coords}")
        if any(v < 0.0 or v > 1.0 for v in coords):
            coords = [min(max(v, 0.0), 1.0) for v in coords]
            line = parts[0] + " " + " ".join(f"{v:.6f}" for v in coords)
            clamped += 1
        kept.append(line)
    return kept, dropped, clamped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=ROOT / "datasets" / "labeled")
    ap.add_argument("--out", type=Path, default=ROOT / "ml" / "dataset")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--copy", action="store_true", help="複製圖片而非建立 symlink")
    args = ap.parse_args()

    items = collect(args.src)
    if not items:
        raise SystemExit("沒有收集到任何圖片")

    # 依 (場景, 有無標註) 分層抽樣,確保 val 也涵蓋各場景的正負樣本
    strata: dict[tuple[str, bool], list] = {}
    for img, lbl in items:
        strata.setdefault((scene_of(img.stem), lbl is not None), []).append((img, lbl))

    rng = random.Random(args.seed)
    split: dict[str, list] = {"train": [], "val": []}
    for key in sorted(strata):
        group = sorted(strata[key], key=lambda p: p[0].stem)
        rng.shuffle(group)
        n_val = round(len(group) * args.val_frac)
        # 正樣本組至少留 1 張給 val,且不能整組被抽走
        if key[1] and len(group) > 1:
            n_val = min(max(n_val, 1), len(group) - 1)
        split["val"] += group[:n_val]
        split["train"] += group[n_val:]

    for sub in ("images", "labels"):
        for s in ("train", "val"):
            d = args.out / sub / s
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)

    stats = {s: Counter() for s in split}
    for s, group in split.items():
        for img, lbl in sorted(group, key=lambda p: p[0].stem):
            dst_img = args.out / "images" / s / img.name
            if args.copy:
                shutil.copy2(img, dst_img)
            else:
                try:
                    dst_img.symlink_to(img.resolve())
                except OSError:
                    # Windows 非開發者模式建 symlink 需要管理員權限,退回複製
                    shutil.copy2(img, dst_img)
            dst_lbl = args.out / "labels" / s / f"{img.stem}.txt"
            if lbl is None:
                dst_lbl.write_text("")
                stats[s]["background"] += 1
            else:
                kept, dropped, clamped = check_label(lbl)
                stats[s]["boxes"] += len(kept)
                stats[s]["dropped"] += dropped
                stats[s]["clamped"] += clamped
                dst_lbl.write_text(NL.join(kept) + (NL if kept else ""), encoding="utf-8")
                if kept:
                    stats[s]["labeled"] += 1
                else:
                    # 整張圖的框都被丟掉 -> 變成純背景負樣本
                    stats[s]["background"] += 1
            stats[s]["images"] += 1

    yaml_path = args.out / "dieturn_obb.yaml"
    names = "\n".join(f"  {k}: {v}" for k, v in sorted(CLASS_NAMES.items()))
    yaml_path.write_text(
        f"# 自動產生,請勿手動編輯 — 由 scripts/prepare_dataset.py 產生\n"
        f"path: {args.out.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n{names}\n"
    )

    print(f"標註者: {', '.join(d.name for d in sorted(args.src.glob(LABELER_GLOB)) if d.is_dir())}")
    for s in ("train", "val"):
        c = stats[s]
        print(
            f"{s:>5}: {c['images']:>3} 張 "
            f"({c['labeled']} 張有標註 / {c['background']} 張背景), {c['boxes']} 個框"
            + (f"  [丟棄 {c['dropped']} 個 class 1]" if c["dropped"] else "")
            + (f"  [夾邊界 {c['clamped']} 個]" if c["clamped"] else "")
        )
    print(f"\n資料集設定檔: {yaml_path}")


if __name__ == "__main__":
    main()
