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

# **🔹 Steam 链接匹配正则**
STEAM_URL_PATTERN = r"https://store\.steampowered\.com/app/(\d+)/[\w\-]+/?"
STEAM_PROFILE_URL_PATTERN = r"https://steamcommunity\.com/(profiles/\d{17}|id/[A-Za-z0-9\-_]+)/?"
STEAM_WORKSHOP_URL_PATTERN = r"https://steamcommunity\.com/(sharedfiles/filedetails|workshop/filedetails)/\?id=(\d+)"

# **🔹 截图路径**
STORE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/store_screenshot.png"
PROFILE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/profile_screenshot.png"
WORKSHOP_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/workshop_screenshot.png"

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

async def get_steam_workshop_info(url):
    """ 解析 Steam 创意工坊页面信息 """
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
            
            info = {}

            # 1. 获取模组名称
            title = soup.find("div", class_="workshopItemTitle")
            info["🛠️ 模组名称"] = title.text.strip() if title else "未知"

            # 2. 获取作者信息 - 精确提取作者名
            author_block = soup.find("div", class_="friendBlockContent")
            if author_block:
                # 获取第一个文本节点（作者名）
                author_name = next((text for text in author_block.stripped_strings), "未知").split('\n')[0]
                info["👤 作者"] = author_name.strip()
                
                # 尝试获取作者链接
                author_link = author_block.find("a")
                if author_link:
                    author_href = author_link["href"]
                    if not author_href.startswith("http"):
                        author_href = "https://steamcommunity.com" + author_href
                    info["🔗 作者主页"] = author_href
                else:
                    # 如果没有链接，尝试从作者名构造个人资料链接
                    if author_name and author_name != "未知":
                        info["🔗 作者主页"] = f"https://steamcommunity.com/id/{author_name}"
            else:
                info["👤 作者"] = "未知"

            # 3. 获取订阅数 - 更可靠的查找方式
            subscribers = soup.find("div", class_="numSubscribers") or \
                        soup.find("div", class_="detailsStatRight", string=re.compile(r"\d+(\,\d+)*"))
            info["📊 订阅数"] = subscribers.text.strip() if subscribers else "未知"

            # 4. 获取详细信息（大小、创建日期）
            stats_container = soup.find("div", class_="detailsStatsContainerRight")
            if stats_container:
                stats_items = stats_container.find_all("div", class_="detailsStatRight")
                if len(stats_items) >= 1:
                    info["📦 文件大小"] = stats_items[0].text.strip()
                if len(stats_items) >= 2:
                    info["🗓️ 创建日期"] = stats_items[1].text.strip()
                # 有些页面可能没有更新日期
                if len(stats_items) >= 3:
                    info["🔄 更新日期"] = stats_items[2].text.strip()

            return info

        finally:
            driver.quit()

    return await asyncio.to_thread(parse)


async def process_steam_workshop(event, workshop_url):
    """ 处理 Steam 创意工坊链接 """
    result = MessageChain()

    info_task = asyncio.create_task(get_steam_workshop_info(workshop_url))
    screenshot_task = asyncio.create_task(capture_screenshot(workshop_url, WORKSHOP_SCREENSHOT_PATH))

    await asyncio.gather(info_task, screenshot_task)
    workshop_info = await info_task

    # 格式化输出信息
    formatted_info = []
    for key, value in workshop_info.items():
        if key in ["🔗 作者主页", "🎮 所属游戏"]:
            # 这些字段已经包含完整URL，直接显示
            formatted_info.append(f"{key}: {value}")
        else:
            formatted_info.append(f"{key}: {value}")

    if formatted_info:
        result.chain.append(Plain("\n".join(formatted_info)))

    if os.path.exists(WORKSHOP_SCREENSHOT_PATH):
        result.chain.append(Image.fromFileSystem(WORKSHOP_SCREENSHOT_PATH))

    await event.send(result)


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

