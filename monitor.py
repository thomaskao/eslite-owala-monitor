import os
import re
import time
import subprocess
import requests
import undetected_chromedriver as uc
from pyvirtualdisplay import Display  # 引入虛擬顯示器套件
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# 從 GitHub Secrets 讀取 Discord Webhook URL
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# 誠品監控網址從 GitHub Secrets 讀取，不寫在程式碼裡
TARGET_URL = os.getenv("ESLITE_URL")

# Finders 商品頁網址從 GitHub Secrets 讀取（多個網址用逗號或換行分隔）
FINDERS_URLS = [u.strip() for u in re.split(r"[,\s]+", os.getenv("FINDERS_URLS", "")) if u.strip()]
# Finders 的 SKU 以 Freesip 開頭才是水壺（排除吸管、保護套等配件）；SKU 若帶容量則標示 710ml / 945ml
FINDERS_SKU_PREFIX = "freesip"
FINDERS_SIZE_LABEL = {"24oz": "710ml", "32oz": "945ml"}
FINDERS_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


BUY_BUTTON_TEXTS = ("加入購物車", "立即購買", "立即結帳")

# 不依賴 class 名稱：以 /product/ 商品連結找出每個商品卡片（含按鈕的最小容器）
EXTRACT_JS = r"""
const seen = new Set(), out = [];
for (const a of document.querySelectorAll('a[href*="/product/"]')) {
  const href = a.href.split('?')[0];
  if (seen.has(href)) continue;
  let card = a, best = null;
  for (let i = 0; i < 8 && card.parentElement; i++) {
    card = card.parentElement;
    const links = new Set([...card.querySelectorAll('a[href*="/product/"]')].map(x => x.href.split('?')[0]));
    if (links.size > 1) break;
    best = card;
  }
  if (!best) continue;
  seen.add(href);
  const text = (best.innerText || '').trim();
  const img = best.querySelector('img[alt]');
  const nameEl = best.querySelector('[class*="name"], [class*="title"], h2, h3');
  const name = ((nameEl && nameEl.innerText) || a.getAttribute('title') || (img && img.alt) || a.innerText || '').trim();
  const priceM = text.match(/(?:NT)?\$\s*[\d,]+/);
  out.push({
    url: href, name, text,
    price: priceM ? priceM[0] : '未知',
    buttons: [...best.querySelectorAll('button')].map(b => ({
      text: (b.innerText || '').trim(),
      gid: b.getAttribute('data-gid') || '',
      disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
    })),
  });
}
return out;
"""

DIAG_JS = r"""
const cls = {};
document.querySelectorAll('[class]').forEach(e => String(e.className).split(/\s+/).forEach(c => { if (c) cls[c] = (cls[c] || 0) + 1; }));
return JSON.stringify({
  anchors: document.querySelectorAll('a').length,
  hrefs: [...document.querySelectorAll('a')].slice(0, 15).map(a => a.getAttribute('href')),
  topClasses: Object.entries(cls).sort((a, b) => b[1] - a[1]).slice(0, 25),
  text: (document.body.innerText || '').slice(0, 300),
});
"""

