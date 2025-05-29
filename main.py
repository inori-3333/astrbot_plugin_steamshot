import subprocess
import sys
import os
import time

def install_missing_packages():
    required_packages = ["selenium", "requests", "bs4", "webdriver-manager"]
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            print(f"⚠️ {package} 未安装，正在自动安装...")
            subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)

install_missing_packages()

# **🔹 依赖导入**
import ssl
import re
import asyncio
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *

# **🔹 Steam 商店 & 个人主页匹配正则**
STEAM_URL_PATTERN = r"https://store\.steampowered\.com/app/(\d+)/[\w\-]+/?"
STEAM_PROFILE_URL_PATTERN = r"https://steamcommunity\.com/(profiles/\d{17}|id/[A-Za-z0-9\-_]+)/?"

# **🔹 截图路径**
STORE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/store_screenshot.png"
PROFILE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/profile_screenshot.png"

# **🔹 指定 ChromeDriver 路径**
MANUAL_CHROMEDRIVER_PATH = r""
CHROMEDRIVER_PATH_FILE = "./chromedriver_path.txt"

def get_stored_chromedriver():
    """ 读取本地缓存的 ChromeDriver 路径 """
    if os.path.exists(CHROMEDRIVER_PATH_FILE):
        with open(CHROMEDRIVER_PATH_FILE, "r") as f:
            path = f.read().strip()
            if os.path.exists(path):
                return path
    return None

def get_chromedriver():
    """ 获取 ChromeDriver 路径，优先使用手动路径或缓存路径，若无则下载 """
    
    if MANUAL_CHROMEDRIVER_PATH and os.path.exists(MANUAL_CHROMEDRIVER_PATH):
        print(f"✅ 使用手动指定的 ChromeDriver: {MANUAL_CHROMEDRIVER_PATH}")
        return MANUAL_CHROMEDRIVER_PATH

    stored_path = get_stored_chromedriver()
    if stored_path:
        print(f"✅ 使用本地缓存的 ChromeDriver: {stored_path}")
        return stored_path

    print("⚠️ 未找到有效的 ChromeDriver，正在下载最新版本...")
    try:
        new_driver_path = ChromeDriverManager().install()
        with open(CHROMEDRIVER_PATH_FILE, "w") as f:
            f.write(new_driver_path)
        print(f"✅ 已下载并缓存 ChromeDriver: {new_driver_path}")
        return new_driver_path
    except Exception as e:
        print(f"❌ ChromeDriver 下载失败: {e}")
        return None

CHROMEDRIVER_PATH = get_chromedriver()

def create_driver():
    """ 创建 Selenium WebDriver """
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-usb-device-detection")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation", "disable-usb", "enable-devtools"])

    service = Service(CHROMEDRIVER_PATH)
    service.creation_flags = 0x08000000
    service.log_output = subprocess.DEVNULL

    return webdriver.Chrome(service=service, options=options)

def bypass_steam_age_check(driver):
    """
    自动处理 Steam 年龄验证页面。如果当前页面是年龄验证页，填写出生日期并跳转。
    """
    try:
        if "agecheck" not in driver.current_url:
            return  # 不是年龄验证页面，直接返回

        print("🔞 检测到 Steam 年龄验证页面，正在自动跳过...")

        # 等待出生日期下拉框出现
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "ageYear")))

        # 选择出生日期
        Select(driver.find_element(By.ID, "ageYear")).select_by_visible_text("2000")

        # 保存跳转前的 URL
        before_url = driver.current_url

        # 尝试执行 JS 跳转函数
        driver.execute_script("ViewProductPage()")

        # 等待 URL 发生变化，表示跳转成功
        WebDriverWait(driver, 10).until(EC.url_changes(before_url))
        print("✅ 已跳转至游戏页面")

        # 再等待游戏名称加载出来
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "apphub_AppName"))
        )

    except Exception as e:
        print(f"⚠️ Steam 年龄验证跳过失败: {e}")

async def capture_screenshot(url, save_path):
    """ 截取网页完整截图（支持懒加载内容） """
    def run():
        driver = None
        try:
            driver = create_driver()
            driver.set_page_load_timeout(15)

            for attempt in range(3):
                try:
                    driver.get(url)
                    bypass_steam_age_check(driver)
                    break
                except Exception:
                    print(f"⚠️ 第 {attempt + 1} 次刷新页面...")
                    driver.refresh()

            # 等待页面初步加载完成
            time.sleep(2)

            # 自动滚动以触发懒加载
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)  # 等待内容加载，可视页面内容调整
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            # 设置窗口为整页高度以便完整截图
            driver.set_window_size(1440, last_height)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            driver.save_screenshot(save_path)
            print(f"✅ 截图已保存: {save_path}")

        except Exception as e:
            print(f"❌ 截图错误: {e}")

        finally:
            if driver:
                driver.quit()

    await asyncio.to_thread(run)

