#!/usr/bin/env python3
"""挑出 v0.6 第二輪標註用的 1500 張影像,並輸出 manifest。

取樣原則:框數比圖片數重要。全台 29,925 個路口只有 6,000 個偵測到框,隨機抽會抽到
大量空圖。所以刻意超抽正樣本,同時保留針對已知失效模式的組別。

六個組別各打不同的東西(見 docs/PHASE0_STRATEGIES.md 的漏檢診斷):
  G1_silent_large  模型沉默的大路口        → recall 的主要來源
  G2_lowconf       信心 0.25-0.5           → 決策邊界
  G3_shadow        沉默 + 路口區域偏暗      → 漏檢位置亮度低 23%,這組打陰影
  G4_positive      高信心,六都以外優先     → 便宜的正樣本 + 地域多樣性
  G5_oddsize       尺寸異常的框             → other_box(機車停等區誤判)的主要來源
  G6_clutter       多個低信心框             → 白色矩形雜訊,困難負樣本

每張圖只歸一組(按稀有度依序分配)。每組內用縣市 round-robin,避免台北/高雄吃掉配額。

輸出檔名 <GROUP>_<seq>.jpg 是刻意的:ml/prepare_dataset.py 的 scene_of() 用
re.sub(r"_\\d+$", "") 取場景名,原本的 ix_<geohash> 會讓每張圖自成一個 stratum、
val 抽不出東西。改成 G1_silent_large_0001 之後 scene_of 得到組名,分層抽樣才會生效。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SIX = {"臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市"}

# 台北的待轉格中位尺寸是 4.85 x 2.53 m(算自 repo/geodata/output/dieturn.geojson)。
# 落在這個區間外的框多半是機車停等區之類的誤判。
OK_LEN = (3.0, 6.0)
OK_WID = (1.8, 3.5)

GROUPS = [
    # (組名, 配額)  順序 = 分配優先序,稀有的先拿
    ("G5_oddsize", 100),
    ("G6_clutter", 200),
    ("G2_lowconf", 350),
    ("G3_shadow", 200),
    ("G1_silent_large", 400),
    ("G4_positive", 250),
]


def center_brightness(path: Path) -> float:
    """路口區域(中央 1/2)的平均亮度。用 JPEG draft 模式做降採樣解碼,快 ~8 倍。"""
    with Image.open(path) as im:
        im.draft("L", (96, 96))
        g = im.convert("L")
        w, h = g.size
        c = g.crop((w // 4, h // 4, w * 3 // 4, h * 3 // 4))
        px = list(c.getdata())
    return sum(px) / len(px)


def round_robin(cands: list[dict], quota: int, seed: int) -> list[dict]:
    """依縣市輪流取,讓小縣市也進得來、大縣市不會吃掉整組。"""
    by_county: dict[str, list[dict]] = defaultdict(list)
    for r in cands:
        by_county[r["county"]].append(r)
    rng = random.Random(seed)
    for k in by_county:
        by_county[k].sort(key=lambda r: r["id"])
        rng.shuffle(by_county[k])
    picked, counties = [], sorted(by_county)
    while len(picked) < quota:
        progressed = False
        for c in counties:
            if by_county[c] and len(picked) < quota:
                picked.append(by_county[c].pop())
                progressed = True
        if not progressed:
            break
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=Path, default=ROOT / "imagery/taiwan_z20/index.csv")
    ap.add_argument("--detections", type=Path,
                    default=ROOT / "runs/predict_taiwan_z20_A/detections.csv")
    ap.add_argument("--zones", type=Path,
                    default=ROOT / "geodata/output_taiwan_z20/dieturn.geojson")
    ap.add_argument("--images", type=Path, default=ROOT / "imagery/taiwan_z20/images")
    ap.add_argument("--out", type=Path, default=ROOT / "label_round2")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shadow-scan", type=int, default=0,
                    help="G3 只掃這麼多張候選(0=全掃)。全掃約 1-2 分鐘")
    A = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    idx = {r["id"]: r for r in csv.DictReader(A.index.open(encoding="utf-8"))}
    print(f"index: {len(idx)} 張")

    # 每張圖的偵測統計
    n_det: Counter = Counter()
    max_conf: dict[str, float] = {}
    for d in csv.DictReader(A.detections.open(encoding="utf-8")):
        iid = Path(d["image"]).stem
        n_det[iid] += 1
        c = float(d["conf"])
        max_conf[iid] = max(max_conf.get(iid, 0.0), c)
    print(f"detections: {sum(n_det.values())} 框, 分佈在 {len(n_det)} 張圖")

    # 尺寸異常的框所在的圖
    odd: set[str] = set()
    zones = json.loads(A.zones.read_text(encoding="utf-8"))["features"]
    for f in zones:
        p = f["properties"]
        if not (OK_LEN[0] <= p["length_m"] <= OK_LEN[1]) or not (OK_WID[0] <= p["width_m"] <= OK_WID[1]):
            odd.add(p["intersection_id"])
    print(f"尺寸異常的框涉及 {len(odd)} 張圖 (全部 {len(zones)} 個框)")

    def rec(iid: str) -> dict:
        r = idx[iid]
        return {"id": iid, "county": r["county"], "n_nodes": int(r["n_nodes"]),
                "lat": r["lat"], "lon": r["lon"],
                "n_det": n_det.get(iid, 0), "max_conf": max_conf.get(iid, 0.0)}

    taken: set[str] = set()
    picks: dict[str, list[dict]] = {}

    def eligible(pred) -> list[dict]:
        return [rec(i) for i in idx if i not in taken and pred(rec(i))]

    for name, quota in GROUPS:
        if name == "G5_oddsize":
            cands = eligible(lambda r: r["id"] in odd)
        elif name == "G6_clutter":
            cands = eligible(lambda r: r["n_det"] >= 2 and r["max_conf"] < 0.6)
        elif name == "G2_lowconf":
            cands = eligible(lambda r: 0.25 <= r["max_conf"] < 0.5)
        elif name == "G3_shadow":
            silent = eligible(lambda r: r["n_det"] == 0)
            rng = random.Random(A.seed)
            silent.sort(key=lambda r: r["id"])
            if A.shadow_scan and A.shadow_scan < len(silent):
                silent = rng.sample(silent, A.shadow_scan)
            print(f"  G3: 掃 {len(silent)} 張沉默影像的亮度…", end="", flush=True)
            for r in silent:
                r["bright"] = center_brightness(A.images / f'{r["id"]}.jpg')
            silent.sort(key=lambda r: r["bright"])
            # 只從最暗的 3 倍配額裡做縣市 round-robin,保證挑到的確實偏暗
            cands = silent[: quota * 3]
            print(f" 最暗 {quota*3} 張的亮度範圍 {cands[0]['bright']:.0f}-{cands[-1]['bright']:.0f}")
        elif name == "G1_silent_large":
            cands = eligible(lambda r: r["n_det"] == 0 and r["n_nodes"] >= 4)
        elif name == "G4_positive":
            cands = eligible(lambda r: r["max_conf"] >= 0.8)
            # 六都以外優先:先只從非六都取,不足再放寬
            outside = [r for r in cands if r["county"] not in SIX]
            got = round_robin(outside, quota, A.seed)
            if len(got) < quota:
                rest = [r for r in cands if r["id"] not in {g["id"] for g in got}]
                got += round_robin(rest, quota - len(got), A.seed + 1)
            picks[name] = got
            taken |= {r["id"] for r in got}
            print(f"{name:<18} 候選 {len(cands):>6} (非六都 {len(outside)})  取 {len(got)}")
            continue
        got = round_robin(cands, quota, A.seed)
        picks[name] = got
        taken |= {r["id"] for r in got}
        print(f"{name:<18} 候選 {len(cands):>6}  取 {len(got)}")

    # 輸出
    img_out = A.out / "images"
    if img_out.exists():
        shutil.rmtree(img_out)
    img_out.mkdir(parents=True)
    rows = []
    for name, _ in GROUPS:
        for i, r in enumerate(sorted(picks[name], key=lambda r: r["id"]), 1):
            new = f"{name}_{i:04d}.jpg"
            shutil.copy2(A.images / f'{r["id"]}.jpg', img_out / new)
            rows.append({"image": new, "ix_id": r["id"], "group": name,
                         "county": r["county"], "lat": r["lat"], "lon": r["lon"],
                         "n_nodes": r["n_nodes"], "v05_n_det": r["n_det"],
                         "v05_max_conf": round(r["max_conf"], 4),
                         "center_brightness": round(r.get("bright", -1), 1)})
    man = A.out / "manifest.csv"
    with man.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"\n共 {len(rows)} 張 → {img_out}")
    print(f"manifest: {man}")
    cc = Counter(r["county"] for r in rows)
    print(f"\n縣市分佈({len(cc)} 個縣市):")
    for c, n in cc.most_common():
        print(f"  {c:<6} {n:>4}  ({n/len(rows):.1%})")
    print("\n每組的 v0.5 偵測狀態:")
    for name, _ in GROUPS:
        g = [r for r in rows if r["group"] == name]
        nd = sum(r["v05_n_det"] for r in g)
        print(f"  {name:<18} {len(g):>4} 張, v0.5 共 {nd:>4} 框, "
              f"{sum(1 for r in g if r['v05_n_det'] == 0)} 張沉默")


if __name__ == "__main__":
    main()
