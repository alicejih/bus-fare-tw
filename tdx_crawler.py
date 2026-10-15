#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDX 台灣公車與客運 站牌 (StopOfRoute) 與 票價 (RouteFare) 全台資料爬蟲程式
支援全台 22 縣市 + InterCity (公路客運)，具備 Token 自動取得、$top/$skip 分頁、智慧 Rate Limiting 與 429 退避重試機制。
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
import requests

# 預設憑證資訊 (可透過參數或環境變數覆蓋)
DEFAULT_CLIENT_ID = os.getenv("TDX_CLIENT_ID", "fj04214-2f6ccc68-0f31-42ca")
DEFAULT_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET", "a704e775-1e75-4f84-ab85-cf555cdb0c54")

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_API_URL = "https://tdx.transportdata.tw/api/basic/v2/Bus"

# 全台灣 22 縣市代碼與中英文對照表 + InterCity
ALL_TARGETS = [
    {"code": "InterCity", "name": "公路客運 (國道/跨縣市)"},
    {"code": "Taipei", "name": "臺北市"},
    {"code": "NewTaipei", "name": "新北市"},
    {"code": "Taoyuan", "name": "桃園市"},
    {"code": "Taichung", "name": "臺中市"},
    {"code": "Tainan", "name": "臺南市"},
    {"code": "Kaohsiung", "name": "高雄市"},
    {"code": "Keelung", "name": "基隆市"},
    {"code": "Hsinchu", "name": "新竹市"},
    {"code": "HsinchuCounty", "name": "新竹縣"},
    {"code": "MiaoliCounty", "name": "苗栗縣"},
    {"code": "ChanghuaCounty", "name": "彰化縣"},
    {"code": "NantouCounty", "name": "南投縣"},
    {"code": "YunlinCounty", "name": "雲林縣"},
    {"code": "ChiayiCounty", "name": "嘉義縣"},
    {"code": "Chiayi", "name": "嘉義市"},
    {"code": "PingtungCounty", "name": "屏東縣"},
    {"code": "YilanCounty", "name": "宜蘭縣"},
    {"code": "HualienCounty", "name": "花蓮縣"},
    {"code": "TaitungCounty", "name": "臺東縣"},
    {"code": "KinmenCounty", "name": "金門縣"},
    {"code": "PenghuCounty", "name": "澎湖縣"},
    {"code": "LienchiangCounty", "name": "連江縣"}
]


