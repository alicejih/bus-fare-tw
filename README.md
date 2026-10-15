# TDX 台灣公車與客運 站牌與票價爬蟲 & 查詢系統

本專案依據交通部 **TDX 運輸資料流通服務平臺 (Transport Data eXchange)** 官方規範與 OAuth 2.0 範例，實作全台公車與客運的資料爬取與前端票價查詢介面。

---

## 專案架構

```
├── tdx_crawler.py       # Python 爬蟲主程式 (含自動取 Token、$top/$skip 分頁、429 RateLimit 退避機制)
├── data/                # 爬蟲產生的各縣市/類別獨立 JSON 資料夾
│   ├── manifest.json    # 已爬取縣市索引清單
│   ├── InterCity_data.json # 公路客運資料 (含 9005 路線完整起訖票價與站牌)
│   ├── Taipei_data.json # 臺北市公車資料
│   └── ...              # 全台其餘縣市資料
├── index.html           # 前端視覺化票價與站牌查詢系統 (支援動態載入與本機離線載入)
└── README.md            # 專案說明文件
```

---

## 一、TDX 爬蟲程式 (`tdx_crawler.py`) 說明

### 核心功能
1. **OAuth 2.0 認證**：依官方 SampleCode 標準流程向 TDX 驗證伺服器交換 Bearer Access Token，並具備過期自動刷新機制。
2. **全台涵蓋**：涵蓋全台 22 縣市及 `InterCity`（公路客運/國道客運）。
3. **完整分頁 ($top / $skip)**：自動依據 TDX 伺服器回傳筆數以 1,000 筆為單位進行分頁，完整獲取巨量資料（如 InterCity 包含 1,859 筆路線站牌與 1,779 筆起訖票價）。
4. **智慧 Rate Limiting 與 429 退避機制**：
   - 設定基本間隔（預設 0.25 秒，不超過 4 次/秒）。
   - 主動讀取 TDX 的 `X-RateLimit-Remaining-Minute` 與 `RateLimit-Reset` 標頭。
   - 若帳號觸發每分鐘上限或 HTTP 429，程式會自動休眠等待重設後無縫續爬，絕不中斷崩潰。
5. **獨立儲存**：依目標分別存成 `{City}_data.json`（例如 `InterCity_data.json`、`Taipei_data.json`）。

### 常用執行指令

- **爬取所有目標（全台 22 縣市 + 公路客運）：**
  ```bash
  python3 tdx_crawler.py
  ```

- **指定特定縣市或類別（如公路客運與臺北市）：**
  ```bash
  python3 tdx_crawler.py --targets InterCity Taipei
  ```

- **自訂 Client ID 與 Client Secret：**
  ```bash
  python3 tdx_crawler.py --client_id "YOUR_CLIENT_ID" --client_secret "YOUR_CLIENT_SECRET"
  ```

---

## 二、前端票價查詢網頁 (`index.html`)

前端採用現代化響應式介面（Tailwind CSS），提供流暢的查詢體驗：

1. **多縣市動態切換**：可選擇公路客運（含 9005 等路線）或各縣市公車，即時非同步載入對應 JSON。
2. **路線搜尋與篩選**：輸入路線代碼（如 `9005`）即可即時過濾並選取。
3. **去返程與附屬路線切換**：支援 Direction 0 (去程) / 1 (返程) 及各分支路線。
4. **起訖站票價試算 (ODFare)**：
   - 選擇出發站牌（Origin）與抵達站牌（Destination），系統自動比對並列出各票種（全票、半票、電子票證等）金額。
   - 點擊站牌順序列表即可一鍵帶入起訖站。
5. **雙模式檢視**：
   - 🎫 **區間票價資料表 (Stage Fares)**：完整展示該路線所有站間計費區段。
   - 📍 **路線站牌順序 (StopOfRoute)**：展示沿途站牌清單與序號。
6. **本機/離線支援**：若未架設 Web 伺服器，可直接透過右上角「📁 本機載入 JSON」載入本機檔案，避開瀏覽器本機 CORS 限制。

### 開啟方式

在終端機啟動本機簡易 HTTP 伺服器即可瀏覽：
```bash
python3 -m http.server 8080
```
接著在瀏覽器打開：`http://localhost:8080`