def send_discord_notification(available_products, source="誠品線上"):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ 未設定 DISCORD_WEBHOOK_URL，跳過推播。")
        return

    embeds = []
    for prod in available_products[:10]:
        embeds.append({
            "title": f"🎉 發現有貨：{prod['name']}",
            "description": f"**[👉 點擊這裡立即前往商品頁面搶購！]({prod['url']})**",
            "color": 5763719,
            "fields": [
                {"name": "💰 價格", "value": f"${prod['price']}", "inline": True},
                {"name": "🛒 按鈕狀態", "value": prod['status'], "inline": True}
            ],
            "footer": {"text": f"{source}監控系統 (GitHub Actions)"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

    payload = {
        "content": f"🚨 **【補貨警報】** {source}偵測到 Owala 水壺有款式可購買啦！趕快檢查下方清單！",
        "embeds": embeds
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code in [200, 204]:
            print("✅ Discord 補貨通知發送成功！")
        else:
            print(f"❌ Discord 通知發送失敗，狀態碼：{res.status_code}")
    except Exception as e:
        print(f"❌ 發送 Discord Webhook 時發生錯誤：{e}")

def check_finders():
    """Finders (Shopline) 商品頁把各款式庫存以 JSON 內嵌在 HTML 中，直接用 requests 解析即可。"""
    B = r"\\"  # 頁面內的 JSON 被跳脫過，key 形如 \"sku\"
    if not FINDERS_URLS:
        print("⚠️ 未設定 FINDERS_URLS，跳過 Finders 檢查。")
        return
    available = []
    for url in FINDERS_URLS:
        try:
            res = requests.get(url, headers=FINDERS_HEADERS, timeout=20)
            res.raise_for_status()
        except Exception as e:
            print(f"❌ Finders 讀取失敗 {url}：{e}")
            continue

        html = res.text
        chunks = re.split(r'(?=' + B + r'"sku' + B + r'":' + B + r'")', html)[1:]
        total = in_stock = 0
        for c in chunks:
            sku = re.match(B + r'"sku' + B + r'":' + B + r'"([^"' + B + r']*)', c).group(1)
            qty = re.search(B + r'"quantity' + B + r'":(-?\d+),' + B + r'"stock_id', c)
            if not qty or not sku.lower().startswith(FINDERS_SKU_PREFIX):
                continue
            name = re.search(B + r'"fields' + B + r'":\[\{' + B + r'"name_translations' + B + r'":\{[^}]*\},' + B + r'"name' + B + r'":' + B + r'"([^"' + B + r']*)', c)
            price = re.search(B + r'"price_sale' + B + r'":\{[^}]*?' + B + r'"dollars' + B + r'":([\d.]+)', c)
            color = name.group(1) if name else sku
            size = next((v for k, v in FINDERS_SIZE_LABEL.items() if k in sku), "")
            kind = "Tritan" if "tritan" in sku.lower() else "不鏽鋼"
            total += 1
            if int(qty.group(1)) > 0:
                in_stock += 1
                available.append({
                    "name": f"Owala FreeSip {kind} {size}｜{color}".replace("  ", " "),
                    "url": url,
                    "price": f"{float(price.group(1)):,.0f}" if price else "未知",
                    "status": f"庫存 {qty.group(1)}",
                })
                print(f" 🟢 【Finders 有貨】{color} x{qty.group(1)} | {url}")
        print(f"📊 Finders {url.rsplit('/', 1)[-1]}：共 {total} 款，有貨 {in_stock} 款")
        if not chunks:
            print("⚠️ 未解析到任何款式，頁面結構可能已變動。")

    if available:
        send_discord_notification(available, source="Finders")
    else:
        print("💤 Finders 目前皆無貨。")

def get_chrome_major_version():
    """自動偵測 GitHub Actions 主機上的 Google Chrome 主版號"""
    try:
        output = subprocess.check_output(['google-chrome', '--version'], text=True)
        version_str = output.strip().split()[2]
        major_version = int(version_str.split('.')[0])
        print(f"ℹ️ 成功偵測到主機 Chrome 瀏覽器主版號為：{major_version}")
        return major_version
    except Exception as e:
        print(f"⚠️ 無法自動偵測 Chrome 版本，將由套件自行決定：{e}")
        return None

def create_driver():
    print("🛠️ 正在初始化防偵測 Chrome 瀏覽器 (一般 GUI 模式)...")
    options = uc.ChromeOptions()
    
    # ⚠️ 【關鍵修改】絕對不要加 --headless=new！讓 Chrome 在虛擬螢幕中以「真實 GUI 模式」開啟
    # 這樣才能擁有完整的 WebGL 與顯示器指紋，輕鬆欺騙 Cloudflare！
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=zh-TW")
    options.add_argument("--password-store=basic")
    options.add_argument("--no-first-run")
    
    chrome_version = get_chrome_major_version()
    if chrome_version:
        driver = uc.Chrome(options=options, version_main=chrome_version)
    else:
        driver = uc.Chrome(options=options)
        
    return driver

def check_eslite():
    if not TARGET_URL:
        print("⚠️ 未設定 ESLITE_URL，跳過誠品檢查。")
        return

    # 1. 在雲端伺服器啟動 1920x1080 的虛擬桌面顯示器 (Xvfb)
    print("🖥️ 正在啟動 Linux 虛擬螢幕顯示器 (Xvfb)，準備偽裝為真實桌面...")
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🌐 正在使用 GUI 模式進入誠品線上...")
        driver = create_driver()
        
        try:
            driver.get(TARGET_URL)
            
            # 給予 Cloudflare 與前端渲染充足的時間
            print("⏳ 正在等待網頁載入 (通過 Cloudflare 驗證並渲染商品)...")
            cards = []
            deadline = time.time() + 40
            while time.time() < deadline:
                cards = driver.execute_script(EXTRACT_JS)
                if cards:
                    break
                time.sleep(2)

            if not cards:
                print("⚠️ 警告：40 秒內未找到任何商品連結。")
                print(f"📄 當前頁面標題：{driver.title}")
                print("🔎 診斷資訊：", driver.execute_script(DIAG_JS))
                return

            # 捲動以觸發延遲載入，再抓一次
            for frac in (0.5, 1):
                driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {frac});")
                time.sleep(1.5)
            cards = driver.execute_script(EXTRACT_JS) or cards

            print(f"📦 成功鎖定 {len(cards)} 款商品，開始逐一分析庫存狀態...\n" + "-"*50)

            available_products = []
            out_of_stock_count = 0

            for index, card in enumerate(cards, 1):
                text = card["text"]
                name = card["name"] or text.split("\n")[0]
                buttons = card["buttons"]
                can_buy = any(
                    (b["gid"] == "add-cart-button" or any(k in b["text"] for k in BUY_BUTTON_TEXTS))
                    and not b["disabled"] for b in buttons
                )
                if can_buy:
                    status = next((b["text"] for b in buttons if b["gid"] == "add-cart-button" or any(k in b["text"] for k in BUY_BUTTON_TEXTS)), "可購買")
                    print(f" 🟢 [{index:02d}] 【發現有貨！】 {name} | 價格: {card['price']}")
                    available_products.append({
                        "name": name,
                        "url": card["url"],
                        "price": card["price"].replace("$", "").replace("NT", "").strip() or "未知",
                        "status": status,
                    })
                else:
                    out_of_stock_count += 1
                    print(f" 🔴 [{index:02d}] 缺貨/未開放：{name} | 按鈕：{[b['text'] for b in buttons]}")

            print("-" * 50)
            print(f"📊 巡邏完畢！🟢 可購買：{len(available_products)} 件 | 🔴 缺貨中：{out_of_stock_count} 件")

            if available_products:
                print("🚀 正在觸發 Discord 警報...")
                send_discord_notification(available_products)
            else:
                print("💤 目前所有款式皆未開放訂購。")

        except Exception as e:
            print(f"❌ 執行過程中發生異常：{e}")
        finally:
            driver.quit()
            print("👋 Chrome 瀏覽器已安全關閉。")
            
    finally:
        # 關閉虛擬顯示器
        display.stop()
        print("🖥️ 虛擬顯示器已停止。")

if __name__ == "__main__":
    try:
        check_finders()
    except Exception as e:
        print(f"❌ Finders 檢查發生異常：{e}")
    check_eslite()