async def get_steam_profile_info(url):
    """ 解析 Steam 个人主页信息 """
    def parse():
        driver = create_driver()
        try:
            driver.set_page_load_timeout(15)
            driver.get(url)
            bypass_steam_age_check(driver)
            time.sleep(2)  # 等待页面渲染完整

            soup = BeautifulSoup(driver.page_source, "html.parser")
            info = []

            # 1. 解析 Steam ID
            name_span = soup.find("span", class_="actual_persona_name")
            if name_span:
                steam_id = name_span.text.strip()
                info.append(f"steam id: {steam_id}")

            # 2. 检查是否为私密主页
            if soup.find("div", class_="profile_private_info"):
                info.append("个人简介: 此个人资料是私密的。")
                return info

            # 3. 解析个人简介
            summary = soup.find("div", class_="profile_summary")
            if summary:
                # 移除图片和链接，仅保留文字
                for tag in summary.find_all(["img", "a"]):
                    tag.decompose()
                profile_text = summary.get_text(separator="\n", strip=True)
                if profile_text:
                    info.append(f"个人简介: {profile_text}")
            else:
                pass  # 没有简介，继续解析下方内容

            # 4. 解析 Steam 等级
            level_span = soup.find("span", class_="friendPlayerLevelNum")
            if level_span:
                level = level_span.text.strip()
                info.append(f"steam等级: {level}")

            # 5. 解析地区
            location_div = soup.find("div", class_="header_location")
            if location_div:
                location_text = location_div.get_text(strip=True)
                if location_text:
                    info.append(f"地区: {location_text}")

            # 6. 解析当前状态
            status_div = soup.find("div", class_="responsive_status_info")
            if status_div:
                status_header = status_div.find("div", class_="profile_in_game_header")
                if status_header:
                    status = status_header.text.strip()
                    if status == "当前离线":
                        info.append("当前状态: 当前离线")
                    elif status == "当前在线":
                        info.append("当前状态: 当前在线")
                    elif status == "当前正在游戏":
                        game_name_div = status_div.find("div", class_="profile_in_game_name")
                        game_name = game_name_div.text.strip() if game_name_div else "未知游戏"
                        info.append(f"当前状态: 当前正在游戏 {game_name}")

            # 7. 解析游戏数
            game_count = None
            for link in soup.find_all("a", href=True):
                if "games/?tab=all" in link["href"]:
                    count_span = link.find("span", class_="profile_count_link_total")
                    if count_span:
                        game_count = count_span.text.strip()
                        if game_count:
                            info.append(f"游戏数: {game_count}")
                    break

            # 8. 解析好友数
            for link in soup.find_all("a", href=True):
                if "/friends/" in link["href"]:
                    count_span = link.find("span", class_="profile_count_link_total")
                    if count_span:
                        friend_count = count_span.text.strip()
                        if friend_count:
                            info.append(f"好友数: {friend_count}")
                    break

            return info

        finally:
            driver.quit()

    return await asyncio.to_thread(parse)

async def process_steam_profile(event, profile_url):
    """ 处理 Steam 个人主页 """
    result = MessageChain()

    info_task = asyncio.create_task(get_steam_profile_info(profile_url))
    screenshot_task = asyncio.create_task(capture_screenshot(profile_url, PROFILE_SCREENSHOT_PATH))

    await asyncio.gather(info_task, screenshot_task)
    profile_info = await info_task

    # 表情映射
    emoji_map = {
        "steam id": "🆔",
        "个人简介": "📝",
        "steam等级": "🎖",
        "地区": "📍",
        "当前状态: 当前在线": "🟢",
        "当前状态: 当前离线": "🔴",
        "当前状态: 当前正在游戏": "🎮",
        "游戏数": "🎮",
        "好友数": "👥",
        "此个人资料是私密的": "🔒"
    }

    formatted_lines = []
    for line in profile_info:
        key = line.split(":")[0].strip()
        matched_emoji = None

        for k, emoji in emoji_map.items():
            if line.startswith(k) or k in line:
                matched_emoji = emoji
                break

        if matched_emoji:
            formatted_lines.append(f"{matched_emoji} {line}")
        else:
            formatted_lines.append(line)

    if formatted_lines:
        result.chain.append(Plain("\n".join(formatted_lines)))

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

    @filter.regex(STEAM_WORKSHOP_URL_PATTERN)
    async def handle_steam_workshop(self, event: AstrMessageEvent):
        match = re.search(STEAM_WORKSHOP_URL_PATTERN, event.message_str)
        workshop_id = match.group(2)
        workshop_url = f"https://steamcommunity.com/sharedfiles/filedetails/?id={workshop_id}"
        await process_steam_workshop(event, workshop_url)
