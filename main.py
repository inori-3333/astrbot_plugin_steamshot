import subprocess
import sys
import os
import time
import winreg

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
    """ 获取 ChromeDriver 路径，优先使用手动路径或缓存路径，若无则下载。
        若已有驱动但版本与当前 Chrome 不符（前三位版本号），则重新下载。
    """
    def get_browser_version():
        try:
            if sys.platform.startswith("win"):
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Google\\Chrome\\BLBeacon")
                version, _ = winreg.QueryValueEx(key, "version")
                return version
            elif sys.platform.startswith("linux"):
                result = subprocess.run(["google-chrome", "--version"], capture_output=True, text=True)
                return result.stdout.strip().split()[-1]
            elif sys.platform == "darwin":
                result = subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"], capture_output=True, text=True)
                return result.stdout.strip().split()[-1]
        except Exception:
            return None

    def extract_driver_version_from_path(path):
        try:
            parts = os.path.normpath(path).split(os.sep)
            for part in parts:
                if part.count(".") >= 2:
                    return part  # e.g., '137.0.7151.68'
            return None
        except Exception:
            return None

    def versions_match(browser_ver, driver_ver):
        try:
            b_parts = browser_ver.split(".")[:3]
            d_parts = driver_ver.split(".")[:3]
            return b_parts == d_parts
        except Exception:
            return False

    browser_version = get_browser_version()

    if MANUAL_CHROMEDRIVER_PATH and os.path.exists(MANUAL_CHROMEDRIVER_PATH):
        driver_version = extract_driver_version_from_path(MANUAL_CHROMEDRIVER_PATH)
        print(f"🌐 检测到浏览器版本: {browser_version}, 当前驱动版本: {driver_version}")
        if browser_version and driver_version and versions_match(browser_version, driver_version):
            print(f"✅ 使用手动指定的 ChromeDriver: {MANUAL_CHROMEDRIVER_PATH}（版本匹配）")
            return MANUAL_CHROMEDRIVER_PATH
        else:
            print("⚠️ 手动指定的 ChromeDriver 版本与浏览器不匹配，忽略使用")

    stored_path = get_stored_chromedriver()
    if stored_path and os.path.exists(stored_path):
        driver_version = extract_driver_version_from_path(stored_path)
        print(f"🌐 检测到浏览器版本: {browser_version}, 当前驱动版本: {driver_version}")
        if browser_version and driver_version and versions_match(browser_version, driver_version):
            print(f"✅ 使用本地缓存的 ChromeDriver: {stored_path}（版本匹配）")
            return stored_path
        else:
            print("⚠️ 本地 ChromeDriver 版本不匹配（前三位），准备重新下载...")
            try:
                os.remove(stored_path)
                print("🗑 已删除旧的驱动")
            except Exception as e:
                print(f"❌ 删除旧驱动失败: {e}")

    print("⚠️ 未找到有效的 ChromeDriver 或需重新下载，正在下载最新版本...")
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
            
            # 0. 获取游戏名称和链接
            breadcrumbs = soup.find("div", class_="breadcrumbs")
            if breadcrumbs:
                game_link = breadcrumbs.find("a")
                if game_link:
                    info["🎮 所属游戏"] = game_link.text.strip()
                    game_href = game_link["href"]
                    if not game_href.startswith("http"):
                        game_href = "https://steamcommunity.com" + game_href
                    info["🔗 游戏链接"] = game_href

            # 1. 获取模组名称
            title = soup.find("div", class_="workshopItemTitle")
            info["🛠️ 模组名称"] = title.text.strip() if title else "未知"

            # 2. 获取作者信息和真实主页链接
            creator_block = soup.find("div", class_="creatorsBlock")
            if creator_block:
                author_name = next((text for text in creator_block.stripped_strings if text.strip()), "未知")
                author_link = creator_block.find("a")
                if author_link:
                    info["👤 作者"] = author_name.split('\n')[0].strip()
                    author_href = author_link["href"]
                    if not author_href.startswith("http"):
                        author_href = "https://steamcommunity.com" + author_href
                    info["🔗 作者主页"] = author_href
                    
                    status = creator_block.find("span", class_="friendSmallText")
                    if status:
                        info["🟢 作者状态"] = status.text.strip()

            # 3. 获取评分信息
            rating_section = soup.find("div", class_="ratingSection")
            if rating_section:
                rating_img = rating_section.find("img")
                if rating_img:
                    info["⭐ 评分"] = rating_img["src"].split("/")[-1].split("_")[0] + " stars"
                num_ratings = rating_section.find("div", class_="numRatings")
                if num_ratings:
                    info["📈 评分数量"] = num_ratings.text.strip()

            # 4. 获取统计数据（访客、订阅、收藏）
            stats_table = soup.find("table", class_="stats_table")
            if stats_table:
                for row in stats_table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) == 2:
                        value = cells[0].text.strip()
                        label = cells[1].text.strip()
                        
                        if "Unique Visitors" in label:
                            info["👀 访客数"] = value
                        elif "Current Subscribers" in label:
                            info["📊 订阅数"] = value
                        elif "Current Favorites" in label:
                            info["❤️ 收藏数"] = value

            # 5. 获取文件大小和日期信息
            stats_right = soup.find("div", class_="detailsStatsContainerRight")
            if stats_right:
                stats_items = stats_right.find_all("div", class_="detailsStatRight")
                if len(stats_items) >= 1:
                    info["📦 文件大小"] = stats_items[0].text.strip()
                if len(stats_items) >= 2:
                    info["🗓️ 创建日期"] = stats_items[1].text.strip()
                if len(stats_items) >= 3:
                    info["🔄 更新日期"] = stats_items[2].text.strip()

            # 6. 获取标签信息
            tags_container = soup.find("div", class_="rightDetailsBlock")
            if tags_container:
                tags = []
                for tag_div in tags_container.find_all("div", class_="workshopTags"):
                    tag_title = tag_div.find("span", class_="workshopTagsTitle")
                    if tag_title:
                        tag_text = tag_title.text.replace(":", "").strip()
                        tag_links = [a.text for a in tag_div.find_all("a")]
                        if tag_links:
                            tags.append(f"{tag_text}: {', '.join(tag_links)}")
                if tags:
                    info["🏷️ 标签"] = "\n".join(tags)

            # 7. 获取描述内容
            description = soup.find("div", class_="workshopItemDescription")
            if description:
                for tag in description.find_all(["script", "style", "img", "a"]):
                    tag.decompose()
                desc_text = description.get_text(separator="\n", strip=True)
                info["📝 描述"] = desc_text[:500] + "..." if len(desc_text) > 500 else desc_text

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

    formatted_info = []
    
    # 优先显示游戏信息
    if "🎮 所属游戏" in workshop_info:
        game_info = f"游戏名称: {workshop_info['🎮 所属游戏']}"
        if "🔗 游戏链接" in workshop_info:
            game_info += f" {workshop_info['🔗 游戏链接']}"
        formatted_info.append(game_info)
        formatted_info.append("")
    
    # 添加其他信息
    for key, value in workshop_info.items():
        if key not in ["🎮 所属游戏", "🔗 游戏链接"]:
            if key in ["🔗 作者主页", "🖼️ 预览图"]:
                formatted_info.append(f"{key}: {value}")
            elif key == "🏷️ 标签":
                formatted_info.append(f"{key}:")
                formatted_info.append(value)
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
    """ 解析 Steam 个人主页信息（支持完整最新动态） """
    def parse():
        driver = create_driver()
        if not driver:
            return []

        standard_profile_lines = []
        recent_activity_parsed_lines = []

        try:
            driver.set_page_load_timeout(15)
            driver.get(url)
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")

            # 1. Steam ID
            name_span = soup.find("span", class_="actual_persona_name")
            if name_span:
                steam_id = name_span.text.strip()
                standard_profile_lines.append(f"steam id: {steam_id}")

            # 🔒 1.5 检查封禁状态（如有则立即返回封禁信息）
            ban_section = soup.find("div", class_="profile_ban_status")
            if ban_section:
                ban_records = []
                for div in ban_section.find_all("div", class_="profile_ban"):
                    ban_text = div.get_text(strip=True).replace("|信息", "").strip()
                    if ban_text:
                        ban_records.append(ban_text)
                # 提取封禁时间（如有）
                ban_status_text = ban_section.get_text(separator="\n", strip=True)
                for line in ban_status_text.split("\n"):
                    if "封禁于" in line:
                        ban_records.append(line.strip())
                if ban_records:
                    standard_profile_lines.append(f"🚫 封禁纪录: \n{'\n'.join(ban_records)}")

            # 2. 私密资料判断
            is_private = False
            if soup.find("div", class_="profile_private_info"):
                standard_profile_lines.append("此个人资料是私密的")
                is_private = True

            # 3. 简介
            if not is_private:
                summary_div = soup.find("div", class_="profile_summary")
                if summary_div:
                    for tag in summary_div.find_all(["img"]):
                        tag.decompose()
                    profile_text = summary_div.get_text(separator="\n", strip=True)
                    if profile_text:
                        standard_profile_lines.append(f"个人简介: \n{profile_text}")

            # 4. 等级
            level_span = soup.find("span", class_="friendPlayerLevelNum")
            if level_span:
                standard_profile_lines.append(f"steam等级: {level_span.text.strip()}")

            # 5. 地区
            location_div = soup.find("div", class_="header_location")
            if location_div:
                standard_profile_lines.append(f"地区: {location_div.get_text(strip=True)}")

            # 6. 当前状态
            status_div = soup.find("div", class_="responsive_status_info")
            if status_div:
                header = status_div.find("div", class_="profile_in_game_header")
                if header:
                    state = header.text.strip()
                    if state == "当前离线":
                        standard_profile_lines.append("当前状态: 当前离线")
                    elif state == "当前在线":
                        standard_profile_lines.append("当前状态: 当前在线")
                    elif state == "当前正在游戏":
                        game_name_div = status_div.find("div", class_="profile_in_game_name")
                        game_name = game_name_div.text.strip() if game_name_div else "未知游戏"
                        standard_profile_lines.append(f"当前状态: 当前正在游戏 \n                     {game_name}")

            # 7. 游戏数
            for link in soup.find_all("a", href=True):
                if "games/?tab=all" in link["href"]:
                    count_span = link.find("span", class_="profile_count_link_total")
                    if count_span:
                        standard_profile_lines.append(f"游戏数: {count_span.text.strip()}")
                    break

            # 8. 好友数
            for link in soup.find_all("a", href=True):
                if link["href"].endswith("/friends/"):
                    count_span = link.find("span", class_="profile_count_link_total")
                    if count_span:
                        standard_profile_lines.append(f"好友数: {count_span.text.strip()}")
                    break

            # 9. 最新动态
            if not is_private:
                recent_activity_customization_div = None
                customization_divs = soup.find_all("div", class_="profile_customization")
                for div_block in customization_divs:
                    header = div_block.find("div", class_="profile_recentgame_header")
                    if header and "最新动态" in header.get_text(strip=True):
                        recent_activity_customization_div = div_block
                        break

                if recent_activity_customization_div:
                    playtime_header = recent_activity_customization_div.find("div", class_="profile_recentgame_header")
                    if playtime_header:
                        playtime_recent_div = playtime_header.find("div", class_="recentgame_recentplaytime")
                        if playtime_recent_div:
                            playtime_text_container = playtime_recent_div.find("div")
                            if playtime_text_container:
                                playtime = playtime_text_container.text.strip()
                                if playtime:
                                    recent_activity_parsed_lines.append(f"🕒 最新动态: {playtime}")

                    recent_games_block = recent_activity_customization_div.find("div", class_="recent_games")
                    if recent_games_block:
                        for game_div in recent_games_block.find_all("div", class_="recent_game", limit=3):
                            game_name_tag = game_div.find("div", class_="game_name")
                            game_name = game_name_tag.find("a", class_="whiteLink").text.strip() if game_name_tag and game_name_tag.find("a") else "未知游戏"

                            game_info_details_div = game_div.find("div", class_="game_info_details")
                            total_playtime = "未知总时数"
                            last_played = None
                            is_currently_playing = False

                            if game_info_details_div:
                                details_texts = [item.strip() for item in game_info_details_div.contents if isinstance(item, str) and item.strip()]
                                for part in details_texts:
                                    if part.startswith("总时数"):
                                        total_playtime = part
                                    elif part.startswith("最后运行日期："):
                                        last_played = part
                                    elif part == "当前正在游戏":
                                        is_currently_playing = True

                            recent_activity_parsed_lines.append(f"\n🎮 {game_name}: {total_playtime}")
                            if is_currently_playing:
                                recent_activity_parsed_lines.append(f"🎮 当前正在游戏")
                            elif last_played:
                                recent_activity_parsed_lines.append(f"📅 {last_played}")

                            ach_str = None
                            stats_div = game_div.find("div", class_="game_info_stats")
                            if stats_div:
                                ach_area = stats_div.find("div", class_="game_info_achievements_summary_area")
                                if ach_area:
                                    summary_span = ach_area.find("span", class_="game_info_achievement_summary")
                                    if summary_span:
                                        ach_text_tag = summary_span.find("a", class_="whiteLink")
                                        ach_progress_tag = summary_span.find("span", class_="ellipsis")
                                        if ach_text_tag and "成就进度" in ach_text_tag.text and ach_progress_tag:
                                            ach_str = f"🏆 {ach_text_tag.text.strip()}  {ach_progress_tag.text.strip()}"
                            if ach_str:
                                recent_activity_parsed_lines.append(f"{ach_str}")

            return standard_profile_lines + recent_activity_parsed_lines

        except Exception as e:
            print(f"❌ 解析 Steam 个人主页错误: {e}")
            combined_on_error = standard_profile_lines + recent_activity_parsed_lines
            return combined_on_error if combined_on_error else ["⚠️ 无法获取个人主页部分信息。"]

        finally:
            if driver:
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

