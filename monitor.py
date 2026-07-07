import os
import time
import requests
from curl_cffi import requests as cffi_requests

# 從環境變數讀取 Discord Webhook URL（安全性考量，不在程式碼內明碼暴露）
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SEARCH_KEYWORD = "owala freesip tritan"
SEARCH_KEYWORD = "blender"
def send_discord_notification(available_products):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL 環境變數，跳過推播。")
        return

    embeds = []
    for prod in available_products[:10]:
        embeds.append({
            "title": f"🎉 發現有貨：{prod['name']}",
            "description": f"**[👉 點擊這裡立即前往商品頁面搶購！]({prod['url']})**",
            "color": 5763719,
            "fields": [
                {"name": "💰 價格", "value": f"${prod['price']}", "inline": True},
                {"name": "🛒 庫存狀態", "value": prod['status'], "inline": True}
            ],
            "footer": {"text": "誠品 Cloudflare 突破監控系統 (GitHub Actions)"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

    payload = {
        "content": "🚨 **【全頻段補貨警報】** 誠品線上偵測到 Owala 水壺有款式開放訂購啦！趕快檢查下方清單！",
        "embeds": embeds
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in [200, 204]:
            print("✅ Discord 補貨通知發送成功！")
        else:
            print(f"❌ Discord 通知發送失敗，狀態碼：{res.status_code}")
    except Exception as e:
        print(f"❌ 發送 Discord Webhook 時發生連線錯誤：{e}")

def check_eslite():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🌐 正在透過偽裝 TLS 指紋連線至誠品後端 API...")
    timestamp = time.strftime("%Y%m%d%H%M%S", time.localtime())
    api_url = f"https://athena.eslite.com/api/v2/search?q={SEARCH_KEYWORD}&final_price=0,&publishDate=0&sort=_weight_+desc&size=20&start=0&datetime={timestamp}"
    
    headers = {
        "Referer": f"https://www.eslite.com/Search?keyword={SEARCH_KEYWORD}",
        "Origin": "https://www.eslite.com",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        # 使用 impersonate="chrome120" 來完美偽裝 Chrome 瀏覽器指紋，繞過 Cloudflare 403
        response = cffi_requests.get(api_url, headers=headers, impersonate="chrome120", timeout=15)
        
        if response.status_code == 403:
            print("❌ 依然遇到 HTTP 403，Cloudflare 攔截了請求！")
            return
        elif response.status_code != 200:
            print(f"❌ 連線失敗，狀態碼：{response.status_code}")
            return

        data = response.json()
        hits = data.get("hits", {}).get("hit", [])
        
        if not hits:
            print("⚠️ 未抓取到任何商品資料，請檢查關鍵字。")
            return

        print(f"📦 成功鎖定 {len(hits)} 款商品，開始分析庫存...")
        available_products = []
        out_of_stock_count = 0

        for idx, prod in enumerate(hits, 1):
            fields = prod.get("fields", {})
            prod_name = fields.get("name", "未知商品")
            prod_id = prod.get("id")
            prod_url = fields.get("url", f"/product/{prod_id}")
            if prod_url.startswith("/"):
                prod_url = f"https://www.eslite.com{prod_url}"
            prod_price = fields.get("final_price", fields.get("price", "未知"))

            is_available = False
            status_text = "缺貨中 / 貨到通知"

            # 判斷庫存
            if fields.get("stock", 0) > 0:
                is_available = True
                status_text = f"有庫存 (剩餘 {fields['stock']} 件)"
            elif fields.get("is_out_of_stock") is False or fields.get("can_buy") is True:
                is_available = True
                status_text = "開放訂購中"

            if is_available:
                print(f" 🟢 [{idx:02d}] 【發現有貨！】 {prod_name} | 價格: ${prod_price} | 狀態: {status_text}")
                available_products.append({
                    "name": prod_name,
                    "url": prod_url,
                    "price": str(prod_price),
                    "status": status_text
                })
            else:
                out_of_stock_count += 1

        print("-" * 50)
        print(f"📊 巡邏完畢！🟢 可購買：{len(available_products)} 件 | 🔴 缺貨中：{out_of_stock_count} 件")

        if available_products:
            print("🚀 正在觸發 Discord 警報...")
            send_discord_notification(available_products)
        else:
            print("💤 目前所有款式皆未開放訂購。")

    except Exception as e:
        print(f"❌ 執行過程中發生異常：{e}")

if __name__ == "__main__":
    check_eslite()
