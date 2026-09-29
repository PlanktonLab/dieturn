<div align="center">

# dieturn

[![Hugging Face model](https://img.shields.io/badge/%F0%9F%A4%97%20Model-dieturn--yolo26s--obb-ffd21e)](https://huggingface.co/xamjiang/dieturn-yolo26s-obb)
[![HF v0.6 PR](https://img.shields.io/badge/v0.6-HF%20draft%20PR-orange)](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/discussions/1)
<br>
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab?logo=python&logoColor=white)
![YOLO26s OBB](https://img.shields.io/badge/YOLO26s-OBB-00b8d4)
![GeoJSON](https://img.shields.io/badge/Data-GeoJSON-3b8739)

**從航拍正射影像找出機車待轉格，產出能接進導航的全台圖資。**

[前言](#為了讓騎士可以預先知道要靠左還是靠右) ·
[功能](#功能) ·
[待轉格資料與模型](#待轉格資料與模型) ·
[Repo 結構](#repo-結構) ·
[在自己的電腦上執行](#在自己的電腦上執行) ·
[資料可信度](#這份圖資能信到什麼程度)

</div>

## 為了讓騎士可以預先知道要靠左還是靠右

騎機車到陌生路口，常常得先判斷左轉要靠左，還是靠右進入待轉區。等到抵達路口才發現站錯位置，可能已經來不及調整。

台灣有約 30,000 個號誌路口，待轉格的位置目前沒有公開的完整資料。dieturn 用航拍正射影像與物件偵測，把待轉格找出來、轉成經緯度，再提供給 [HowTurn](https://github.com/PlanktonLab/HowTurn) 等導航工具使用。

**目前成果：全台 29,925 個路口 → 18,243 個待轉格，分佈在 9,790 個路口（32.7%）。**

## 功能

### 1. 從航拍圖找到待轉格的位置

<table>
  <tr>
    <th>臺中</th>
    <th>高雄</th>
  </tr>
  <tr>
    <td width="50%"><img src="docs/images/taichung_detail.jpg" alt="臺中待轉格偵測結果" width="100%"></td>
    <td width="50%"><img src="docs/images/satellite_detail.jpg" alt="高雄路口衛星圖與待轉格偵測結果" width="100%"></td>
  </tr>
</table>

<sub>左:臺中,以 24.144750°N、120.670500°E 為中心,349 m 見方。右:高雄路口,282 m 見方。綠框 = `auto`(conf 0.8 以上),
黃框 = `review`(0.5–0.8,需人工複核)。待轉格位在路口角落、緊貼行人穿越道前方 ——
圖中框為模型偵測結果，尚未經實地驗證。
影像:內政部國土測繪中心 PHOTO2025 z20。</sub>


### 2. 找回陰影、遮蔽與大路口中漏掉的待轉格

同一個路口,舊模型(v0.5)與新模型(v0.6)的偵測結果。
黃圈 = 高解析影像上的參考位置,紅框 = v0.5,綠框 = v0.6。

![大路口對照](docs/images/v05_vs_v06_large.jpg)

四個參考位置都有待轉格的大路口,v0.5 各只找到 1 個角,v0.6 四個角全中。
這類路口的「全部找齊」比例從 **24% 提升到 71%**。

![單點對照](docs/images/v05_vs_v06.jpg)

陰影、樹冠遮蔽、斜向路口 —— 六個案例中五個是 v0.5 完全沒有偵測。
台北 1,096 個參考位置中,**v0.6 多找回 234 個、只退步 13 個**。

詳細評估與**已知的失效模式**見 [`docs/MODEL.md`](docs/MODEL.md)。


### 3. 輸出 GeoJSON，接進導航或地圖工具

偵測結果合併去重後，保留待轉格多邊形、所在路口、方位、信心值與原始影像索引。下游可依 `auto`／`review` 做分級處理，也能回溯影像重新查證。

**→ [全台待轉格 GeoJSON](geodata/output_taiwan/dieturn.geojson)** · [欄位說明](docs/PIPELINE.md) · [資料授權](DATA_LICENSES.md)

## 待轉格資料與模型

[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-dieturn--yolo26s--obb-yellow)](https://huggingface.co/xamjiang/dieturn-yolo26s-obb)

偵測模型以 `YOLO26s-OBB` 微調，輸出每個待轉格的四個角點。v0.6 從 v0.5 續訓，改用 **1,500 張、涵蓋 21 個縣市的 NLSC z20 人工標註影像**。

**→ [xamjiang/dieturn-yolo26s-obb](https://huggingface.co/xamjiang/dieturn-yolo26s-obb)**

v0.5 已在模型倉庫發布；**v0.6 權重、model card、訓練參數與曲線目前在 [HF 草稿 PR #1](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/discussions/1)**，尚未合併至 `main`。可先檢查 [v0.6 檔案](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/tree/refs%2Fpr%2F1)及 [model card](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/blob/refs%2Fpr%2F1/README.md)。

| | v0.5 | **v0.6** |
|---|--:|--:|
| 台北 recall(3 尺度,conf 0.50) | 0.576 | **0.807** |
| `heading` P95 誤差 | 14.9° | **7.1°** |
| 訓練資料 | 320 張(62% 高解析域) | **1,500 張(100% 同域)** |
| 標註框 | 211 | **1,415** |


表中的 recall 與角度誤差是相對於舊模型參考位置的影像比較，不能直接當成全台圖資的絕對準確率。完整方法、訓練驗證指標與已知失效模式見 [docs/MODEL.md](docs/MODEL.md)及 HF v0.6 model card。

### 你要的是哪一個

| 我想… | 去這裡 |
|---|---|
| **拿待轉格圖資來用** | [`geodata/output_taiwan/dieturn.geojson`](geodata/output_taiwan/dieturn.geojson) — GeoJSON,18,243 筆。欄位定義見 [`docs/PIPELINE.md`](docs/PIPELINE.md),**使用前必讀** [`DATA_LICENSES.md`](DATA_LICENSES.md) |
| **用模型跑自己的影像** | [HF 模型倉庫](https://huggingface.co/xamjiang/dieturn-yolo26s-obb)／[v0.6 草稿 PR](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/discussions/1)，下載方式見下方。**影像格式有嚴格限制** |
| **知道這東西準不準** | [`docs/MODEL.md`](docs/MODEL.md) — 完整評估、失效模式、限制 |
| **自己重跑整條管線** | 下方「管線」。全台抓圖約 8 小時、推論約 30 分鐘 |
| **擴充或重建訓練資料** | [`docs/ANNOTATION_GUIDE.md`](docs/ANNOTATION_GUIDE.md) |
| **理解資料可信度的設計** | [`docs/VERIFICATION_LEVELS.md`](docs/VERIFICATION_LEVELS.md) — 這是本專案的核心概念 |


## Repo 結構

| 目錄 | 內容 |
| --- | --- |
| `imagery/` | 從 OSM 建立路口取樣點、抓取 NLSC 正射影像；原始影像不隨 repo 散布 |
| `ml/` | 標註整理、模型訓練、推論與影像對照評估 |
| `geodata/` | 將旋轉框轉成地理座標、合併去重，並匯出 App 使用的 GeoJSON |
| `geodata/output_taiwan/` | 全台待轉格多邊形、點位、複核清單與統計 |
| `docs/` | 模型評估、方法、管線、標註規範及資料可信度設計 |

模型權重另放在 Hugging Face；訓練影像與標註資料集不隨本 repo 發布。

## 在自己的電腦上執行

### 需求

- **Python 3.10 以上**，依賴見 [requirements.txt](requirements.txt)。
- 推論與訓練需 `torch`、`ultralytics`；抓圖與圖資處理另使用 `requests`、`Pillow`、`shapely` 等套件。
- **NLSC z20 正射影像**：約 13.5 cm/px，原始影像為 768×768 的 3×3 瓦片拼接。

### 安裝與下載模型

```bash
git clone https://github.com/PlanktonLab/dieturn.git
cd dieturn
python -m pip install -r requirements.txt huggingface_hub
```

v0.6 尚在草稿 PR，下載時指定其 revision：

```bash
hf download xamjiang/dieturn-yolo26s-obb dieturn-yolo26s-obb-v0.6.pt --revision refs/pr/1 --local-dir ml/weights
```

PR 合併後，可移除 `--revision refs/pr/1`，從 `main` 下載。

### 對自己的影像推論

```python
from ultralytics import YOLO

model = YOLO("ml/weights/dieturn-yolo26s-obb-v0.6.pt")
results = model.predict("intersection.jpg", imgsz=1024, conf=0.5)
for result in results:
    print(result.obb.xyxyxyxy)  # 旋轉框的四個角點，像素座標
    print(result.obb.conf)
```

影像來源、解析度與拍攝角度會影響結果；使用不同輸入前，請先閱讀 [模型的影像域限制](docs/MODEL.md#33-域限制)。

### 重跑全台管線

```text
imagery/  抓圖 ──→ ml/  訓練＋推論 ──→ geodata/  轉世界座標 ──→ export_app.py
(index.csv)        (detections.csv)   (dieturn.geojson)      (給前端的 GeoJSON)
```

以下示範單尺度推論與地理化。上方公布的全台成果使用三尺度聯集，完整方法見 [docs/METHODS.md](docs/METHODS.md)。

```bash
python imagery/build_points.py
python imagery/fetch_ortho.py --source nlsc_photo2025 \
       --points imagery/points_taiwan_z20.csv --out imagery/taiwan_z20
python ml/predict.py --weights ml/weights/dieturn-yolo26s-obb-v0.6.pt \
       --source imagery/taiwan_z20/images --index imagery/taiwan_z20/index.csv \
       --out runs/tw --imgsz 1024 --conf 0.5 --no-plot
python geodata/build_geodata.py --detections runs/tw/detections.csv \
       --index imagery/taiwan_z20/index.csv --out geodata/output_taiwan
```

全台抓圖約 **8 小時**（60 張/分）、推論約 **10 分鐘/尺度**（RTX 3080）。影像約 2.7 GB，不在 repo 裡，可用上述腳本重抓。

### 匯出 App 使用的圖資

```bash
python geodata/export_app.py --prefix taiwan --out-dir geodata/output_app \
       --add geodata/output_taiwan/dieturn.geojson imagery/taiwan_z20/index.csv 0.0
```

`0.0` 表示尚無獨立驗收的 recall，保守地不採信「沒有偵測到待轉格」的結果；有獨立驗收資料後，才替換成實測值。

## 文件

| | |
|---|---|
| [`docs/MODEL.md`](docs/MODEL.md) | **模型評估** — v0.5→v0.6、失效模式、訓練資料組成 |
| [`docs/METHODS.md`](docs/METHODS.md) | 推論方法 — 尺度、多尺度聯集、多年度影像 |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | 管線流程與 **GeoJSON 欄位定義** |
| [`docs/VERIFICATION_LEVELS.md`](docs/VERIFICATION_LEVELS.md) | **查證等級制度** — 每一筆資料「怎麼被確認的」 |
| [`docs/ANNOTATION_GUIDE.md`](docs/ANNOTATION_GUIDE.md) | 標註規範(要重建或擴充資料集時看這個) |

## 這份圖資能信到什麼程度

**不要當成權威資料使用。** 四個具體限制:

1. **絕對準確率未知。** 現有的評估用臺北 z21 影像當 ground truth,而那個
   ground truth 本身是舊模型的輸出、未經人工確認,**包含誤報**。
   v0.5 與 v0.6 的相對比較可信,絕對數字不可信。
2. **台北以外完全未量測。** 訓練資料涵蓋 21 個縣市,但沒有任何一個縣市
   有獨立的驗收資料。
3. **沒有實地驗證。** 所有結果都只是影像上的比對。
4. **評估數字無法獨立重現。** 當作 ground truth 的臺北 z21 影像與其衍生資料
   受來源條款限制,不隨此 repo 散布(見 [`DATA_LICENSES.md`](DATA_LICENSES.md))。
   全台掃描的部分則完全可重現。

[`docs/VERIFICATION_LEVELS.md`](docs/VERIFICATION_LEVELS.md) 提出一套查證等級制度
來處理這件事 —— 核心是:**「這裡要待轉」說錯只是多等一個燈,
「可以直接左轉」說錯是罰單**,兩種主張的證據門檻必須不同。


## 授權

程式碼為 **Apache-2.0**(見 [`LICENSE`](LICENSE))。散布時須一併附上
[`NOTICE`](NOTICE) —— 那是 Apache-2.0 第 4(d) 條的要求,裡面載明必須標示的來源。

**資料的授權與程式碼不同,而且混合了多種來源 ——
使用前請讀 [`DATA_LICENSES.md`](DATA_LICENSES.md)。**

> ⚠️ `ml/` 底下的程式碼 import `ultralytics`(AGPL-3.0)。Ultralytics 主張使用其
> 程式庫的程式碼亦受 AGPL 約束;此主張有爭議,本專案不提供法律意見。
> 商業使用請自行評估。

摘要:圖資為 **ODbL 1.0**,影像來自**內政部國土測繪中心**(使用須註明出處),
路口資料 **© OpenStreetMap contributors**,模型權重為 **AGPL-3.0**。


## 相關專案

- [Hugging Face 模型倉庫](https://huggingface.co/xamjiang/dieturn-yolo26s-obb) — 權重、model card 與訓練紀錄；[v0.6 草稿 PR](https://huggingface.co/xamjiang/dieturn-yolo26s-obb/discussions/1)。
- [HowTurn](https://github.com/PlanktonLab/HowTurn) — 使用待轉格圖資的機車導航 App。
