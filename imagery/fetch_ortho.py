#!/usr/bin/env python3
"""下載以路口為中心的正射影像拼接圖,支援多個影像來源與分縣市續跑。

`fetch_taipei.py` 的泛化版:同樣的續跑、瓦片快取、限流退讓與停機偵測機制,
但把寫死的都發局 URL 換成 SOURCES 登錄表,並多了 --county 分片。

    # 全台(NLSC z20,約 30,000 個路口)
    python imagery/fetch_ortho.py --source nlsc_photo2025 --out imagery/taiwan_z20

    # 只跑一個縣市
    python imagery/fetch_ortho.py --source nlsc_photo2025 --out imagery/taiwan_z20 --county 高雄市

    # Phase 0 驗證:對台北那 2,556 個路口改抓 NLSC z20,與現有 z21 結果對照
    python imagery/fetch_ortho.py --source nlsc_photo2025 --zoom 20 --grid 3 \\
        --points imagery/points_taiwan_z20.csv --out imagery/taiwan_z20

每張圖只有在所有瓦片都乾淨回來時才會寫出,所以拼接圖不會有黑塊。
索引 index.csv 是**地理定位的唯一依據**(tile_x0/tile_y0 給 geodata/build_geodata.py
的 px2ll() 用),不論影像本身在不在 git 裡都要留著。
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

# --- 影像來源登錄表 ------------------------------------------------------------
# max_zoom 為實測值。attribution 會寫進 index.csv,下游要照授權條款標示出處。
SOURCES: dict[str, dict] = {
    "nlsc_photo2025": {
        "url": "https://wmts.nlsc.gov.tw/wmts/PHOTO2025/default/GoogleMapsCompatible/{z}/{y}/{x}",
        "max_zoom": 20,
        "attribution": "內政部國土測繪中心",
        "license": "政府資料開放授權條款第 1 版",
        "note": "114 年度正射影像,全台涵蓋。z21 會回 0 byte 的 image/gif。",
        "interval": 0.10,
        "probe": (20, 878370, 448934),
    },
    "nlsc_photo2": {
        "url": "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}",
        "max_zoom": 20,
        "attribution": "內政部國土測繪中心",
        "license": "政府資料開放授權條款第 1 版",
        "note": "通用正射影像(各年度最新混合)。年度層被雲遮時的備援。",
        "interval": 0.10,
        "probe": (20, 878370, 448934),
    },
    "nlsc_photo2024": {
        "url": "https://wmts.nlsc.gov.tw/wmts/PHOTO2024/default/GoogleMapsCompatible/{z}/{y}/{x}",
        "max_zoom": 20,
        "attribution": "內政部國土測繪中心",
        "license": "政府資料開放授權條款第 1 版",
        "note": "113 年度。同一路口被雲/樹遮住時可換年度重抓。",
        "interval": 0.10,
        "probe": (20, 878370, 448934),
    },
    "nlsc_photo2022": {
        "url": "https://wmts.nlsc.gov.tw/wmts/PHOTO2022/default/GoogleMapsCompatible/{z}/{y}/{x}",
        "max_zoom": 20,
        "attribution": "內政部國土測繪中心",
        "license": "政府資料開放授權條款第 1 版",
        "note": "111 年度。多年度聯集用;實測與 PHOTO2021 在部分地區逐位元相同(年度層逐地區別名)。",
        "interval": 0.10,
        "probe": (20, 878370, 448934),
    },
    # 臺北市政府都市發展局 Ortho_2025(z21, 6.8 cm/px)的來源設定**刻意不含在
    # 公開版本裡**。該服務的使用條款為「不得批次大量取圖、不得轉供第三方流通」,
    # 而本檔正是一支批次抓圖工具 —— 附上它的 URL 與座標等於提供現成的違規手段。
    #
    # 本專案早期用該影像做過台北的對照實驗(見 docs/MODEL.md 第 1 節),
    # 但影像、瓦片座標與衍生圖資都不隨此 repo 散布。
    # 全台掃描一律使用 NLSC 來源,授權允許重製、改作與散布。
}

UA = "HowTurn-imagery/0.2 (research; https://github.com/PlanktonLab/HowTurn)"


def tilef(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def is_blank(im: Image.Image) -> bool:
    lo, hi = im.convert("L").getextrema()
    return hi - lo < 8


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="nlsc_photo2025", choices=sorted(SOURCES))
    ap.add_argument("--out", required=True, help="輸出目錄,例如 imagery/taiwan_z20")
    ap.add_argument("--points", default=None, help="預設為 <out>/points_*.csv 裡唯一的那份")
    ap.add_argument("--county", nargs="*", help="只抓這些縣市(需要 points CSV 有 county 欄)")
    ap.add_argument("--zoom", type=int, default=0, help="覆寫 points CSV 的 zoom")
    ap.add_argument("--grid", type=int, default=0, help="覆寫 points CSV 的 grid")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--interval", type=float, default=0.0, help="全域請求間隔(秒);0 = 用來源預設")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repair", action="store_true",
                    help="修補模式:清掉 0-byte 瓦片快取,重抓所有含黑塊或無涵蓋的圖")
    A = ap.parse_args()

    src = SOURCES[A.source]
    if A.zoom and A.zoom > src["max_zoom"]:
        raise SystemExit(f"{A.source} 實測最高只到 z{src['max_zoom']},z{A.zoom} 會拿到空圖")

    points = A.points
    if points is None:
        cands = [f for f in os.listdir(A.out) if f.startswith("points_") and f.endswith(".csv")] if os.path.isdir(A.out) else []
        if len(cands) != 1:
            raise SystemExit(f"請用 --points 指定取樣點 CSV(在 {A.out} 找到 {len(cands)} 份)")
        points = os.path.join(A.out, cands[0])

    os.makedirs(f"{A.out}/images", exist_ok=True)
    os.makedirs(f"{A.out}/tiles", exist_ok=True)
    LOG = open(f"{A.out}/fetch.log", "a", encoding="utf-8")
    IDX = f"{A.out}/index.csv"

    idx_lock = threading.Lock()
    rate_lock = threading.Lock()
    last_req = [0.0]
    interval = [A.interval or src["interval"]]
    outage = threading.Event()
    err_streak = [0]
    empty = [0]          # 空回應次數(可重試,非無涵蓋)

    def log(*a):
        print(time.strftime("%H:%M:%S"), *a, file=LOG, flush=True)

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=A.workers * 2,
                                                         pool_maxsize=A.workers * 2))
    pz, px_, py_ = src["probe"]
    PROBE = src["url"].format(z=pz, x=px_, y=py_)

    def throttle():
        while outage.is_set():
            time.sleep(5)
        with rate_lock:
            wait = last_req[0] + interval[0] - time.time()
            if wait > 0:
                time.sleep(wait)
            last_req[0] = time.time() + random.random() * interval[0] * 0.3

    def wait_for_recovery():
        """服務開始噴錯時由一條執行緒進來,持續探測到它回來為止。"""
        if outage.is_set():
            return
        outage.set()
        log("OUTAGE server returning errors; pausing and probing every 60 s")
        waited = 0
        while True:
            time.sleep(60)
            waited += 60
            try:
                r = sess.get(PROBE, timeout=30)
                if r.status_code == 200 and "image" in r.headers.get("content-type", "") and r.content:
                    log(f"RECOVERED after {waited // 60} min, resuming")
                    err_streak[0] = 0
                    outage.clear()
                    return
            except Exception:
                pass
            if waited % 600 == 0:
                log(f"OUTAGE still down after {waited // 60} min")

    def fetch_tile(z: int, x: int, y: int):
        """回傳瓦片路徑,或 None 表示這格真的沒有影像。"""
        fn = f"{A.out}/tiles/{z}_{x}_{y}.jpg"
        if os.path.exists(fn):
            return None if os.path.getsize(fn) == 0 else fn
        for attempt in range(5):
            throttle()
            try:
                r = sess.get(src["url"].format(z=z, x=x, y=y), timeout=40)
            except Exception as e:
                log("NET", z, x, y, repr(e)[:70])
                time.sleep(3 * (attempt + 1))
                continue
            ct = r.headers.get("content-type", "")
            # NLSC 對超出層級/無涵蓋的請求回 200 + 0 byte 的 image/gif,要當成無涵蓋
            if r.status_code == 200 and "image" in ct and r.content:
                try:
                    im = Image.open(io.BytesIO(r.content))
                    im.load()
                except Exception:
                    open(fn, "wb").close()
                    return None
                if is_blank(im):
                    open(fn, "wb").close()
                    return None
                open(fn, "wb").write(r.content)
                err_streak[0] = 0
                return fn
            if r.status_code == 200 and not r.content:
                # NLSC 對超出層級的請求回 200 + 0 byte,但**同樣的回應也會短暫地
                # 出現在正常瓦片上**(CDN miss 之類)。首次實跑有 371 個瓦片被這樣
                # 誤判為無涵蓋,事後重抓 25 個全部拿得到圖。所以空回應要當成可重試
                # 的軟錯誤,retry 用盡才寫 0-byte 標記。
                empty[0] += 1
                if attempt < 4:
                    time.sleep(5 * (attempt + 1) ** 2)   # 5/20/45/80 s
                    continue
                open(fn, "wb").close()
                return None
            if r.status_code in (204, 404):
                open(fn, "wb").close()
                return None
            if r.status_code in (403, 429):
                with rate_lock:
                    interval[0] = min(interval[0] * 2, 1.0)
                log(f"HTTP {r.status_code} rate limited -> slowing to {interval[0]:.2f} s/req")
                time.sleep(30)
                continue
            if r.status_code >= 500 or r.status_code == 400:
                err_streak[0] += 1
                if err_streak[0] >= 6:
                    wait_for_recovery()
                else:
                    time.sleep(4 * (attempt + 1))
                continue
        raise RuntimeError(f"tile {z}/{x}/{y} unavailable after retries")

    def do_point(r: dict) -> str:
        out = f"{A.out}/images/{r['id']}.jpg"
        if os.path.exists(out):
            return "exists"
        z = A.zoom or int(r["zoom"])
        G = A.grid or int(r["grid"])
        fx, fy = tilef(float(r["lat"]), float(r["lon"]), z)
        x0, y0 = int(round(fx - G / 2)), int(round(fy - G / 2))
        im = Image.new("RGB", (256 * G, 256 * G), (0, 0, 0))
        blank = 0
        for dy in range(G):
            for dx in range(G):
                fn = fetch_tile(z, x0 + dx, y0 + dy)
                if fn:
                    try:
                        im.paste(Image.open(fn).convert("RGB"), (256 * dx, 256 * dy))
                        continue
                    except Exception:
                        pass
                blank += 1
        status = "ok" if blank <= G * G // 4 else "no_coverage"
        if status == "ok":
            im.save(out, quality=92)
        with idx_lock:
            new = not os.path.exists(IDX)
            with open(IDX, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["id", "lat", "lon", "n_nodes", "county", "source", "attribution",
                                "zoom", "grid", "tile_x0", "tile_y0", "blank_tiles", "status", "path"])
                w.writerow([r["id"], r["lat"], r["lon"], r["n_nodes"], r.get("county", ""),
                            A.source, src["attribution"], z, G, x0, y0, blank, status,
                            out if status == "ok" else ""])
        return status

    if A.repair:
        # 空回應會被快取成 0-byte,而 NLSC 的空回應多半是暫時的(CDN miss)。
        # 修補模式把這些快取丟掉,並重抓所有帶黑塊或被判無涵蓋的取樣點。
        zero = [f.path for f in os.scandir(f"{A.out}/tiles") if f.stat().st_size == 0] if os.path.isdir(f"{A.out}/tiles") else []
        for z in zero:
            os.remove(z)
        bad: set[str] = set()
        if os.path.exists(IDX):
            with open(IDX, encoding="utf-8") as fh:
                idx_rows = list(csv.DictReader(fh))
            bad = {r["id"] for r in idx_rows if r["status"] != "ok" or int(r["blank_tiles"]) > 0}
            for i in bad:
                img = f"{A.out}/images/{i}.jpg"
                if os.path.exists(img):
                    os.remove(img)
            keep = [r for r in idx_rows if r["id"] not in bad]
            with open(IDX, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=idx_rows[0].keys())
                w.writeheader()
                w.writerows(keep)
        log(f"REPAIR dropped {len(zero)} empty tiles, requeued {len(bad)} points")
        print(f"修補模式:清掉 {len(zero)} 個空瓦片快取,重排 {len(bad)} 個取樣點")

    rows = list(csv.DictReader(open(points, encoding="utf-8")))
    if A.county:
        rows = [r for r in rows if r.get("county") in A.county]
        if not rows:
            raise SystemExit(f"{points} 裡沒有 county 符合 {A.county} 的列")
    if A.limit:
        rows = rows[: A.limit]
    todo = [r for r in rows if not os.path.exists(f"{A.out}/images/{r['id']}.jpg")]

    log(f"START source={A.source} points={len(rows)} todo={len(todo)} "
        f"workers={A.workers} interval={interval[0]}")
    print(f"來源 {A.source}（{src['attribution']}，{src['license']}）")
    print(f"取樣點 {len(rows)}，待抓 {len(todo)}，輸出 {A.out}")

    t0 = time.time()
    done, nc, err = [0], [0], [0]

    def wrap(r):
        try:
            s = do_point(r)
        except Exception as e:
            log("ERR", r["id"], repr(e)[:100])
            s = "error"
            err[0] += 1
        done[0] += 1
        if s == "no_coverage":
            nc[0] += 1
        if done[0] % 50 == 0 or done[0] == len(todo):
            el = time.time() - t0
            rate = done[0] / el * 60
            eta = (len(todo) - done[0]) / max(rate, 1e-6)
            msg = (f"PROGRESS {done[0]}/{len(todo)} no_coverage={nc[0]} errors={err[0]} "
                   f"{rate:.1f} img/min ETA {eta / 60:.2f} h")
            log(msg)
            print("  " + msg, end="\r", flush=True)

    with ThreadPoolExecutor(max_workers=A.workers) as ex:
        list(ex.map(wrap, todo))

    n = len(os.listdir(f"{A.out}/images"))
    log(f"DONE images={n} no_coverage={nc[0]} errors={err[0]} empty_retries={empty[0]} elapsed={(time.time() - t0) / 3600:.2f} h")
    print(f"\n完成:影像 {n} 張,no_coverage {nc[0]},錯誤 {err[0]},"
          f"耗時 {(time.time() - t0) / 3600:.2f} h")


if __name__ == "__main__":
    main()
