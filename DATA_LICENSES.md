# 資料與模型授權

這個 repo 混合了四種來源,授權各不相同。**程式碼的授權(見 `LICENSE`)不涵蓋資料。**

---

## 影像

### 內政部國土測繪中心 正射影像(NLSC)

`imagery/fetch_ortho.py` 的 `nlsc_photo2025` / `nlsc_photo2024` / `nlsc_photo2022` /
`nlsc_photo2` 來源,以及由它們產生的所有衍生資料。

- **授權:政府資料開放授權條款第 1 版**
- 可重製、改作、編輯、公開傳輸、商業利用
- **使用時須註明出處:「內政部國土測繪中心」**

`imagery/taiwan_z20/index.csv` 的 `attribution` 欄位帶著這個標示,下游請勿剝除。

### 臺北市政府都市發展局 航測正射影像 — **完全不含在本 repo**

本專案早期曾用臺北市都發局的 `Ortho_2025`(z21,6.8 cm/px)做台北的對照實驗
(見 `docs/MODEL.md` 第 1 節)。該服務的使用條款是:

> **不得批次大量取圖、不得轉供第三方流通**

因此以下全部**刻意排除**在公開版本之外:

| 排除項目 | 理由 |
|---|---|
| 影像本身 | 不得轉供第三方流通 |
| `imagery/fetch_ortho.py` 的 `taipei_udd` 來源設定 | 本檔是批次抓圖工具,附上 URL 等於提供現成的違規手段 |
| `imagery/taipei_z21/index.csv`(瓦片座標) | 與上一項合起來就是完整的取圖配方 |
| `imagery/points_taipei_z21.csv`(取樣點) | 同上 |
| `geodata/output/`(台北 z21 的衍生圖資) | 座標與尺寸雖非影像重製,但產生它的取圖行為本身受條款限制 |

**這造成一個後果,使用者應該知道:**
`docs/MODEL.md` 裡所有以「z21 ground truth」為基準的
數字(recall 0.616 → 0.818 等)**無法用本 repo 的內容獨立重現**,因為參考資料
不隨 repo 散布。那些報告保留下來是為了透明呈現評估方法與已知限制,
不是可重現的 benchmark。

全台掃描一律使用 NLSC 來源,授權允許重製、改作與散布,**該部分完全可重現**。

---

## 取樣點與路口 ID

`imagery/points_taiwan_z20.csv`

- 由 **OpenStreetMap** 的 `highway=traffic_signals` 節點聚合產生
- **授權:ODbL 1.0**(Open Database License),© OpenStreetMap contributors
- ODbL 有 **share-alike** 條款:衍生資料庫須以相同授權釋出

### 這會傳染到圖資

`geodata/output_taiwan/dieturn.geojson` 的每一筆都帶 `intersection_id`
(例:`ix_wsqmcs47`),那個 ID 由 OSM 節點聚合而來。因此:

> **`geodata/` 底下的圖資一併以 ODbL 1.0 釋出,並標示
> 「© OpenStreetMap contributors」與「內政部國土測繪中心」。**

如果你只要待轉格的幾何而不要路口關聯,剝掉 `intersection_id`、
`osm_node_id`、`intersection_dist_m` 三個欄位即可切斷這層關係。

---

## 人工標註

1,500 張 NLSC z20 影像上的 1,415 個待轉格旋轉框。

- **不在這個 repo 裡**,預計另行以 HuggingFace Datasets 發布
- 影像部分沿用 NLSC 的政府資料開放授權(須註明出處)
- 標註部分:**ODbL 1.0**,與取樣點一致
- 標註方法見 `docs/ANNOTATION_GUIDE.md`

---

## 模型權重

`dieturn-yolo26s-obb-v0.5.pt` 已發布於 HuggingFace;`v0.6.pt` 尚未發布。

- **授權:AGPL-3.0**,繼承自 Ultralytics YOLO
- **權重不在這個 repo 裡**

> ### ⚠️ 授權相容性提醒
>
> `ml/` 底下的程式碼 import `ultralytics`(AGPL-3.0)。Ultralytics 主張使用其
> 程式庫的程式碼也受 AGPL 約束;此主張在法律上有爭議,本專案不提供法律意見。
>
> 若你要在商業產品中使用,請自行評估,或向 Ultralytics 取得商業授權。

---

## 下游產物的標示

用這個專案的圖資做出任何東西時,建議的標示:

```
待轉格圖資 © dieturn contributors, ODbL 1.0
正射影像:內政部國土測繪中心(政府資料開放授權條款第 1 版)
路口資料 © OpenStreetMap contributors, ODbL 1.0
```

---

## 摘要表

| 資產 | 在 repo 裡? | 授權 |
|---|:-:|---|
| 程式碼 | ✅ | 見 `LICENSE` |
| NLSC 影像 | ❌(可用腳本重抓) | 政府資料開放授權 v1,**須註明出處** |
| 都發局 z21 影像及其所有衍生物 | ❌ **全部排除** | 不得批次取圖、不得轉供第三方流通 |
| `index.csv` / 取樣點 | ✅ | ODbL 1.0 |
| `geodata/` 圖資 | ✅ | ODbL 1.0 + NLSC 出處標示 |
| 人工標註 | ❌(另發) | ODbL 1.0 |
| 模型權重 | ❌(HuggingFace) | AGPL-3.0 |