async def get_steam_page_info(url):
    """ 解析 Steam 商店页面信息 """
    def parse():
        driver = create_driver()
        try:
            driver.set_page_load_timeout(15)
            for attempt in range(3):
                try:
                    driver.get(url)
                    bypass_steam_age_check(driver)
                    break
                except Exception:
                    print(f"⚠️ 第 {attempt + 1} 次刷新页面...")
                    driver.refresh()

            soup = BeautifulSoup(driver.page_source, "html.parser")

            game_name = soup.find("div", class_="apphub_AppName")
            game_name = game_name.text.strip() if game_name else "未知"

            release_date = soup.find("div", class_="date")
            release_date = release_date.text.strip() if release_date else "未知"

            developers = [a.text.strip() for a in soup.select("div#developers_list a")]
            developers = ", ".join(developers) if developers else "未知"

            publisher_div = soup.find("div", class_="dev_row")
            publisher = "未知"
            if publisher_div:
                next_div = publisher_div.find_next_sibling("div")
                if next_div:
                    # **🔥 直接获取纯文本，并去掉前缀 "发行商:"**
                    publisher = next_div.get_text(strip=True).replace("发行商:", "").strip()


            tags = soup.select("a.app_tag")
            tags = "，".join([tag.text.strip() for tag in tags[:5]]) if tags else "未知"

            description_div = soup.find("div", class_="game_description_snippet")
            description = description_div.text.strip() if description_div else "暂无简介"

            review_summary = soup.find("span", class_="game_review_summary")
            review_summary = review_summary.text.strip() if review_summary else "暂无评分"

            price = soup.find("div", class_="discount_final_price") or soup.find("div", class_="game_purchase_price")
            price = price.text.strip() if price else "暂无售价"

            return {
                "🎮 游戏名称": game_name,
                "📅 发行时间": release_date,
                "🏗 开发商": developers,
                "🏛 发行商": publisher,
                "🎭 游戏类别": tags,
                "📜 简介": description,
                "⭐ 评分": review_summary,
                "💰 价格": price
            }

        finally:
            driver.quit()

    return await asyncio.to_thread(parse)

async def process_steam_store(event, steam_url):
    """ 处理 Steam 商店信息 """
    result = MessageChain()
    screenshot_task = asyncio.create_task(capture_screenshot(steam_url, STORE_SCREENSHOT_PATH))
    info_task = asyncio.create_task(get_steam_page_info(steam_url))

    await asyncio.gather(screenshot_task, info_task)

    game_info = await info_task
    info_text = "\n".join([f"{key}: {value}" for key, value in game_info.items()])
    
    result.chain.append(Plain(info_text))
    
    if os.path.exists(STORE_SCREENSHOT_PATH):
        result.chain.append(Image.fromFileSystem(STORE_SCREENSHOT_PATH))

    await event.send(result)

async def process_steam_profile(event, profile_url):
    """ 处理 Steam 个人主页 """
    result = MessageChain()
    await capture_screenshot(profile_url, PROFILE_SCREENSHOT_PATH)

    if os.path.exists(PROFILE_SCREENSHOT_PATH):
        result.chain.append(Image.fromFileSystem(PROFILE_SCREENSHOT_PATH))

    await event.send(result)

@register("astrbot_plugin_steamshot", "Inori-3333", "检测 Steam 链接，截图并返回游戏信息", "1.6.0", "https://github.com/inori-3333/astrbot_plugin_steamshot")
class SteamPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.regex(STEAM_URL_PATTERN)
    async def handle_steam_store(self, event: AstrMessageEvent):
        steam_url = re.search(STEAM_URL_PATTERN, event.message_str).group(0)
        await process_steam_store(event, steam_url)

    @filter.regex(STEAM_PROFILE_URL_PATTERN)
    async def handle_steam_profile(self, event: AstrMessageEvent):
        profile_url = re.search(STEAM_PROFILE_URL_PATTERN, event.message_str).group(0)
        await process_steam_profile(event, profile_url)
