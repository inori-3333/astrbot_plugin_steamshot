import subprocess
import sys
import ssl
import re
import requests
import os
import subprocess
import sys
import ssl

# 禁用 SSL 证书验证，避免某些环境下载失败
ssl._create_default_https_context = ssl._create_unverified_context

# **自动安装指定的依赖包**
REQUIRED_PACKAGES = [
    "selenium",
    "webdriver_manager",
    "requests",
    "beautifulsoup4"
]

def install_packages():
    for package in REQUIRED_PACKAGES:
        try:
            __import__(package)  # 尝试导入
        except ImportError:
            print(f"📦 未找到 {package}，正在安装...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"✅ {package} 安装成功！")

# **执行安装**
install_packages()

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *

# Steam 商店和个人主页正则表达式
STEAM_URL_PATTERN = r"https://store\.steampowered\.com/app/\d+/[\w\-]+/?"
STEAM_PROFILE_URL_PATTERN = r"https://steamcommunity\.com/(profiles/\d{17}|id/[A-Za-z0-9\-_]+)/?"

# 截图保存路径
STORE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/store_screenshot.png"
PROFILE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/profile_screenshot.png"

# **截图函数（适用于商店和个人主页）**
def capture_screenshot(url, save_path):
    driver = None
    try:
        options = Options()
        options.add_argument("--headless")  # 无头模式
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation", "disable-usb"])  # 禁用 USB

        driver = webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=options)
        driver.set_window_size(1440, 1600)
        driver.set_page_load_timeout(15)  # 限制页面加载时间
        driver.get(url)
        driver.implicitly_wait(5)  # 等待页面加载

        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        driver.save_screenshot(save_path)
        print(f"✅ 截图已保存至: {save_path}")
        return True

    except Exception as e:
        print(f"❌ 截图错误: {e}")
        return False  # 防止卡死

    finally:
        if driver:
            driver.quit()

# **使用原来的 `get_steam_page_info(url)` 获取完整的游戏信息**
def get_steam_page_info(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9"  # 强制请求中文页面
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # 检查年龄验证页面
        if "agecheck" in response.url:
            print("需要年龄验证，跳过此页面")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # 游戏名称
        game_name = soup.find("div", class_="apphub_AppName")
        game_name = game_name.text.strip() if game_name else "未知"

        # 发行时间
        release_date = soup.find("div", class_="date")
        release_date = release_date.text.strip() if release_date else "未知"

        # 开发商
        developers = [a.text.strip() for a in soup.select("div#developers_list a")]
        developers = ", ".join(developers) if developers else "未知"

        # 发行商
        publisher_div = soup.find("div", class_="dev_row")
        publisher = publisher_div.find_next_sibling("div").text.strip() if publisher_div else "未知"

        # 游戏类别（仅保留前 5 个）
        tags = soup.select("a.app_tag")
        tags = "，".join([tag.text.strip() for tag in tags[:5]]) if tags else "未知"

        # 游戏简介（完整保留）
        description_div = soup.find("div", class_="game_description_snippet")
        description = description_div.text.strip() if description_div else "暂无简介"

        # 评分
        review_summary = soup.find("span", class_="game_review_summary")
        review_summary = review_summary.text.strip() if review_summary else "暂无评分"

        # 价格
        price = soup.find("div", class_="game_purchase_price")
        price = price.text.strip() if price else "暂无售价"

        # 只判断是否支持中文
        language_table = soup.find("table", class_="game_language_options")
        support_chinese = "不支持中文"
        if language_table:
            languages = [row.find("td").text.strip() for row in language_table.find_all("tr")[1:]]
            if any("简体中文" in lang or "繁体中文" in lang for lang in languages):
                support_chinese = "支持中文"

        return {
            "游戏名称": game_name,
            "发行时间": release_date,
            "开发商": developers,
            "发行商": publisher,
            "游戏类别": tags,
            "简介": description,
            "评分": review_summary,
            "价格": price,
            "是否支持中文": support_chinese
        }

    except requests.exceptions.RequestException as e:
        print(f"❌ 请求错误: {e}")
        return None
    except AttributeError as e:
        print(f"❌ 解析错误，可能页面结构已改变: {e}")
        return None

# **注册插件**
@register("astrbot_plugin_steamshot", "Inori-3333", "检测 Steam 链接，截图并返回游戏信息", "1.2.0", "https://github.com/inori-3333/astrbot_plugin_steamshot")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # **监听 Steam 商店 URL**
    @filter.regex(STEAM_URL_PATTERN)
    async def handle_steam_store(self, event: AstrMessageEvent):
        msg = event.message_str
        match = re.search(STEAM_URL_PATTERN, msg)
        if match:
            steam_url = match.group(0)
            await self.process_steam_store(event, steam_url)

    # **监听 Steam 个人主页 URL**
    @filter.regex(STEAM_PROFILE_URL_PATTERN)
    async def handle_steam_profile(self, event: AstrMessageEvent):
        msg = event.message_str
        match = re.search(STEAM_PROFILE_URL_PATTERN, msg)
        if match:
            profile_url = match.group(0)
            await self.process_steam_profile(event, profile_url)

    # **处理 Steam 商店**
    async def process_steam_store(self, event: AstrMessageEvent, steam_url: str):
        result = MessageChain()
        result.chain = []

        capture_screenshot(steam_url, STORE_SCREENSHOT_PATH)
        if os.path.exists(STORE_SCREENSHOT_PATH):
            result.chain.append(Image.fromFileSystem(STORE_SCREENSHOT_PATH))

        game_info = get_steam_page_info(steam_url)
        if game_info:
            game_info_str = "\n".join([f"{key}: {value}" for key, value in game_info.items()])
            result.chain.append(Plain(game_info_str))
        else:
            result.chain.append(Plain("游戏信息抓取失败，请检查 URL 格式是否正确或稍后再试。"))

        await event.send(result)

    # **处理 Steam 个人主页**
    async def process_steam_profile(self, event: AstrMessageEvent, profile_url: str):
        result = MessageChain()
        result.chain = []

        capture_screenshot(profile_url, PROFILE_SCREENSHOT_PATH)
        if os.path.exists(PROFILE_SCREENSHOT_PATH):
            result.chain.append(Image.fromFileSystem(PROFILE_SCREENSHOT_PATH))
        else:
            result.chain.append(Plain("个人主页截图失败，请检查 URL 格式是否正确或稍后再试。"))

        await event.send(result)