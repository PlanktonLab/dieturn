#!/usr/bin/env python3
"""產生航拍取樣點:OSM 號誌節點 → 聚合成路口 → 帶穩定 ID 的 points CSV。

全台版本的取樣點產生腳本。
並且刻意能重現它:同樣以 OSM `highway=traffic_signals` 為來源、同樣用單鏈聚合。
預設半徑 40 m 是校準過的 —— 對台北 bbox 得到的「群數 / 節點數」是 0.521,
與早期台北版本的 0.532 相符(該檔為都發局影像的取樣點,不隨此 repo 散布)。

與台北版的一個差異:**ID 用 geohash 而不是流水號**。流水號會在 OSM 更新後整體
位移,讓既有圖資的 intersection_id 全部對不上;geohash 只跟位置有關,路口沒搬家
ID 就不變。這與 geodata/build_geodata.py 給待轉格編 `dt_` ID 的作法一致。

這支刻意放在 HowTurn repo 之外(見同層 README.md),只依賴標準庫 + requests。

用法:
    python build_points.py                                   # 全台,輸出 points_taiwan_z20.csv
    python build_points.py --county 臺北市 --zoom 20 --grid 3 --out points_taipei_z20.csv  # 單一縣市
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"

# 台灣本島 + 離島。Overpass 的 area 查詢常逾時,用 bbox 穩定得多。
# 含金門(118.1E)與馬祖。bbox 會掃到福建沿岸,靠縣市界線過濾掉(見 --keep-outside)。
TAIWAN_BBOX = (21.85, 118.10, 26.40, 122.10)  # S, W, N, E

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]

# 直轄市／縣市界線(g0v twgeojson,2010 界線;桃園縣的範圍即今桃園市)
COUNTY_GEOJSON = "https://raw.githubusercontent.com/g0v/twgeojson/master/json/twCounty2010.geo.json"
COUNTY_RENAME = {"桃園縣": "桃園市"}

UA = "HowTurn-points-builder/0.1 (https://github.com/PlanktonLab/HowTurn)"

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lon: float, precision: int = 8) -> str:
    """標準 geohash。8 碼約 38 m × 19 m,與 40 m 聚合半徑相稱。"""
    lat_i, lon_i = [-90.0, 90.0], [-180.0, 180.0]
    bits = (16, 8, 4, 2, 1)
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        interval, val = (lon_i, lon) if even else (lat_i, lat)
        mid = (interval[0] + interval[1]) / 2
        if val > mid:
            ch |= bits[bit]
            interval[0] = mid
        else:
            interval[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_B32[ch])
            bit, ch = 0, 0
    return "".join(out)


def fetch_signals(bbox: tuple[float, float, float, float], cache: Path) -> tuple[list[tuple[float, float]], str]:
    """抓 OSM 號誌節點。回傳 [(lat, lon)] 與資料快照時間。"""
    cache.mkdir(parents=True, exist_ok=True)
    raw = cache / f"signals_{bbox[0]}_{bbox[1]}_{bbox[2]}_{bbox[3]}.json"
    if raw.is_file() and raw.stat().st_size > 1000:
        print(f"使用快取 {raw}")
        data = json.loads(raw.read_text(encoding="utf-8"))
    else:
        q = (
            "[out:json][timeout:280];"
            f'node["highway"="traffic_signals"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
            "out body;"
        )
        data = None
        for url in OVERPASS:
            try:
                print(f"Overpass 查詢 {url} …", flush=True)
                r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=300)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                print(f"  失敗:{e!r}", flush=True)
                time.sleep(5)
        if data is None:
            raise SystemExit("所有 Overpass 端點都失敗,稍後再試")
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    stamp = data.get("osm3s", {}).get("timestamp_osm_base", "unknown")
    pts = [(e["lat"], e["lon"]) for e in data.get("elements", []) if e.get("type") == "node"]
    return pts, stamp


def cluster(pts: list[tuple[float, float]], radius_m: float) -> list[list[int]]:
    """單鏈聚合(grid bucket + union-find)。回傳每群的成員索引。"""
    if not pts:
        return []
    lat0 = sum(p[0] for p in pts) / len(pts)
    m_lat = 110_574.0
    m_lon = 111_320.0 * math.cos(math.radians(lat0))
    xy = [(p[1] * m_lon, p[0] * m_lat) for p in pts]

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (x, y) in enumerate(xy):
        buckets[(int(x // radius_m), int(y // radius_m))].append(i)

    parent = list(range(len(pts)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (cx, cy), idxs in buckets.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cx + dx, cy + dy), ()):
                    for i in idxs:
                        if j <= i:
                            continue
                        if math.dist(xy[i], xy[j]) <= radius_m:
                            union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(pts)):
        groups[find(i)].append(i)
    return list(groups.values())


Ring = tuple[list[tuple[float, float]], tuple[float, float, float, float]]
Poly = tuple[Ring, list[Ring]]  # 外環 + 內環(洞)
County = tuple[str, tuple[float, float, float, float], list[Poly]]


def _ring(coords: list) -> Ring:
    pts = [(float(c[0]), float(c[1])) for c in coords]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return pts, (min(xs), min(ys), max(xs), max(ys))


def _in_ring(x: float, y: float, ring: Ring) -> bool:
    pts, (x0, y0, x1, y1) = ring
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def load_counties(cache: Path) -> list[County]:
    """縣市界線,純標準庫的點面判斷用結構(不依賴 shapely)。"""
    cache.mkdir(parents=True, exist_ok=True)
    fn = cache / "tw_county.geo.json"
    if not fn.is_file() or fn.stat().st_size < 10000:
        print("下載縣市界線 …", flush=True)
        r = requests.get(COUNTY_GEOJSON, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
        fn.write_bytes(r.content)
    fc = json.loads(fn.read_text(encoding="utf-8"))

    out: list[County] = []
    for f in fc["features"]:
        name = f["properties"].get("COUNTYNAME") or f["properties"].get("name") or "?"
        name = COUNTY_RENAME.get(name, name).replace("台", "臺")
        g = f["geometry"]
        raw = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        polys: list[Poly] = [(_ring(p[0]), [_ring(h) for h in p[1:]]) for p in raw]
        xs0 = min(p[0][1][0] for p in polys)
        ys0 = min(p[0][1][1] for p in polys)
        xs1 = max(p[0][1][2] for p in polys)
        ys1 = max(p[0][1][3] for p in polys)
        out.append((name, (xs0, ys0, xs1, ys1), polys))
    return out


def county_of(lon: float, lat: float, counties: list[County]) -> str:
    for name, (x0, y0, x1, y1), polys in counties:
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        for outer, holes in polys:
            if _in_ring(lon, lat, outer) and not any(_in_ring(lon, lat, h) for h in holes):
                return name
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "points_taiwan_z20.csv")
    ap.add_argument("--zoom", type=int, default=20, help="瓦片層級;NLSC 全台上限為 20")
    ap.add_argument("--grid", type=int, default=3, help="每張圖幾乘幾張瓦片(3 → 768 px ≈ 104 m)")
    ap.add_argument("--radius", type=float, default=40.0, help="聚合半徑(公尺)")
    ap.add_argument("--source", default="nlsc_photo2025", help="寫進 CSV 的影像來源標記")
    ap.add_argument("--county", nargs="*", help="只輸出這些縣市(預設全台)")
    ap.add_argument("--keep-outside", action="store_true",
                    help="保留落在縣市界線外的點;預設丟棄(bbox 會掃到福建沿岸)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"))
    ap.add_argument("--cache", type=Path, default=CACHE)
    args = ap.parse_args()

    bbox = tuple(args.bbox) if args.bbox else TAIWAN_BBOX
    pts, stamp = fetch_signals(bbox, args.cache)  # type: ignore[arg-type]
    print(f"OSM 號誌節點 {len(pts)} 個(快照 {stamp})", flush=True)

    groups = cluster(pts, args.radius)
    print(f"聚合半徑 {args.radius:.0f} m → {len(groups)} 個路口", flush=True)

    counties = load_counties(args.cache)

    rows, used, dropped, outside = [], defaultdict(int), 0, 0
    per_county: defaultdict[str, int] = defaultdict(int)
    for members in groups:
        lat = sum(pts[i][0] for i in members) / len(members)
        lon = sum(pts[i][1] for i in members) / len(members)
        county = county_of(lon, lat, counties)
        if not county and not args.keep_outside:
            outside += 1
            continue
        if args.county and county not in args.county:
            dropped += 1
            continue
        gid = "ix_" + geohash(lat, lon, 8)
        used[gid] += 1
        if used[gid] > 1:
            gid = f"{gid}-{used[gid]}"
        per_county[county or "(界外)"] += 1
        rows.append(
            {
                "id": gid,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "n_nodes": len(members),
                "county": county,
                "source": args.source,
                "zoom": args.zoom,
                "grid": args.grid,
            }
        )

    # 依緯度排序:抓圖時相鄰的點會共用瓦片,順序相近可以大幅提高瓦片快取命中率
    rows.sort(key=lambda r: (r["county"], -r["lat"], r["lon"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "lat", "lon", "n_nodes", "county", "source", "zoom", "grid"])
        w.writeheader()
        w.writerows(rows)

    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "osm_snapshot": stamp,
        "osm_nodes": len(pts),
        "cluster_radius_m": args.radius,
        "intersections": len(rows),
        "dropped_outside_boundary": outside,
        "zoom": args.zoom,
        "grid": args.grid,
        "imagery_source": args.source,
        "bbox": list(bbox),
        "per_county": dict(sorted(per_county.items(), key=lambda kv: -kv[1])),
        "id_scheme": "ix_ + geohash8 (位置穩定;重跑不會位移)",
        "attribution": "取樣點來自 OpenStreetMap contributors,ODbL 1.0",
    }
    meta_path = args.out.with_name(args.out.stem + "_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n寫出 {len(rows)} 個取樣點 → {args.out}")
    if outside:
        print(f"(丟棄縣市界線外的 {outside} 個,多為 bbox 掃到的福建沿岸)")
    if dropped:
        print(f"(依 --county 過濾掉 {dropped} 個)")
    print(f"metadata → {meta_path}\n")
    for k, v in sorted(per_county.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<8} {v:>6}")


if __name__ == "__main__":
    main()