async def steam_store_search(search_game_name: str, event: AstrMessageEvent):
    """访问 Steam 搜索页面并跳转第一个游戏结果"""
    url = f"https://store.steampowered.com/search/?term={search_game_name}&ndl=1"
    driver = create_driver()
    try:
        driver.get(url)
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, "html.parser")

        # 检查是否没有结果
        no_result_div = soup.select_one("#search_results .search_results_count")
        if no_result_div and "0 个匹配的搜索结果" in no_result_div.text:
            yield event.plain_result(f"❌ 没有找到名为 {search_game_name} 的游戏。")
            return

        # 查找第一条游戏链接
        result_container = soup.select_one("#search_resultsRows a")
        if result_container and result_container.has_attr("href"):
            game_url = result_container["href"]
            yield event.plain_result(f"🔍 正在解析符合条件的第一款游戏...\n🌐 链接：{game_url}")
            await process_steam_store(event, game_url)
        else:
            yield event.plain_result("⚠️ 未能找到有效的游戏搜索结果。")

    except Exception as e:
        yield event.plain_result(f"❌ 搜索失败: {e}")
    finally:
        driver.quit()

async def steam_user_search(search_user_name: str, event: AstrMessageEvent):
    """搜索 Steam 用户并获取其主页 URL，传给 process_steam_profile"""
    url = f"https://steamcommunity.com/search/users/#text={search_user_name}"
    driver = create_driver()
    try:
        driver.get(url)
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # ❌ 检查是否没有用户
        no_user = soup.select_one(".search_results_error h2")
        if no_user and "没有符合您搜索的用户" in no_user.text:
            yield event.plain_result(f"❌ 没有找到名为 {search_user_name} 的用户。")
            return

        # ✅ 获取第一个用户链接
        first_user_row = soup.select_one(".search_row a")
        if first_user_row and first_user_row.has_attr("href"):
            profile_url = first_user_row["href"]
            yield event.plain_result(f"🔍 正在解析符合条件的第一位用户...\n🌐 链接: {profile_url}")
            await process_steam_profile(event, profile_url)
        else:
            yield event.plain_result("⚠️ 未能解析出用户链接。")

    except Exception as e:
        yield event.plain_result(f"❌ 搜索用户失败: {e}")
    finally:
        driver.quit()


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

    @filter.command("sss")
    async def search_steam_store(self, event: AstrMessageEvent):
        """搜索 Steam 商店游戏信息\n用法：/sss 游戏名"""
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("请输入要搜索的游戏名称，例如：/sss 犹格索托斯的庭院")
            return

        search_game_name = args[1]
        async for response in steam_store_search(search_game_name, event):
            yield response

    @filter.command("ssu")
    async def search_steam_user(self, event: AstrMessageEvent):
        """搜索 Steam 用户信息\n用法：/ssu 用户名"""
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("请输入要搜索的 Steam 用户名，例如：/ssu m4a1_death-Dawn")
            return

        search_user_name = args[1]
        async for result in steam_user_search(search_user_name, event):
            yield result
