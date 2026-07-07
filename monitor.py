import os
import time
import subprocess
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# 從 GitHub Secrets 讀取 Discord Webhook URL
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TARGET_URL = "https://www.eslite.com/Search?keyword=owala+freesip+tritan&final_price=0,&publishDate=0&sort=_weight_+desc&size=20&display=list&start=0&exp=c"

def send_discord_notification(available_products):
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
            "footer": {"text": "誠品防偵測瀏覽器監控系統 (GitHub Actions)"},
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
        print(f"❌ 發送 Discord Webhook 時發生錯誤：{e}")

def get_chrome_major_version():
    """自動偵測 GitHub Actions 主機上的 Google Chrome 主版號"""
    try:
        # 透過系統指令查詢 Google Chrome 版本
        output = subprocess.check_output(['google-chrome', '--version'], text=True)
        # 輸出範例: "Google Chrome 149.0.7827.0 ..." -> 擷取 "149"
        version_str = output.strip().split()[2]
        major_version = int(version_str.split('.')[0])
        print(f"ℹ️ 成功偵測到主機 Chrome 瀏覽器主版號為：{major_version}")
        return major_version
    except Exception as e:
        print(f"⚠️ 無法自動偵測 Chrome 版本，將由套件自行決定：{e}")
        return None

def create_driver():
    print("🛠️ 正在初始化防偵測 Chrome 瀏覽器 (Undetected ChromeDriver)...")
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=zh-TW")
    
    # 獲取當前主機的 Chrome 版本，並強制對齊 ChromeDriver 版本
    chrome_version = get_chrome_major_version()
    if chrome_version:
        driver = uc.Chrome(options=options, version_main=chrome_version)
    else:
        driver = uc.Chrome(options=options)
        
    return driver

def check_eslite():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🌐 正在使用防偵測瀏覽器進入誠品線上...")
    driver = create_driver()
    
    try:
        driver.get(TARGET_URL)
        
        print("⏳ 正在等待網頁與商品卡片載入 (同時通過 Cloudflare 背景檢測)...")
        wait = WebDriverWait(driver, 20)
        try:
            wait.until(EC.presence_of_element_located((By.CLASS_NAME, "product-item")))
        except TimeoutException:
            print("⚠️ 警告：20 秒內未找到商品卡片。可能是關鍵字無結果，或 Cloudflare 正在要求進一步驗證。")
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
        print("👋 瀏覽器已安全釋放。")

if __name__ == "__main__":
    check_eslite()