class TDXCrawler:
    def __init__(self, client_id: str, client_secret: str, min_delay: float = 0.25):
        """
        初始化 TDX 爬蟲客戶端
        :param client_id: TDX Client ID
        :param client_secret: TDX Client Secret
        :param min_delay: 每次請求最小間隔時間 (秒)，預設 0.25 秒 (即每秒最多 4 次)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.min_delay = min_delay
        self.last_request_time = 0.0
        self.access_token = None
        self.token_expire_time = 0.0
        self.session = requests.Session()

    def get_token(self) -> str:
        """依據官方 OAuth 2.0 規格取得或刷新 Access Token"""
        now = time.time()
        # 若 token 仍有效 (預留 60 秒緩衝期)，直接使用現有 token
        if self.access_token and now < (self.token_expire_time - 60):
            return self.access_token

        print("[Auth] 正在向 TDX 驗證伺服器申請 Access Token...")
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}

        resp = self.session.post(TOKEN_URL, data=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"取得 Access Token 失敗 (HTTP {resp.status_code}): {resp.text}")

        token_data = resp.json()
        self.access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 86400)
        self.token_expire_time = now + expires_in
        print(f"[Auth] 成功取得 Token！有效期間約 {expires_in // 3600} 小時。")
        return self.access_token

    def _wait_rate_limit(self):
        """維持呼叫間隔，符合每秒最多 4 次 (min_delay = 0.25s) 的規範"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()

    def request_with_retry(self, url: str, params: dict = None, max_retries: int = 5) -> requests.Response:
        """
        發送 HTTP GET 請求，具備：
        1. 智慧 Rate Limiter (每秒 4 次限制)
        2. 檢查 TDX X-RateLimit 標頭與 HTTP 429 狀態碼
        3. 自動依照 RateLimit-Reset 進行安全退避與重新呼叫
        """
        token = self.get_token()
        headers = {
            "authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip, deflate"
        }

        retries = 0
        while retries <= max_retries:
            self._wait_rate_limit()
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=25)

                # 檢查 RateLimit 標頭狀態
                rem_minute = resp.headers.get("X-RateLimit-Remaining-Minute") or resp.headers.get("RateLimit-Remaining")
                reset_sec = resp.headers.get("RateLimit-Reset")

                if resp.status_code == 200:
                    # 若剩餘呼叫額度為 0，且有 reset 時間，稍微休息避免下一呼叫直接被拒
                    if rem_minute is not None and int(rem_minute) <= 0:
                        sleep_time = int(reset_sec) + 1 if reset_sec else 5
                        print(f"  [RateLimit 提示] 當前分鐘額度已用罄，安全暫停等待 {sleep_time} 秒...")
                        time.sleep(sleep_time)
                    return resp

                elif resp.status_code == 429 or "rate limit" in resp.text.lower():
                    # 遭遇頻率限制，等待重設
                    wait_time = int(reset_sec) + 1 if reset_sec else 30
                    print(f"  [RateLimit 觸發] 觸發 TDX 伺服器頻率保護 (429)，自動等待 {wait_time} 秒後重試 (第 {retries+1}/{max_retries} 次)...")
                    time.sleep(wait_time)
                    retries += 1
                    continue

                elif resp.status_code == 401:
                    # Token 可能過期，重取 Token
                    print("  [Auth] Token 可能已失效，重新申請 Token...")
                    self.access_token = None
                    token = self.get_token()
                    headers["authorization"] = f"Bearer {token}"
                    retries += 1
                    continue

                elif resp.status_code == 400:
                    # 部份縣市可能不支援特定 API (例如票價)，直接回傳供上層判定
                    return resp

                else:
                    print(f"  [HTTP {resp.status_code}] 請求異常: {resp.text[:120]}，重試中...")
                    time.sleep(3)
                    retries += 1

            except (requests.RequestException, TimeoutError) as e:
                print(f"  [網路連線異常] {e}，5 秒後重試...")
                time.sleep(5)
                retries += 1

        raise RuntimeError(f"請求失敗超過最大重試次數: {url}")

    def fetch_all_paginated(self, url: str, page_size: int = 1000) -> list:
        """
        實作 OData $top 與 $skip 分頁機制，完整抓取所有資料
        :param url: 目標 API 端點
        :param page_size: 單次查詢上限 $top (預設 1000)
        :return: 完整合併後的資料陣列
        """
        all_records = []
        skip = 0

        while True:
            params = {
                "$top": page_size,
                "$skip": skip,
                "$format": "JSON"
            }
            resp = self.request_with_retry(url, params=params)

            if resp.status_code == 400:
                # 400 Bad Request 通常為該縣市不支援此資料集 (如票價)
                return []

            if resp.status_code != 200:
                print(f"  [Warning] HTTP {resp.status_code}: {resp.text[:100]}")
                break

            data = resp.json()
            if not isinstance(data, list):
                # 若回傳錯誤物件
                print(f"  [Warning] 回傳非陣列資料: {data}")
                break

            all_records.extend(data)
            count = len(data)
            print(f"    -> 累計取得: {len(all_records)} 筆 (本頁: {count} 筆, $skip={skip})")

            # 當回傳筆數小於 $top 時，代表已無後續資料
            if count < page_size:
                break

            skip += page_size

        return all_records

    def crawl_target(self, target_code: str, target_name: str, output_dir: str = "data") -> dict:
        """
        抓取單一縣市或公路客運的 StopOfRoute 與 RouteFare (票價)，並存成獨立 JSON 檔案
        """
        os.makedirs(output_dir, exist_ok=True)
        print(f"\n==========================================")
        print(f"開始抓取: [{target_name}] ({target_code})")
        print(f"==========================================")

        # 1. 決定 API URL 路徑
        if target_code == "InterCity":
            stop_url = f"{BASE_API_URL}/StopOfRoute/InterCity"
            fare_url = f"{BASE_API_URL}/RouteFare/InterCity"
        else:
            stop_url = f"{BASE_API_URL}/StopOfRoute/City/{target_code}"
            fare_url = f"{BASE_API_URL}/RouteFare/City/{target_code}"

        # 2. 抓取站牌資料 (StopOfRoute)
        print(f"-> 正在抓取「站牌資料 (StopOfRoute)」...")
        stops_data = self.fetch_all_paginated(stop_url, page_size=1000)

        # 3. 抓取票價資料 (RouteFare / ODFare)
        print(f"-> 正在抓取「票價資料 (RouteFare / ODFare)」...")
        fares_data = self.fetch_all_paginated(fare_url, page_size=1000)

        # 4. 整合成格式化物件
        combined_result = {
            "cityCode": target_code,
            "cityName": target_name,
            "updatedAt": datetime.now().isoformat(),
            "summary": {
                "stopOfRouteCount": len(stops_data),
                "routeFareCount": len(fares_data)
            },
            "stopOfRoute": stops_data,
            "routeFare": fares_data  # 包含 OriginStage、DestinationStage、各票種之起訖票價
        }

        # 5. 存檔成獨立 JSON 檔案
        file_path = os.path.join(output_dir, f"{target_code}_data.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(combined_result, f, ensure_ascii=False, indent=2)

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[完成] 已儲存至: {file_path} ({file_size_mb:.2f} MB)")
        print(f"       站牌數: {len(stops_data)} 筆, 票價路線數: {len(fares_data)} 筆")

        return {
            "code": target_code,
            "name": target_name,
            "file": f"{target_code}_data.json",
            "sizeMB": round(file_size_mb, 2),
            "stopsCount": len(stops_data),
            "faresCount": len(fares_data),
            "updatedAt": combined_result["updatedAt"]
        }


def main():
    parser = argparse.ArgumentParser(description="TDX 全台公車客運站牌與票價爬蟲")
    parser.add_argument("--client_id", default=DEFAULT_CLIENT_ID, help="TDX Client ID")
    parser.add_argument("--client_secret", default=DEFAULT_CLIENT_SECRET, help="TDX Client Secret")
    parser.add_argument("--output_dir", default="data", help="JSON 檔案輸出資料夾")
    parser.add_argument("--targets", nargs="*", default=None,
                        help="指定要抓取的縣市代碼 (例如: InterCity Taipei NewTaipei)，未指定則抓取全部")
    parser.add_argument("--rate_delay", type=float, default=0.25,
                        help="每次請求最小間隔時間 (預設 0.25 秒 = 每秒最多 4 次)")
    args = parser.parse_args()

    crawler = TDXCrawler(
        client_id=args.client_id,
        client_secret=args.client_secret,
        min_delay=args.rate_delay
    )

    targets_to_run = ALL_TARGETS
    if args.targets:
        selected_codes = set(args.targets)
        targets_to_run = [t for t in ALL_TARGETS if t["code"] in selected_codes]
        if not targets_to_run:
            print(f"[錯誤] 未找到符合的縣市代碼: {args.targets}")
            print(f"可選代碼: {[t['code'] for t in ALL_TARGETS]}")
            return

    print("==================================================")
    print("      TDX 全台客運與公車票價/站牌資料爬蟲啟動      ")
    print(f"  目標數量: {len(targets_to_run)} 個地區/客運類別")
    print(f"  輸出資料夾: {args.output_dir}/")
    print(f"  請求頻率控制: 每秒最多 {1 / args.rate_delay:.1f} 次")
    print("==================================================")

    manifest = []
    manifest_file = os.path.join(args.output_dir, "manifest.json")

    # 讀取現有清單以支援續傳與合併
    if os.path.exists(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = []

    for item in targets_to_run:
        try:
            res_info = crawler.crawl_target(item["code"], item["name"], output_dir=args.output_dir)
            # 更新清單
            manifest = [m for m in manifest if m["code"] != item["code"]]
            manifest.append(res_info)
            with open(manifest_file, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, ensure_ascii=False, indent=2)
        except KeyboardInterrupt:
            print("\n使用者中斷爬蟲程式。")
            break
        except Exception as e:
            print(f"[錯誤] 抓取 {item['name']} 失敗: {e}")

    print("\n==================================================")
    print("所有抓取工作已完成！清單已記錄至:", manifest_file)
    print("==================================================")


if __name__ == "__main__":
    main()
