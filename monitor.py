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
TARGET_URL = "https://www.eslite.com/Search?keyword=owala+freesip+%E4%B8%8D%E9%8F%BD%E9%8B%BC&final_price=0,&publishDate=0&sort=_weight_+desc&size=20&display=list&start=0&exp=c"

# 目標規格：三層不鏽鋼保溫杯 710ml (24oz) 與 945ml (32oz)，顏色不限
CAPACITY_KEYWORDS = ("710", "24oz", "24 oz", "945", "32oz", "32 oz")
EXCLUDE_KEYWORDS = ("tritan",)
# 名稱必須含不鏽鋼字樣（兼容「不鏽鋼 / 不銹鋼 / stainless」寫法）
MATERIAL_KEYWORDS = ("不鏽鋼", "不銹鋼", "stainless", "保溫", "保冰")

FINDERS_URLS = [
    "https://www.finders.com.tw/products/owala-freesip-24oz",
    "https://www.finders.com.tw/products/owala-freesip-32oz",
    "https://www.finders.com.tw/products/owala-fs-special",
]
# Finders 的 SKU 帶有容量，如 FreesipSS_24oz_Blue Steel；要 24oz (710ml) 與 32oz (945ml)
FINDERS_SKU_FILTER = ("24oz", "32oz")
FINDERS_SIZE_LABEL = {"24oz": "710ml", "32oz": "945ml"}
FINDERS_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

def is_target_spec(name):
    low = name.lower()
    return any(k in low for k in CAPACITY_KEYWORDS) and any(k in low for k in MATERIAL_KEYWORDS) and not any(k in low for k in EXCLUDE_KEYWORDS)

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
        "content": f"🚨 **【補貨警報】** {source}偵測到 Owala 三層不鏽鋼保溫杯 (710/945ml) 有款式可購買啦！趕快檢查下方清單！",
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
            if not qty or not any(k in sku for k in FINDERS_SKU_FILTER):
                continue
            name = re.search(B + r'"fields' + B + r'":\[\{' + B + r'"name_translations' + B + r'":\{[^}]*\},' + B + r'"name' + B + r'":' + B + r'"([^"' + B + r']*)', c)
            price = re.search(B + r'"price_sale' + B + r'":\{[^}]*?' + B + r'"dollars' + B + r'":([\d.]+)', c)
            color = name.group(1) if name else sku
            size = next(FINDERS_SIZE_LABEL[k] for k in FINDERS_SKU_FILTER if k in sku)
            total += 1
            if int(qty.group(1)) > 0:
                in_stock += 1
                available.append({
                    "name": f"Owala FreeSip 不鏽鋼 {size}｜{color}",
                    "url": url,
                    "price": f"{float(price.group(1)):,.0f}" if price else "未知",
                    "status": f"庫存 {qty.group(1)}",
                })
                print(f" 🟢 【Finders 有貨】{color} x{qty.group(1)} | {url}")
        print(f"📊 Finders {url.rsplit('/', 1)[-1]}：710/945ml 共 {total} 款，有貨 {in_stock} 款")
        if not chunks:
            print("⚠️ 未解析到任何款式，頁面結構可能已變動。")

    if available:
        send_discord_notification(available, source="Finders")
    else:
        print("💤 Finders 目前 710/945ml 皆無貨。")

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
    # 1. 在雲端伺服器啟動 1920x1080 的虛擬桌面顯示器 (Xvfb)
    print("🖥️ 正在啟動 Linux 虛擬螢幕顯示器 (Xvfb)，準備偽裝為真實桌面...")
    display = Display(visible=0, size=(1920, 1080))
    display.start()

    try:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🌐 正在使用 GUI 模式進入誠品線上...")
        driver = create_driver()
        
        try:
            driver.get(TARGET_URL)
            
            # 給予 Cloudflare 稍微充足的時間進行背景 JS 靜默驗證
            print("⏳ 正在等待網頁載入 (正在通過 Cloudflare 靜默指紋驗證)...")
            wait = WebDriverWait(driver, 25)
            try:
                wait.until(EC.presence_of_element_located((By.CLASS_NAME, "product-item")))
                print("🎉 成功突破 Cloudflare 防線！網頁商品卡片已完全載入！")
            except TimeoutException:
                print("⚠️ 警告：25 秒內未找到商品卡片。")
                print(f"📄 當前頁面標題：{driver.title}")
                return

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(1.5)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            product_items = driver.find_elements(By.CLASS_NAME, "product-item")
            print(f"📦 成功鎖定 {len(product_items)} 款商品，開始逐一分析庫存狀態...\n" + "-"*50)

            available_products = []
            out_of_stock_count = 0

            for index, item in enumerate(product_items, 1):
                try:
                    name_elem = item.find_element(By.CLASS_NAME, "product-name")
                    prod_name = name_elem.text.strip()
                    if not is_target_spec(prod_name):
                        print(f" ⏭️ [{index:02d}] 規格不符，略過：{prod_name}")
                        continue
                    
                    link_elem = item.find_element(By.CLASS_NAME, "item-image-link")
                    prod_url = link_elem.get_attribute("href")

                    try:
                        price_elem = item.find_element(By.CLASS_NAME, "slider-price")
                        prod_price = price_elem.text.strip()
                    except NoSuchElementException:
                        prod_price = "未知"

                    try:
                        buy_btn = item.find_element(By.CSS_SELECTOR, "button[data-gid='add-cart-button']")
                        status_text = buy_btn.text.strip()
                        print(f" 🟢 [{index:02d}] 【發現有貨！】 {prod_name} | 價格: ${prod_price}")
                        available_products.append({
                            "name": prod_name,
                            "url": prod_url,
                            "price": prod_price,
                            "status": status_text
                        })
                    except NoSuchElementException:
                        out_of_stock_count += 1
                        try:
                            notice_btn = item.find_element(By.CSS_SELECTOR, "button[data-gid='notice-button']")
                            status_text = notice_btn.text.strip()
                        except NoSuchElementException:
                            status_text = "暫無庫存/未開放"
                        pass

                except Exception as e:
                    continue

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
