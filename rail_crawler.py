#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TDX 台灣鐵道 (高鐵 THSR 與 台鐵 TRA) 車站 (Station) 與 起訖票價 (ODFare) 爬蟲程式
支援 Access Token 自動取得、智慧 Rate Limiting 與 429 退避重試機制。
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
import requests

DEFAULT_CLIENT_ID = os.getenv("TDX_CLIENT_ID", "fj04214-2f6ccc68-0f31-42ca")
DEFAULT_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET", "a704e775-1e75-4f84-ab85-cf555cdb0c54")

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
BASE_RAIL_URL = "https://tdx.transportdata.tw/api/basic/v2/Rail"


class TDXRailCrawler:
    def __init__(self, client_id: str, client_secret: str, min_delay: float = 0.25):
        self.client_id = client_id
        self.client_secret = client_secret
        self.min_delay = min_delay
        self.last_request_time = 0.0
        self.access_token = None
        self.token_expire_time = 0.0
        self.session = requests.Session()

    def get_token(self) -> str:
        now = time.time()
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
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()

    def request_with_retry(self, url: str, params: dict = None, max_retries: int = 5) -> requests.Response:
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

                rem_minute = resp.headers.get("X-RateLimit-Remaining-Minute") or resp.headers.get("RateLimit-Remaining")
                reset_sec = resp.headers.get("RateLimit-Reset")

                if resp.status_code == 200:
                    if rem_minute is not None and int(rem_minute) <= 0:
                        sleep_time = int(reset_sec) + 1 if reset_sec else 5
                        print(f"  [RateLimit 提示] 當前分鐘額度已用罄，安全暫停等待 {sleep_time} 秒...")
                        time.sleep(sleep_time)
                    return resp

                elif resp.status_code == 429 or "rate limit" in resp.text.lower():
                    wait_time = int(reset_sec) + 1 if reset_sec else 30
                    print(f"  [RateLimit 觸發] 觸發 TDX 伺服器頻率保護 (429)，自動等待 {wait_time} 秒後重試...")
                    time.sleep(wait_time)
                    retries += 1
                    continue

                elif resp.status_code == 401:
                    print("  [Auth] Token 可能已失效，重新申請 Token...")
                    self.access_token = None
                    token = self.get_token()
                    headers["authorization"] = f"Bearer {token}"
                    retries += 1
                    continue

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
        all_records = []
        skip = 0

        while True:
            params = {
                "$top": page_size,
                "$skip": skip,
                "$format": "JSON"
            }
            resp = self.request_with_retry(url, params=params)

            if resp.status_code != 200:
                print(f"  [Warning] HTTP {resp.status_code}: {resp.text[:100]}")
                break

            data = resp.json()
            if not isinstance(data, list):
                print(f"  [Warning] 回傳非陣列資料: {data}")
                break

            all_records.extend(data)
            count = len(data)
            print(f"    -> 累計取得: {len(all_records)} 筆 (本頁: {count} 筆, $skip={skip})")

            if count < page_size:
                break

            skip += page_size

        return all_records

    def crawl_thsr(self, output_dir: str = "data") -> dict:
        """抓取台灣高鐵 (THSR) 車站與全區間起訖票價"""
        os.makedirs(output_dir, exist_ok=True)
        print("\n==========================================")
        print("開始抓取: [台灣高鐵 (THSR)] 車站與票價")
        print("==========================================")

        # 1. 車站
        station_url = f"{BASE_RAIL_URL}/THSR/Station"
        print("-> 正在抓取高鐵車站 (Station)...")
        stations = self.fetch_all_paginated(station_url, page_size=100)

        # 2. 票價
        fare_url = f"{BASE_RAIL_URL}/THSR/ODFare"
        print("-> 正在抓取高鐵起訖票價 (ODFare)...")
        fares = self.fetch_all_paginated(fare_url, page_size=1000)

        # 轉換 Fares 格式為易於前端顯示之票種名稱
        # THSR TicketType: 1-全票, 2-孩童票, 3-敬老票, 4-愛心票
        # FareClass: 1-成人, 2-孩童, 3-敬老, 4-愛心
        # CabinClass: 1-標準車廂, 2-商務車廂, 3-自由座
        cabin_names = {1: "標準車廂", 2: "商務車廂", 3: "自由座"}
        ticket_names = {1: "全票", 2: "孩童票", 3: "敬老票", 4: "愛心票", 5: "團體票"}

        formatted_fares = []
        for f in fares:
            stage_fares = []
            for item in f.get("Fares", []):
                cb = cabin_names.get(item.get("CabinClass"), "一般")
                tk = ticket_names.get(item.get("TicketType"), "單程票")
                stage_fares.append({
                    "FareName": f"{cb}_{tk}",
                    "Price": item.get("Price"),
                    "CabinClass": cb,
                    "TicketType": tk
                })
            
            orig_name = f.get("OriginStationName", {}).get("Zh_tw", "")
            dest_name = f.get("DestinationStationName", {}).get("Zh_tw", "")
            formatted_fares.append({
                "RouteID": "THSR",
                "RouteName": "台灣高鐵",
                "SubRouteName": f"{orig_name} - {dest_name}",
                "OriginStationID": f.get("OriginStationID"),
                "OriginStationName": orig_name,
                "DestinationStationID": f.get("DestinationStationID"),
                "DestinationStationName": dest_name,
                "Direction": f.get("Direction", 0),
                "Fares": stage_fares,
                "StageFares": [{
                    "Direction": f.get("Direction", 0),
                    "OriginStage": {"StopName": orig_name, "StopID": f.get("OriginStationID")},
                    "DestinationStage": {"StopName": dest_name, "StopID": f.get("DestinationStationID")},
                    "Fares": stage_fares
                }]
            })

        # 建立站點列表模擬 StopOfRoute (南港 -> 左營)
        south_bound_stops = sorted(stations, key=lambda x: x.get("StationSequence", 0) or x.get("StationID", 0))
        simulated_stops_route = [{
            "RouteID": "THSR",
            "RouteName": {"Zh_tw": "台灣高鐵 (南下)", "En": "THSR (Southbound)"},
            "SubRouteName": "南港 - 左營",
            "Direction": 0,
            "Stops": [{
                "StopID": s.get("StationID"),
                "StopName": s.get("StationName", {}),
                "StopSequence": idx + 1,
                "StopPosition": s.get("StationPosition", {})
            } for idx, s in enumerate(south_bound_stops)]
        }, {
            "RouteID": "THSR",
            "RouteName": {"Zh_tw": "台灣高鐵 (北上)", "En": "THSR (Northbound)"},
            "SubRouteName": "左營 - 南港",
            "Direction": 1,
            "Stops": [{
                "StopID": s.get("StationID"),
                "StopName": s.get("StationName", {}),
                "StopSequence": idx + 1,
                "StopPosition": s.get("StationPosition", {})
            } for idx, s in enumerate(reversed(south_bound_stops))]
        }]

        result = {
            "cityCode": "THSR",
            "cityName": "台灣高鐵 (THSR)",
            "updatedAt": datetime.now().isoformat(),
            "summary": {
                "stationsCount": len(stations),
                "farePairsCount": len(fares)
            },
            "stations": stations,
            "stopOfRoute": simulated_stops_route,
            "routeFare": formatted_fares
        }

        file_path = os.path.join(output_dir, "THSR_data.json")
        with open(file_path, "w", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2)

        mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[完成] 高鐵資料已儲存至: {file_path} ({mb:.2f} MB)")
        return {"code": "THSR", "name": "台灣高鐵", "file": "THSR_data.json", "sizeMB": round(mb, 2)}

    def crawl_tra(self, output_dir: str = "data") -> dict:
        """抓取台灣鐵路 (TRA / 台鐵) 車站與全區間起訖票價"""
        os.makedirs(output_dir, exist_ok=True)
        print("\n==========================================")
        print("開始抓取: [台灣鐵路 (TRA / 台鐵)] 車站與票價")
        print("==========================================")

        # 1. 車站
        station_url = f"{BASE_RAIL_URL}/TRA/Station"
        print("-> 正在抓取台鐵車站 (Station)...")
        stations = self.fetch_all_paginated(station_url, page_size=1000)

        # 2. 票價
        fare_url = f"{BASE_RAIL_URL}/TRA/ODFare"
        print("-> 正在抓取台鐵起訖票價 (ODFare，包含全台各級車種)...")
        fares = self.fetch_all_paginated(fare_url, page_size=1000)

        # 票種中英文對照
        ticket_type_names = {
            "成自": "自強號 (全票)", "孩自": "自強號 (孩童)", "敬自": "自強號 (敬老)", "愛自": "自強號 (愛心)",
            "成莒": "莒光號 (全票)", "孩莒": "莒光號 (孩童)", "敬莒": "莒光號 (敬老)", "愛莒": "莒光號 (愛心)",
            "成復": "區間車/復興號 (全票)", "孩復": "區間車 (孩童)", "敬復": "區間車 (敬老)", "愛復": "區間車 (愛心)",
            "成普": "普快車 (全票)", "孩普": "普快車 (孩童)"
        }

        formatted_fares = []
        for f in fares:
            stage_fares = []
            for item in f.get("Fares", []):
                t_code = item.get("TicketType", "")
                t_name = ticket_type_names.get(t_code, t_code)
                stage_fares.append({
                    "FareName": t_name,
                    "Price": item.get("Price"),
                    "Code": t_code
                })

            orig_name = f.get("OriginStationName", {}).get("Zh_tw", "")
            dest_name = f.get("DestinationStationName", {}).get("Zh_tw", "")
            formatted_fares.append({
                "RouteID": "TRA",
                "RouteName": "台灣鐵路",
                "SubRouteName": f"{orig_name} - {dest_name}",
                "OriginStationID": f.get("OriginStationID"),
                "OriginStationName": orig_name,
                "DestinationStationID": f.get("DestinationStationID"),
                "DestinationStationName": dest_name,
                "Direction": f.get("Direction", 0),
                "Fares": stage_fares,
                "StageFares": [{
                    "Direction": f.get("Direction", 0),
                    "OriginStage": {"StopName": orig_name, "StopID": f.get("OriginStationID")},
                    "DestinationStage": {"StopName": dest_name, "StopID": f.get("DestinationStationID")},
                    "Fares": stage_fares
                }]
            })

        # 建立簡易環島主要站點模擬 StopOfRoute
        stops_sim = [{
            "RouteID": "TRA",
            "RouteName": {"Zh_tw": "台鐵西部幹線/東部幹線 (各車站)", "En": "TRA All Stations"},
            "SubRouteName": "全線各站",
            "Direction": 0,
            "Stops": [{
                "StopID": s.get("StationID"),
                "StopName": s.get("StationName", {}),
                "StopSequence": idx + 1,
                "StopPosition": s.get("StationPosition", {})
            } for idx, s in enumerate(stations)]
        }]

        result = {
            "cityCode": "TRA",
            "cityName": "台灣鐵路 (TRA / 台鐵)",
            "updatedAt": datetime.now().isoformat(),
            "summary": {
                "stationsCount": len(stations),
                "farePairsCount": len(fares)
            },
            "stations": stations,
            "stopOfRoute": stops_sim,
            "routeFare": formatted_fares
        }

        file_path = os.path.join(output_dir, "TRA_data.json")
        with open(file_path, "w", encoding="utf-8") as out:
            json.dump(result, out, ensure_ascii=False, indent=2)

        mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[完成] 台鐵資料已儲存至: {file_path} ({mb:.2f} MB)")
        return {"code": "TRA", "name": "台灣鐵路", "file": "TRA_data.json", "sizeMB": round(mb, 2)}


def main():
    parser = argparse.ArgumentParser(description="TDX 鐵道 (高鐵 THSR / 台鐵 TRA) 資料爬蟲")
    parser.add_argument("--client_id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--client_secret", default=DEFAULT_CLIENT_SECRET)
    parser.add_argument("--output_dir", default="data")
    parser.add_argument("--target", choices=["all", "thsr", "tra"], default="all")
    args = parser.parse_args()

    crawler = TDXRailCrawler(client_id=args.client_id, client_secret=args.client_secret)

    if args.target in ["all", "thsr"]:
        crawler.crawl_thsr(output_dir=args.output_dir)

    if args.target in ["all", "tra"]:
        crawler.crawl_tra(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
