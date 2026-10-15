/**
 * TDX 台灣大眾運輸 (客運 / 高鐵 / 台鐵) Google Apps Script (GAS) 雲端 API 後端
 * 支援自動向 TDX 取得 Access Token、24小時快取緩存，以及提供 Web App HTTP API
 */

// 1. TDX 官方金鑰憑證
const CLIENT_ID = "fj04214-2f6ccc68-0f31-42ca";
const CLIENT_SECRET = "a704e775-1e75-4f84-ab85-cf555cdb0c54";

const TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token";
const BASE_BUS_URL = "https://tdx.transportdata.tw/api/basic/v2/Bus";
const BASE_RAIL_URL = "https://tdx.transportdata.tw/api/basic/v2/Rail";

/**
 * 取得 TDX OAuth 2.0 Bearer Token (自動緩存於 GAS 快取 23 小時)
 */
function getTDXToken() {
  const cache = CacheService.getScriptCache();
  const cachedToken = cache.get("TDX_ACCESS_TOKEN");
  if (cachedToken) {
    return cachedToken;
  }

  const payload = {
    grant_type: "client_credentials",
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET
  };
  const options = {
    method: "post",
    payload: payload,
    headers: { "content-type": "application/x-www-form-urlencoded" },
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(TOKEN_URL, options);
  if (response.getResponseCode() !== 200) {
    throw new Error("取得 Token 失敗: " + response.getContentText());
  }

  const data = JSON.parse(response.getContentText());
  const token = data.access_token;
  if (token) {
    // 快取 23 小時 (82,800 秒)
    cache.put("TDX_ACCESS_TOKEN", token, 82800);
  }
  return token;
}

/**
 * 提供前端呼叫的 Web App HTTP GET 端點
 * 支援參數：
 *  - type: 'bus' | 'thsr' | 'tra' (預設 'bus')
 *  - route: 路線代號 (例如 '9005')
 *  - from: 出發站名 (例如 '桃客總站' 或 '台北')
 *  - to: 抵達站名 (例如 '市府轉運站' 或 '左營')
 */
function doGet(e) {
  try {
    const params = e ? e.parameter : {};
    const type = (params.type || "bus").toLowerCase();
    const route = params.route || "9005";
    const fromStop = params.from || "";
    const toStop = params.to || "";

    const token = getTDXToken();
    const headers = { "authorization": "Bearer " + token };

    let apiUrl = "";
    if (type === "thsr") {
      // 高鐵全區間票價
      apiUrl = BASE_RAIL_URL + "/THSR/ODFare?$format=JSON";
    } else if (type === "tra") {
      // 台鐵起訖票價 (若有指定站牌則過濾)
      if (fromStop && toStop) {
        apiUrl = BASE_RAIL_URL + `/TRA/ODFare?$filter=contains(OriginStationName/Zh_tw,'${encodeURIComponent(fromStop)}') and contains(DestinationStationName/Zh_tw,'${encodeURIComponent(toStop)}')&$format=JSON`;
      } else {
        apiUrl = BASE_RAIL_URL + "/TRA/ODFare?$top=500&$format=JSON";
      }
    } else {
      // 公路客運或公車路線票價
      apiUrl = BASE_BUS_URL + `/RouteFare/InterCity?$filter=RouteID eq '${route}'&$format=JSON`;
    }

    const resp = UrlFetchApp.fetch(apiUrl, { headers: headers, muteHttpExceptions: true });
    const content = resp.getContentText();

    return ContentService.createTextOutput(content)
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    const errObj = {
      status: "error",
      message: error.toString(),
      timestamp: new Date().toISOString()
    };
    return ContentService.createTextOutput(JSON.stringify(errObj))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
