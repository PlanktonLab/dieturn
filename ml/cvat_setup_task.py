#!/usr/bin/env python3
"""在 CVAT 建好第二輪標註的 task 並匯入預標框。

用 SDK 而不是手動點,是為了把兩個容易設錯、而且設錯要重做的參數寫死:

  image_quality=100  CVAT 預設 70,會把每張圖重壓成 JPEG q70 給標註畫面用。
                     待轉格的白線在 z20 上寬度不到 1 px、漏檢位置的對比只有 42 灰階
                     (見 docs/PHASE0_STRATEGIES.md 的漏檢診斷),q70 會模糊掉
                     最需要看清的訊號。
  segment_size=100   不設的話 1500 張會變成單一 job:沒有分段進度、沒辦法
                     一段一段 export 當備份、UI 要吃 1500 frame。

執行環境:專用 venv(不動系統的 torch/ultralytics)
  python -m venv .venv-cvat && .venv-cvat/Scripts/python -m pip install cvat-cli

token 取得方式(不需要密碼,在有 docker 權限的機器上):
  docker exec cvat_server python3 /home/django/manage.py shell -c ^
    "from rest_framework.authtoken.models import Token; from django.contrib.auth import get_user_model; ^
     print(Token.objects.get_or_create(user=get_user_model().objects.get(username='<你的 CVAT 帳號>'))[0].key)"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cvat_sdk import make_client
from cvat_sdk.core.proxies.tasks import ResourceType

ROOT = Path(__file__).resolve().parent
FORMAT = "Ultralytics YOLO Oriented Bounding Boxes 1.0"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--host", default="http://localhost")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--project", type=int, default=1)
    ap.add_argument("--name", default="round2_all")
    ap.add_argument("--subset", default="train")
    ap.add_argument("--images", type=Path, default=ROOT / "label_round2/images")
    ap.add_argument("--annotations", type=Path, default=ROOT / "label_round2/cvat_preanno_full.zip")
    ap.add_argument("--segment-size", type=int, default=100)
    ap.add_argument("--image-quality", type=int, default=100)
    ap.add_argument("--delete-task", type=int, default=0, help="先刪掉這個 task id(0=不刪)")
    A = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    imgs = sorted(A.images.glob("*.jpg"))
    if not imgs:
        raise SystemExit(f"{A.images} 裡沒有 jpg")
    if not A.annotations.is_file():
        raise SystemExit(f"找不到 {A.annotations}")

    c = make_client(host=A.host, port=A.port)
    c.api_client.set_default_header("Authorization", f"Token {A.token}")

    if A.delete_task:
        t = c.tasks.retrieve(A.delete_task)
        print(f"刪除 task {t.id} ({t.name})…", flush=True)
        t.remove()

    print(f"建立 task: {len(imgs)} 張, segment_size={A.segment_size}, "
          f"image_quality={A.image_quality}", flush=True)
    task = c.tasks.create_from_data(
        spec={"name": A.name, "project_id": A.project, "subset": A.subset,
              "segment_size": A.segment_size},
        resource_type=ResourceType.LOCAL,
        resources=[str(p) for p in imgs],
        data_params={"image_quality": A.image_quality},
    )
    print(f"task id={task.id}  jobs={len(task.get_jobs())}", flush=True)

    print(f"匯入預標框: {A.annotations.name} ({A.annotations.stat().st_size/1024/1024:.0f} MB)", flush=True)
    task.import_annotations(format_name=FORMAT, filename=str(A.annotations))

    task.fetch()
    n = sum(len(j.get_annotations().shapes) for j in task.get_jobs())
    print(f"\n完成。task {task.id} '{task.name}'  {task.size} 張  "
          f"{len(task.get_jobs())} 個 job  {n} 個預標框")
    print(f"本機: {A.host}:{A.port}/tasks/{task.id}")


if __name__ == "__main__":
    main()
