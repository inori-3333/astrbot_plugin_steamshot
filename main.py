import os
import time
import tempfile
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# **🔹 依赖导入**
import re
import asyncio
from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)
from jinja2 import Template
from .steam_login import apply_cookies_to_driver, set_data_dir as set_steam_login_data_dir
from .browser_runtime import create_chrome_webdriver, set_data_dir as set_browser_runtime_data_dir
from .steam_api import extract_appid_from_url, get_friend_status_lines, get_profile_api_info, get_store_api_info
from .price_api import get_price_summary

# 用户状态跟踪
USER_STATES = {}
INFO_CACHE = {}
USER_STATE_TTL_SECONDS = 120
MAX_LINKS_PER_MESSAGE = 3
STEAM_PAGE_LOAD_TIMEOUT_SECONDS = 25
STEAM_PARTIAL_PAGE_WAIT_SECONDS = 8

# **🔹 Steam 链接匹配正则**
STEAM_URL_PATTERN = r"https?://store\.steampowered\.com/app/(\d+)(?:/[\w\-%]+)?/?(?:\?[^\s]*)?"
STEAM_PROFILE_URL_PATTERN = r"https?://steamcommunity\.com/(profiles/\d{17}|id/[A-Za-z0-9\-_]+)(?:/)?(?:\?[^\s]*)?"
STEAM_WORKSHOP_URL_PATTERN = r"https?://steamcommunity\.com/(sharedfiles/filedetails|workshop/filedetails)/\?id=(\d+)(?:[^\s]*)?"

# **🔹 截图路径**
DEFAULT_PLUGIN_DATA_DIR = "./data/plugins/astrbot_plugin_steamshot"
PLUGIN_DATA_DIR = os.getenv("STEAMSHOT_DATA_DIR", DEFAULT_PLUGIN_DATA_DIR)
SCREENSHOT_DIR = os.path.join(PLUGIN_DATA_DIR, "screenshots")

DEFAULT_PROCESS_OPTIONS = {
    "return_screenshot": True,
    "screenshot_width": 1440,
    "screenshot_max_height": 20000,
    "screenshot_scroll_limit": 15,
    "summary_detail_level": "standard",
    "use_steam_api": True,
    "cache_ttl_store": 1800,
    "cache_ttl_profile": 300,
    "cache_ttl_workshop": 600,
    "steam_api_key": "",
    "show_friends": False,
    "friends_status_limit": 10,
    "itad_api_key": "",
    "show_price_history": False,
    "itad_country": "CN",
    "price_current_limit": 3,
    "price_history_limit": 5,
}


def _get_process_options(options=None):
    merged = DEFAULT_PROCESS_OPTIONS.copy()
    if options:
        merged.update(options)

    merged["return_screenshot"] = bool(merged.get("return_screenshot", True))
    merged["screenshot_width"] = _clamp_int(merged.get("screenshot_width"), 800, 2560, 1440)
    merged["screenshot_max_height"] = _clamp_int(merged.get("screenshot_max_height"), 4000, 50000, 20000)
    merged["screenshot_scroll_limit"] = _clamp_int(merged.get("screenshot_scroll_limit"), 1, 50, 15)
    merged["cache_ttl_store"] = _clamp_int(merged.get("cache_ttl_store"), 0, 86400, 1800)
    merged["cache_ttl_profile"] = _clamp_int(merged.get("cache_ttl_profile"), 0, 86400, 300)
    merged["cache_ttl_workshop"] = _clamp_int(merged.get("cache_ttl_workshop"), 0, 86400, 600)
    merged["use_steam_api"] = bool(merged.get("use_steam_api", True))
    merged["steam_api_key"] = str(merged.get("steam_api_key") or "").strip()
    merged["show_friends"] = bool(merged.get("show_friends", False))
    merged["friends_status_limit"] = _clamp_int(merged.get("friends_status_limit"), 1, 50, 10)
    merged["itad_api_key"] = str(merged.get("itad_api_key") or "").strip()
    merged["show_price_history"] = bool(merged.get("show_price_history", False))
    merged["itad_country"] = str(merged.get("itad_country") or "CN").strip().upper()[:2] or "CN"
    merged["price_current_limit"] = _clamp_int(merged.get("price_current_limit"), 1, 10, 3)
    merged["price_history_limit"] = _clamp_int(merged.get("price_history_limit"), 1, 10, 5)
    if merged.get("summary_detail_level") not in {"simple", "standard", "detailed"}:
        merged["summary_detail_level"] = "standard"
    return merged


def _clamp_int(value, min_value, max_value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def set_data_dir(data_dir):
    """运行时注入插件数据目录，统一截图、认证与运行时缓存路径。"""
    if not data_dir:
        return

    global PLUGIN_DATA_DIR, SCREENSHOT_DIR
    PLUGIN_DATA_DIR = os.path.abspath(os.fspath(data_dir))
    SCREENSHOT_DIR = os.path.join(PLUGIN_DATA_DIR, "screenshots")
    set_steam_login_data_dir(PLUGIN_DATA_DIR)
    set_browser_runtime_data_dir(PLUGIN_DATA_DIR)


def _coerce_data_dir(value):
    if not value:
        return None
    try:
        return os.fspath(value)
    except TypeError:
        return str(value)


def resolve_plugin_data_dir(context=None):
    env_data_dir = os.getenv("STEAMSHOT_DATA_DIR", "").strip()
    if env_data_dir:
        return env_data_dir

    try:
        from astrbot.api.star import StarTools  # pylint: disable=import-outside-toplevel

        getter = getattr(StarTools, "get_data_dir", None)
        if callable(getter):
            for args in (
                ("astrbot_plugin_steamshot",),
                (context,),
                (context, "astrbot_plugin_steamshot"),
                (),
            ):
                try:
                    data_dir = _coerce_data_dir(getter(*args))
                except Exception:
                    continue
                if data_dir:
                    return data_dir
    except Exception:
        pass

    context_candidates = (
        "get_data_dir",
        "get_plugin_data_dir",
        "get_data_path",
        "data_dir",
        "plugin_data_dir",
    )
    for name in context_candidates:
        attr = getattr(context, name, None)
        if not attr:
            continue
        try:
            value = attr() if callable(attr) else attr
        except Exception:
            continue
        data_dir = _coerce_data_dir(value)
        if data_dir:
            return data_dir

    return DEFAULT_PLUGIN_DATA_DIR


def with_lang(url, lang="schinese"):
    """为 Steam URL 追加/覆盖中文语言参数，降低页面文案变化导致的解析失败。"""
    parsed = urlparse(url)
    query_pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "l"]
    query_pairs.append(("l", lang))
    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def normalize_store_url(match):
    return with_lang(f"https://store.steampowered.com/app/{match.group(1)}")


def normalize_profile_url(match):
    return with_lang(f"https://steamcommunity.com/{match.group(1).rstrip('/')}/")


def normalize_workshop_url(match):
    return with_lang(f"https://steamcommunity.com/sharedfiles/filedetails/?id={match.group(2)}")


def make_search_url(base_url, params=None, fragment_params=None):
    params = params or {}
    fragment_params = fragment_params or {}
    parsed = urlparse(base_url)
    query = urlencode(params)
    fragment = urlencode(fragment_params)
    return with_lang(urlunparse(parsed._replace(query=query, fragment=fragment)))


def make_temp_screenshot_path(kind):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"{kind}_",
        suffix=".png",
        dir=SCREENSHOT_DIR,
        delete=False,
    )
    temp_file.close()
    return temp_file.name


def safe_remove(path):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logger.warning(f"清理临时截图失败: {path}, {e}")


def format_error_for_user(error):
    message = str(error).strip()
    if isinstance(error, TimeoutException) or "Timed out receiving message from renderer" in message:
        return "页面加载超时，已自动重试但仍未完成；请稍后重试，或临时关闭截图后再试。"

    if "Stacktrace:" in message:
        message = message.split("Stacktrace:", 1)[0].strip()

    if not message:
        return error.__class__.__name__
    return message[:300]


def get_session_key(event):
    origin_attr = getattr(event, "unified_msg_origin", None)
    try:
        origin = origin_attr() if callable(origin_attr) else origin_attr
    except Exception:
        origin = None
    if origin:
        return str(origin)

    sender_id = event.get_sender_id()
    group_id = None
    get_group_id = getattr(event, "get_group_id", None)
    if callable(get_group_id):
        try:
            group_id = get_group_id()
        except Exception:
            group_id = None
    return f"{group_id or 'private'}:{sender_id}"


def cleanup_user_states():
    now = time.time()
    expired_keys = [
        key for key, state in USER_STATES.items()
        if now - state.get("timestamp", now) > USER_STATE_TTL_SECONDS
    ]
    for key in expired_keys:
        USER_STATES.pop(key, None)


def get_cached_info(kind, key, ttl_seconds):
    if not key or ttl_seconds <= 0:
        return None

    cache_key = (kind, key)
    record = INFO_CACHE.get(cache_key)
    if not record:
        return None

    if time.time() - record["timestamp"] > ttl_seconds:
        INFO_CACHE.pop(cache_key, None)
        return None

    data = record["data"]
    if isinstance(data, dict):
        return data.copy()
    if isinstance(data, list):
        return list(data)
    return data


def set_cached_info(kind, key, data):
    if key and data:
        INFO_CACHE[(kind, key)] = {
            "timestamp": time.time(),
            "data": data.copy() if isinstance(data, dict) else list(data) if isinstance(data, list) else data,
        }


def clear_info_cache():
    INFO_CACHE.clear()


def cleanup_temp_screenshots():
    if not os.path.isdir(SCREENSHOT_DIR):
        return
    for filename in os.listdir(SCREENSHOT_DIR):
        if filename.endswith(".png"):
            safe_remove(os.path.join(SCREENSHOT_DIR, filename))


def is_next_page_command(message):
    return message.strip().lower() in {"n", "next", "下一页", "下页"}


def is_prev_page_command(message):
    return message.strip().lower() in {"p", "prev", "previous", "上一页", "上页"}


def filter_info_by_detail(info, kind, options=None):
    level = _get_process_options(options)["summary_detail_level"]
    if level != "simple":
        return info

    simple_keys = {
        "store": ["🎮 游戏名称", "⭐ 评分", "💰 价格", "💹 史低", "🛒 当前低价"],
        "workshop": ["🎮 所属游戏", "🛠️ 模组名称", "👤 作者", "⭐ 评分"],
    }
    keys = simple_keys.get(kind)
    if not keys:
        return info
    return {key: info[key] for key in keys if key in info}


def filter_profile_by_detail(lines, options=None):
    level = _get_process_options(options)["summary_detail_level"]
    if level != "simple":
        return lines

    keep_prefixes = (
        "steam id",
        "🚫 封禁纪录",
        "此个人资料是私密的",
        "steam等级",
        "地区",
        "当前状态",
        "游戏数",
        "好友数",
    )
    return [line for line in lines if line.startswith(keep_prefixes)]


async def enrich_store_with_price_history(game_info, appid, options=None):
    options = _get_process_options(options)
    if not game_info or not appid or not options["show_price_history"]:
        return game_info

    enriched_info = game_info.copy()
    if "💹 史低" in enriched_info and "🛒 当前低价" in enriched_info:
        return enriched_info

    if not options["itad_api_key"]:
        enriched_info["💹 史低"] = "未配置 ITAD API Key，已跳过"
        return enriched_info

    try:
        price_summary = await get_price_summary(
            appid,
            options["itad_api_key"],
            options["itad_country"],
            options["price_current_limit"],
            options["price_history_limit"],
        )
        for key, value in price_summary.items():
            if key == "📉 近期价格" and options["summary_detail_level"] != "detailed":
                continue
            if value and key not in enriched_info:
                enriched_info[key] = value
    except Exception as e:
        logger.warning(f"ITAD 获取价格摘要失败: {appid}, {e}")

    return enriched_info


async def enrich_profile_with_friends(profile_lines, profile_url, options=None):
    options = _get_process_options(options)
    if not options["show_friends"]:
        return profile_lines

    if not options["steam_api_key"]:
        return profile_lines + ["👥 好友在线概览: 未配置 Steam Web API Key，已跳过"]

    try:
        friend_lines = await get_friend_status_lines(
            profile_url,
            options["steam_api_key"],
            options["friends_status_limit"],
        )
    except Exception as e:
        logger.warning(f"Steam API 获取好友状态失败: {profile_url}, {e}")
        friend_lines = ["👥 好友在线概览: 无法获取（好友列表可能未公开或 API Key 无效）"]

    return profile_lines + friend_lines


def create_driver(apply_login=True, url=None):
    """ 创建 Selenium WebDriver，支持可选的Steam登录 """
    driver = create_chrome_webdriver()
    
    # 如果启用了登录并且传入了apply_login参数，应用Steam登录cookies
    if apply_login:
        # 传入URL参数，让函数根据URL自动选择应用哪个域的cookies
        login_applied = apply_cookies_to_driver(driver, url)
        if login_applied:
            logger.info("已应用 Steam 登录信息")
    
    return driver

def bypass_steam_age_check(driver):
    """
    自动处理 Steam 年龄验证页面和敏感内容验证页面。
    如果当前页面是验证页，自动填写信息并跳转。
    """
    try:
        # 检查当前URL是否包含agecheck关键字
        if "agecheck" not in driver.current_url:
            return  # 不是验证页面，直接返回

        # 检查页面内容判断是哪种验证类型
        # 方法1：检查是否存在年龄下拉框(常规年龄验证)
        is_age_verification = False
        is_content_verification = False
        
        try:
            # 先尝试检测常规年龄验证页面特有元素
            if driver.find_elements(By.ID, "ageYear"):
                is_age_verification = True
                logger.info("检测到 Steam 年龄验证页面，正在自动跳过")
            # 检测敏感内容验证页面特有元素
            elif driver.find_elements(By.ID, "app_agegate") and driver.find_elements(By.ID, "view_product_page_btn"):
                is_content_verification = True
                logger.info("检测到 Steam 敏感内容验证页面，正在自动跳过")
        except Exception as e:
            # 如果上述检测失败，尝试通过页面源码判断
            logger.warning(f"检测 Steam 验证页面元素失败，改用源码判断: {e}")
            page_source = driver.page_source
            if "ageYear" in page_source:
                is_age_verification = True
                logger.info("检测到 Steam 年龄验证页面，正在自动跳过")
            elif "app_agegate" in page_source and "view_product_page_btn" in page_source:
                is_content_verification = True
                logger.info("检测到 Steam 敏感内容验证页面，正在自动跳过")
        
        # 保存跳转前的 URL
        before_url = driver.current_url
        
        # 处理常规年龄验证
        if is_age_verification:
            # 等待出生日期下拉框出现
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "ageYear")))
            
            # 选择出生日期
            Select(driver.find_element(By.ID, "ageYear")).select_by_visible_text("2000")
            
            # 尝试执行 JS 跳转函数
            driver.execute_script("ViewProductPage()")
        
        # 处理敏感内容验证
        elif is_content_verification:
            # 尝试直接点击"查看页面"按钮
            try:
                view_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "view_product_page_btn"))
                )
                view_btn.click()
            except Exception as e:
                # 如果按钮点击失败，尝试执行JS函数
                logger.warning(f"点击敏感内容验证按钮失败，改用 JS 跳转: {e}")
                driver.execute_script("ViewProductPage()")
        
        else:
            # 如果无法确定验证类型，但确实在agecheck页面，尝试通用方法
            logger.warning("未能识别验证类型，尝试通用方法跳转")
            try:
                # 尝试执行 JS 跳转函数 (两种验证页面都使用这个函数)
                driver.execute_script("ViewProductPage()")
            except Exception as e:
                # 尝试点击任何可能的"查看页面"按钮
                logger.warning(f"JS 跳转 Steam 验证页面失败，尝试点击按钮: {e}")
                buttons = driver.find_elements(By.CSS_SELECTOR, ".btnv6_blue_hoverfade")
                for button in buttons:
                    if "查看" in button.text:
                        button.click()
                        break
        
        # 等待 URL 发生变化，表示跳转成功
        WebDriverWait(driver, 10).until(EC.url_changes(before_url))
        logger.info("已跳转至游戏页面")

        # 等待游戏页面加载完成 (寻找游戏名称元素)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "apphub_AppName"))
        )

    except Exception as e:
        logger.warning(f"Steam 验证页面跳过失败: {e}")

def get_steam_page_ready_selectors(url):
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()

    if "steamcommunity.com" in netloc and (
        "/sharedfiles/filedetails" in path or "/workshop/filedetails" in path
    ):
        return [
            ".workshopItemTitle",
            ".workshopItemDetailsHeader",
            ".workshopItemDescription",
            ".breadcrumbs",
        ]

    if "steamcommunity.com" in netloc:
        return [
            ".actual_persona_name",
            ".profile_header",
            ".profile_private_info",
            ".responsive_status_info",
        ]

    if "store.steampowered.com" in netloc:
        return [
            ".apphub_AppName",
            "#appHubAppName_responsive",
            ".game_area_purchase_game",
            ".game_page_background",
        ]

    return ["body"]


def wait_for_usable_page(driver, selectors, timeout=STEAM_PARTIAL_PAGE_WAIT_SECONDS):
    try:
        WebDriverWait(driver, timeout).until(
            lambda current_driver: any(
                current_driver.find_elements(By.CSS_SELECTOR, selector)
                for selector in selectors
            )
        )
        return True
    except Exception as e:
        logger.warning(f"等待 Steam 页面关键元素超时: {selectors}, {e}")
        return False


def stop_page_loading(driver):
    try:
        driver.execute_script("window.stop();")
        return True
    except Exception as e:
        logger.warning(f"停止 Steam 页面加载失败: {e}")
        return False


def navigate_with_retries(driver, url):
    normalized_url = with_lang(url)
    ready_selectors = get_steam_page_ready_selectors(normalized_url)
    driver.set_page_load_timeout(STEAM_PAGE_LOAD_TIMEOUT_SECONDS)
    last_error = None
    for attempt in range(3):
        try:
            driver.get(normalized_url)
            bypass_steam_age_check(driver)
            if wait_for_usable_page(driver, ready_selectors):
                return
            last_error = TimeoutException(f"Steam 页面关键元素未出现: {normalized_url}")
        except Exception as e:
            last_error = e
            logger.warning(f"第 {attempt + 1} 次加载 Steam 页面失败: {e}")
            if isinstance(e, TimeoutException):
                stop_page_loading(driver)
                if wait_for_usable_page(driver, ready_selectors):
                    logger.warning("Steam 页面完整加载超时，已使用当前已渲染内容继续解析")
                    return

            if attempt == 2:
                raise last_error
            try:
                driver.refresh()
            except Exception as refresh_error:
                logger.warning(f"刷新 Steam 页面失败: {refresh_error}")

    if last_error:
        raise last_error


def scroll_for_lazy_content(driver, options=None):
    options = _get_process_options(options)
    time.sleep(2)

    last_height = driver.execute_script("return document.body.scrollHeight") or 0
    max_height = options["screenshot_max_height"]
    for _ in range(options["screenshot_scroll_limit"]):
        if last_height >= max_height:
            break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight") or last_height
        if new_height == last_height:
            break
        last_height = new_height

    return min(int(last_height), max_height)


def save_driver_screenshot(driver, save_path, options=None):
    if not save_path:
        return False

    options = _get_process_options(options)
    page_height = scroll_for_lazy_content(driver, options)
    driver.set_window_size(options["screenshot_width"], page_height)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    driver.save_screenshot(save_path)
    logger.info(f"截图已保存: {save_path}")
    return True


async def render_and_parse(url, save_path, parser_fn, options=None):
    """单 driver 完成页面加载、可选截图与 BeautifulSoup 解析。"""
    options = _get_process_options(options)

    def run():
        driver = None
        screenshot_ok = False
        try:
            driver = create_driver(apply_login=True, url=url)
            navigate_with_retries(driver, url)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            parsed_info = parser_fn(soup)

            if options["return_screenshot"] and save_path:
                try:
                    screenshot_ok = save_driver_screenshot(driver, save_path, options)
                except Exception as e:
                    logger.warning(f"Steam 页面截图失败，仅返回文本摘要: {url}, {e}")
            else:
                time.sleep(2)

            return parsed_info, screenshot_ok
        except Exception as e:
            logger.error(f"解析 Steam 页面失败: {url}, {e}")
            raise
        finally:
            if driver:
                driver.quit()

    return await asyncio.to_thread(run)


async def capture_screenshot(url, save_path, options=None):
    """截取网页完整截图（支持懒加载内容，带滚动/高度上限）。"""
    def run():
        driver = None
        try:
            driver = create_driver(apply_login=True, url=url)
            navigate_with_retries(driver, url)
            return save_driver_screenshot(driver, save_path, options)

        except Exception as e:
            logger.error(f"截图错误: {e}")
            return False

        finally:
            if driver:
                driver.quit()

    return await asyncio.to_thread(run)

def parse_steam_workshop_soup(soup):
    """解析 Steam 创意工坊页面信息。"""
    info = {}

    # 0. 获取游戏名称和链接
    breadcrumbs = soup.find("div", class_="breadcrumbs")
    if breadcrumbs:
        game_link = breadcrumbs.find("a")
        if game_link and game_link.get("href"):
            info["🎮 所属游戏"] = game_link.text.strip()
            game_href = game_link["href"]
            if not game_href.startswith("http"):
                game_href = "https://steamcommunity.com" + game_href
            info["🔗 游戏链接"] = with_lang(game_href)

    # 1. 获取模组名称
    title = soup.find("div", class_="workshopItemTitle")
    info["🛠️ 模组名称"] = title.text.strip() if title else "未知"

    # 2. 获取作者信息和真实主页链接
    creator_block = soup.find("div", class_="creatorsBlock")
    if creator_block:
        author_name = next((text for text in creator_block.stripped_strings if text.strip()), "未知")
        author_link = creator_block.find("a")
        if author_link and author_link.get("href"):
            info["👤 作者"] = author_name.split('\n')[0].strip()
            author_href = author_link["href"]
            if not author_href.startswith("http"):
                author_href = "https://steamcommunity.com" + author_href
            info["🔗 作者主页"] = with_lang(author_href)

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


async def get_steam_workshop_info(url):
    """解析 Steam 创意工坊页面信息。"""
    info, _ = await render_and_parse(
        url,
        None,
        parse_steam_workshop_soup,
        {**DEFAULT_PROCESS_OPTIONS, "return_screenshot": False},
    )
    return info


async def process_steam_workshop(event, workshop_url, options=None):
    """ 处理 Steam 创意工坊链接 """
    result = MessageChain()
    options = _get_process_options(options)
    screenshot_path = make_temp_screenshot_path("workshop") if options["return_screenshot"] else None
    cache_key = with_lang(workshop_url)

    try:
        workshop_info = get_cached_info("workshop", cache_key, options["cache_ttl_workshop"])
        screenshot_ok = False
        if workshop_info is None:
            workshop_info, screenshot_ok = await render_and_parse(
                workshop_url,
                screenshot_path,
                parse_steam_workshop_soup,
                options,
            )
            set_cached_info("workshop", cache_key, workshop_info)
        elif options["return_screenshot"]:
            logger.info(f"命中创意工坊信息缓存: {cache_key}")
            screenshot_ok = await capture_screenshot(workshop_url, screenshot_path, options)

        workshop_info = filter_info_by_detail(workshop_info, "workshop", options)

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
        else:
            result.chain.append(Plain("⚠️ 无法获取创意工坊页面信息。"))

        if screenshot_ok and screenshot_path and os.path.exists(screenshot_path):
            result.chain.append(Image.fromFileSystem(screenshot_path))

        await event.send(result)
    except Exception as e:
        logger.error(f"处理 Steam 创意工坊链接失败: {e}")
        result.chain.append(Plain(f"❌ 创意工坊解析失败: {format_error_for_user(e)}"))
        await event.send(result)
    finally:
        safe_remove(screenshot_path)

def parse_steam_store_soup(soup):
    """解析 Steam 商店页面信息。"""
    from bs4.element import Tag

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
            publisher = next_div.get_text(strip=True).replace("发行商:", "").strip()

    tags = soup.select("a.app_tag")
    tags = "，".join([tag.text.strip() for tag in tags[:5]]) if tags else "未知"

    description_div = soup.find("div", class_="game_description_snippet")
    description = description_div.text.strip() if description_div else "暂无简介"

    review_summary = soup.find("span", class_="game_review_summary")
    review_summary = review_summary.text.strip() if review_summary else "暂无评分"

    price_items = []

    is_preorder = False
    preorder_date = None
    coming_soon_div = soup.find("div", class_="game_area_comingsoon")
    if coming_soon_div:
        is_preorder = True
        coming_soon_h1 = coming_soon_div.find("h1")
        if coming_soon_h1:
            preorder_date = coming_soon_h1.text.strip()
        logger.info(f"检测到预购游戏: {preorder_date}")

    is_free_game = False
    free_tag = soup.find(
        "div",
        class_="game_purchase_price",
        string=lambda s: s and ("免费" in s or "free" in s.lower()),
    )
    if free_tag:
        is_free_game = True
        logger.info("检测到免费游戏")

    try:
        if is_free_game:
            price_items.append("免费游戏")
        elif is_preorder:
            logger.info("尝试提取预购游戏的价格信息")

            purchase_area = soup.find("div", id="game_area_purchase")
            if purchase_area:
                logger.info("找到预购游戏购买区域")
                preorder_containers = []

                for container in purchase_area.select(".game_area_purchase_game_wrapper"):
                    preorder_containers.append(container)

                if not preorder_containers:
                    for container in purchase_area.select(".game_area_purchase_game"):
                        if not any(c.find(container) for c in preorder_containers):
                            preorder_containers.append(container)

                if not preorder_containers:
                    for container in purchase_area.select(".game_purchase_options_editions_container > div"):
                        if container.select_one("h2.title, h1.title") is not None:
                            preorder_containers.append(container)

                unique_titles = set()
                filtered_containers = []
                for container in preorder_containers:
                    title_elem = container.select_one("h1.title, h2.title")
                    if title_elem:
                        title = title_elem.text.strip()
                        if title not in unique_titles:
                            unique_titles.add(title)
                            filtered_containers.append(container)
                preorder_containers = filtered_containers

                logger.info(f"找到 {len(preorder_containers)} 个唯一预购选项容器")

                if not preorder_containers:
                    logger.warning("没有找到标准预购容器，尝试直接分析")
                    price_elems = purchase_area.select(".game_purchase_price, .discount_final_price")
                    for price_elem in price_elems:
                        price_text = price_elem.text.strip()
                        if price_text:
                            preorder_title = f"预购 {game_name}"
                            if preorder_date:
                                preorder_title += f" ({preorder_date})"

                            parent = price_elem.parent
                            discount_pct = None
                            if parent:
                                discount_elem = parent.select_one(".discount_pct")
                                if discount_elem:
                                    discount_pct = discount_elem.text.strip()

                            if discount_pct:
                                formatted_price = f"{preorder_title}   {discount_pct}   {price_text}"
                            else:
                                formatted_price = f"{preorder_title}   {price_text}"

                            logger.info(f"预购价格: {formatted_price}")
                            price_items.append(formatted_price)
                else:
                    for i, container in enumerate(preorder_containers):
                        try:
                            title_elem = container.select_one("h1.title, h2.title, .game_purchase_options_editions_header_title")
                            title = title_elem.text.strip() if title_elem else f"预购 {game_name}"

                            if "预购" not in title:
                                title = f"预购 {title}"

                            if preorder_date and preorder_date not in title:
                                title += f" ({preorder_date})"

                            price_elem = container.select_one(".game_purchase_price, .discount_final_price")
                            if price_elem:
                                price_text = price_elem.text.strip()

                                discount_elem = container.select_one(".discount_pct")
                                if discount_elem:
                                    discount_text = discount_elem.text.strip()
                                    formatted_price = f"{title}   {discount_text}   {price_text}"
                                else:
                                    formatted_price = f"{title}   {price_text}"

                                logger.info(f"预购价格选项 {i + 1}: {formatted_price}")
                                price_items.append(formatted_price)
                            else:
                                price_items.append(f"{title}   价格未知")
                        except Exception as e:
                            logger.warning(f"处理预购选项 {i + 1} 时出错: {e}")

                if not price_items:
                    preorder_info = f"预购 {game_name}"
                    if preorder_date:
                        preorder_info += f" ({preorder_date})"
                    price_items.append(f"{preorder_info}   价格未知")
            else:
                preorder_info = f"预购 {game_name}"
                if preorder_date:
                    preorder_info += f" ({preorder_date})"
                price_items.append(preorder_info)
        else:
            purchase_area = soup.find("div", id="game_area_purchase")
            if purchase_area:
                logger.info("找到游戏购买区域")
                purchase_wrappers = []

                for child in purchase_area.children:
                    if not isinstance(child, Tag):
                        continue

                    if child.get("id") == "gameAreaDLCSection":
                        logger.info("找到DLC部分，停止收集购买选项")
                        break

                    if "game_area_purchase_game_wrapper" in child.get("class", []):
                        purchase_wrappers.append(child)

                logger.info(f"找到 {len(purchase_wrappers)} 个购买选项")

                for i, wrapper in enumerate(purchase_wrappers):
                    try:
                        if wrapper.find("div", class_="game_purchase_sub_dropdown"):
                            logger.info(f"跳过第 {i + 1} 个购买选项，因为它是下拉框")
                            continue

                        if "dynamic_bundle_description" in wrapper.get("class", []):
                            logger.info(f"第 {i + 1} 个购买选项是动态捆绑包")

                            bundle_title_elem = wrapper.find("h2", class_="title")
                            if not bundle_title_elem:
                                logger.warning(f"第 {i + 1} 个捆绑包没有找到标题元素")
                                continue

                            bundle_title = bundle_title_elem.get_text(strip=True)
                            if bundle_title.startswith("购买 "):
                                bundle_title = bundle_title[3:]
                            bundle_title = bundle_title.replace("(?)", "").strip()

                            logger.info(f"捆绑包标题: {bundle_title}")

                            collection_complete = wrapper.find("span", class_="collectionComplete")
                            if collection_complete:
                                logger.info(f"捆绑包 \"{bundle_title}\" 已完成合集")
                                price_items.append(f"{bundle_title}   已完成合集")
                                continue

                            discount_block = wrapper.find("div", class_="discount_block")
                            if discount_block:
                                discount_pct = discount_block.find("div", class_="bundle_base_discount")
                                final_price = discount_block.find("div", class_="discount_final_price")

                                if discount_pct and final_price:
                                    discount_text = discount_pct.text.strip()
                                    price_text = final_price.text.strip()
                                    if "您的价格：" in price_text:
                                        price_parts = price_text.split("您的价格：")
                                        price_text = price_parts[-1].strip()

                                    formatted_price = f"{bundle_title}   {discount_text}   {price_text}"
                                    logger.info(f"捆绑包价格: {formatted_price}")
                                    price_items.append(formatted_price)
                                elif final_price:
                                    price_text = final_price.text.strip()
                                    if "您的价格：" in price_text:
                                        price_parts = price_text.split("您的价格：")
                                        price_text = price_parts[-1].strip()

                                    formatted_price = f"{bundle_title}   {price_text}"
                                    logger.info(f"捆绑包价格: {formatted_price}")
                                    price_items.append(formatted_price)

                            continue

                        logger.info(f"第 {i + 1} 个购买选项是普通游戏")

                        game_purchase = wrapper.find("div", class_="game_area_purchase_game")
                        if not game_purchase:
                            logger.warning(f"第 {i + 1} 个购买选项没有找到game_area_purchase_game元素")
                            continue

                        title_elem = game_purchase.find("h2", class_="title")
                        if not title_elem:
                            logger.warning(f"第 {i + 1} 个购买选项没有找到标题元素")
                            continue

                        title = title_elem.text.strip()
                        if title.startswith("购买 "):
                            title = title[3:]

                        logger.info(f"游戏标题: {title}")

                        in_library = game_purchase.find("div", class_="package_in_library_flag")
                        if in_library:
                            logger.info(f"游戏 \"{title}\" 已在库中")
                            price_items.append(f"{title}   在库中")
                            continue

                        discount_block = game_purchase.find("div", class_="discount_block")
                        regular_price = game_purchase.find("div", class_="game_purchase_price")

                        if discount_block:
                            discount_pct = discount_block.find("div", class_="discount_pct")
                            final_price = discount_block.find("div", class_="discount_final_price")

                            if discount_pct and final_price:
                                price_text = f"{title}   {discount_pct.text.strip()}   {final_price.text.strip()}"
                                logger.info(f"折扣价格: {price_text}")
                                price_items.append(price_text)
                            elif final_price:
                                price_text = f"{title}   {final_price.text.strip()}"
                                logger.info(f"最终价格: {price_text}")
                                price_items.append(price_text)
                        elif regular_price:
                            price_text = f"{title}   {regular_price.text.strip()}"
                            logger.info(f"常规价格: {price_text}")
                            price_items.append(price_text)
                        else:
                            logger.warning(f"游戏 \"{title}\" 没有找到价格信息")
                            price_items.append(f"{title}   价格未知")
                    except Exception as e:
                        logger.warning(f"处理第 {i + 1} 个购买选项时出错: {e}")
                        continue
            else:
                logger.warning("没有找到游戏购买区域")
    except Exception as e:
        logger.error(f"解析价格信息时出错: {e}")

    price_text = "\n".join(price_items) if price_items else "暂无售价"

    return {
        "🎮 游戏名称": game_name,
        "📅 发行时间": release_date,
        "🏗 开发商": developers,
        "🏛 发行商": publisher,
        "🎭 游戏类别": tags,
        "📜 简介": description,
        "⭐ 评分": review_summary,
        "💰 价格": f"\n{price_text}",
    }


async def get_steam_page_info(url, options=None):
    """解析 Steam 商店页面信息。"""
    options = _get_process_options(options)
    appid = extract_appid_from_url(url)
    if options["use_steam_api"] and appid:
        try:
            info = await get_store_api_info(appid)
            return await enrich_store_with_price_history(info, appid, options)
        except Exception as e:
            logger.warning(f"Steam API 获取商店信息失败，回退页面解析: {appid}, {e}")

    info, _ = await render_and_parse(
        url,
        None,
        parse_steam_store_soup,
        {**options, "return_screenshot": False},
    )
    return await enrich_store_with_price_history(info, appid, options)

async def process_steam_store(event, steam_url, options=None):
    """ 处理 Steam 商店信息 """
    result = MessageChain()
    options = _get_process_options(options)
    screenshot_path = make_temp_screenshot_path("store") if options["return_screenshot"] else None
    appid = extract_appid_from_url(steam_url)
    cache_key = appid or with_lang(steam_url)

    try:
        game_info = get_cached_info("store", cache_key, options["cache_ttl_store"])
        screenshot_ok = False

        if game_info is None:
            if options["use_steam_api"] and appid:
                try:
                    game_info = await get_store_api_info(appid)
                    logger.info(f"已通过 Steam API 获取商店信息: {appid}")
                except Exception as e:
                    logger.warning(f"Steam API 获取商店信息失败，回退页面解析: {appid}, {e}")

            if game_info is not None:
                set_cached_info("store", cache_key, game_info)
                if options["return_screenshot"]:
                    screenshot_ok = await capture_screenshot(steam_url, screenshot_path, options)
            else:
                game_info, screenshot_ok = await render_and_parse(
                    steam_url,
                    screenshot_path,
                    parse_steam_store_soup,
                    options,
                )
                set_cached_info("store", cache_key, game_info)
        elif options["return_screenshot"]:
            logger.info(f"命中商店信息缓存: {cache_key}")
            screenshot_ok = await capture_screenshot(steam_url, screenshot_path, options)

        game_info = await enrich_store_with_price_history(game_info, appid, options)
        game_info = filter_info_by_detail(game_info, "store", options)

        if game_info:
            info_text = "\n".join([f"{key}: {value}" for key, value in game_info.items()])
            result.chain.append(Plain(info_text))
        else:
            result.chain.append(Plain("⚠️ 无法获取 Steam 商店页面信息。"))
        
        if screenshot_ok and screenshot_path and os.path.exists(screenshot_path):
            result.chain.append(Image.fromFileSystem(screenshot_path))

        await event.send(result)
    except Exception as e:
        logger.error(f"处理 Steam 商店链接失败: {e}")
        result.chain.append(Plain(f"❌ Steam 商店解析失败: {format_error_for_user(e)}"))
        await event.send(result)
    finally:
        safe_remove(screenshot_path)

def parse_steam_profile_soup(soup):
    """解析 Steam 个人主页信息（支持完整最新动态）。"""
    standard_profile_lines = []
    recent_activity_parsed_lines = []

    try:
        # 1. Steam ID
        name_span = soup.find("span", class_="actual_persona_name")
        if name_span:
            steam_id = name_span.text.strip()
            standard_profile_lines.append(f"steam id: {steam_id}")

        # 1.5 检查封禁状态（如有则立即返回封禁信息）
        ban_section = soup.find("div", class_="profile_ban_status")
        if ban_section:
            ban_records = []
            for div in ban_section.find_all("div", class_="profile_ban"):
                ban_text = div.get_text(strip=True).replace("|信息", "").strip()
                if ban_text:
                    ban_records.append(ban_text)
            ban_status_text = ban_section.get_text(separator="\n", strip=True)
            for line in ban_status_text.split("\n"):
                if "封禁于" in line:
                    ban_records.append(line.strip())
            if ban_records:
                standard_profile_lines.append("🚫 封禁纪录: \n" + "\n".join(ban_records))

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
                            recent_activity_parsed_lines.append("🎮 当前正在游戏")
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
                            recent_activity_parsed_lines.append(ach_str)

        return standard_profile_lines + recent_activity_parsed_lines

    except Exception as e:
        logger.error(f"解析 Steam 个人主页错误: {e}")
        combined_on_error = standard_profile_lines + recent_activity_parsed_lines
        return combined_on_error if combined_on_error else ["⚠️ 无法获取个人主页部分信息。"]


async def get_steam_profile_info(url, options=None):
    """解析 Steam 个人主页信息（支持完整最新动态）。"""
    options = _get_process_options(options)
    if options["use_steam_api"] and options["steam_api_key"]:
        try:
            return await get_profile_api_info(url, options["steam_api_key"])
        except Exception as e:
            logger.warning(f"Steam API 获取个人主页信息失败，回退页面解析: {url}, {e}")

    info, _ = await render_and_parse(
        url,
        None,
        parse_steam_profile_soup,
        {**options, "return_screenshot": False},
    )
    return info


async def process_steam_profile(event, profile_url, options=None):
    """ 处理 Steam 个人主页 """
    result = MessageChain()
    options = _get_process_options(options)
    screenshot_path = make_temp_screenshot_path("profile") if options["return_screenshot"] else None
    cache_key = with_lang(profile_url)

    try:
        profile_info = get_cached_info("profile", cache_key, options["cache_ttl_profile"])
        screenshot_ok = False
        if profile_info is None:
            if options["use_steam_api"] and options["steam_api_key"]:
                try:
                    profile_info = await get_profile_api_info(profile_url, options["steam_api_key"])
                    logger.info(f"已通过 Steam API 获取个人主页信息: {profile_url}")
                except Exception as e:
                    logger.warning(f"Steam API 获取个人主页信息失败，回退页面解析: {profile_url}, {e}")

            if profile_info is not None:
                set_cached_info("profile", cache_key, profile_info)
                if options["return_screenshot"]:
                    screenshot_ok = await capture_screenshot(profile_url, screenshot_path, options)
            else:
                profile_info, screenshot_ok = await render_and_parse(
                    profile_url,
                    screenshot_path,
                    parse_steam_profile_soup,
                    options,
                )
                set_cached_info("profile", cache_key, profile_info)
        elif options["return_screenshot"]:
            logger.info(f"命中个人主页信息缓存: {cache_key}")
            screenshot_ok = await capture_screenshot(profile_url, screenshot_path, options)

        profile_info = filter_profile_by_detail(profile_info, options)
        profile_info = await enrich_profile_with_friends(profile_info, profile_url, options)

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
        else:
            result.chain.append(Plain("⚠️ 无法获取个人主页部分信息。"))

        if screenshot_ok and screenshot_path and os.path.exists(screenshot_path):
            result.chain.append(Image.fromFileSystem(screenshot_path))

        await event.send(result)
    except Exception as e:
        logger.error(f"处理 Steam 个人主页失败: {e}")
        result.chain.append(Plain(f"❌ Steam 个人主页解析失败: {format_error_for_user(e)}"))
        await event.send(result)
    finally:
        safe_remove(screenshot_path)

@register("astrbot_plugin_steamshot", "Inori-3333", "检测 Steam 链接，截图并返回游戏信息", "v1.8.8", "https://github.com/inori-3333/astrbot_plugin_steamshot")
class SteamPlugin(Star):

    # 定义 HTML 模板
    HTML_STORE_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #1b2838;
                color: #c7d5e0;
                padding: 20px;
                max-width: 800px;
                margin: 0 auto;
            }
            .item {
                border-bottom: 1px solid #4c5a6a;
                padding: 15px 0;
                position: relative;
                display: flex;
                flex-direction: column;
            }
            .item-header {
                display: flex;
                align-items: flex-start;
            }
            .cover {
                width: 120px;
                height: 45px;
                margin-right: 15px;
                object-fit: cover;
            }
            .info {
                flex-grow: 1;
            }
            h2 {
                margin: 0 0 5px 0;
                color: #ffffff;
                font-size: 18px;
            }
            .details {
                font-size: 14px;
                margin-top: 5px;
            }
            .price {
                color: #a4d007;
                font-weight: bold;
            }
            .number {
                position: absolute;
                left: -20px;
                top: 15px;
                width: 20px;
                height: 20px;
                background-color: #67c1f5;
                color: #ffffff;
                border-radius: 50%;
                text-align: center;
                line-height: 20px;
                font-size: 12px;
            }
            .separator {
                height: 1px;
                background-color: #4c5a6a;
                margin: 5px 0;
                width: 100%;
            }
            .note {
                text-align: center;
                margin-top: 20px;
                font-style: italic;
                color: #67c1f5;
            }
        </style>
    </head>
    <body>
        <div class="container">
            {% for game in games %}
            <div class="item">
                <div class="number">{{ loop.index }}</div>
                <div class="item-header">
                    {% if game.image_url %}
                    <img class="cover" src="{{ game.image_url }}" alt="{{ game.title }}">
                    {% endif %}
                    <div class="info">
                        <h2>{{ game.title }}</h2>
                        <div class="details">
                            {% if game.release_date %}
                            <div>上架时间: {{ game.release_date }}</div>
                            {% else %}
                            <div>上架时间: 未知</div>
                            {% endif %}
                            {% if game.price %}
                            <div class="price">价格: {{ game.price }}</div>
                            {% else %}
                            <div class="price">价格: 未知</div>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>
            {% endfor %}
            <div class="note">第 {{ page }} 页｜30秒内回复序号；回复 n/下一页 或 p/上一页 翻页；超时默认访问当前页第一项</div>
        </div>
    </body>
    </html>
    """

    HTML_USER_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {
                font-family: Arial, sans-serif;
                background-color: #1b2838;
                color: #c7d5e0;
                padding: 20px;
                max-width: 800px;
                margin: 0 auto;
            }
            .item {
                border-bottom: 1px solid #4c5a6a;
                padding: 15px 0;
                position: relative;
                display: flex;
            }
            .avatar {
                width: 80px;
                height: 80px;
                margin-right: 15px;
                border-radius: 3px;
            }
            .info {
                flex-grow: 1;
            }
            h2 {
                margin: 0 0 5px 0;
                color: #ffffff;
                font-size: 18px;
            }
            .details {
                font-size: 14px;
                margin-top: 5px;
            }
            .number {
                position: absolute;
                left: -20px;
                top: 15px;
                width: 20px;
                height: 20px;
                background-color: #67c1f5;
                color: #ffffff;
                border-radius: 50%;
                text-align: center;
                line-height: 20px;
                font-size: 12px;
            }
            .note {
                text-align: center;
                margin-top: 20px;
                font-style: italic;
                color: #67c1f5;
            }
        </style>
    </head>
    <body>
        <div class="container">
            {% for user in users %}
            <div class="item">
                <div class="number">{{ loop.index }}</div>
                {% if user.avatar_url %}
                <img class="avatar" src="{{ user.avatar_url }}" alt="{{ user.name }}">
                {% endif %}
                <div class="info">
                    <h2>{{ user.name }}</h2>
                    <div class="details">
                        {% if user.location %}
                        <div>(别名/)地区: {{ user.location }}</div>
                        {% endif %}
                        {% if user.custom_url %}
                        <div>自定义URL: {{ user.custom_url }}</div>
                        {% endif %}
                    </div>
                </div>
            </div>
            {% endfor %}
            <div class="note">第 {{ page }} 页｜30秒内回复序号；回复 n/下一页 或 p/上一页 翻页；超时默认访问当前页第一项</div>
        </div>
    </body>
    </html>
    """

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # 初始化配置
        self.config = config or {}
        self.plugin_data_dir = resolve_plugin_data_dir(context)
        set_data_dir(self.plugin_data_dir)
        
        # 从配置中读取Steam登录设置
        self._load_config_values()
        
        # 应用配置
        self._apply_config()

    def _load_config_values(self):
        self.enable_steam_login = self.config.get("enable_steam_login", False)
        self.steam_store_cookies = self.config.get("steam_store_cookies", "")
        self.steam_community_cookies = self.config.get("steam_community_cookies", "")
        self.steam_api_key = self.config.get("steam_api_key", "")
        self.itad_api_key = self.config.get("itad_api_key", "")
        self.render_options = _get_process_options({
            "return_screenshot": self.config.get("return_screenshot", True),
            "screenshot_width": self.config.get("screenshot_width", 1440),
            "screenshot_max_height": self.config.get("screenshot_max_height", 20000),
            "screenshot_scroll_limit": self.config.get("screenshot_scroll_limit", 15),
            "summary_detail_level": self.config.get("summary_detail_level", "standard"),
            "use_steam_api": self.config.get("use_steam_api", True),
            "cache_ttl_store": self.config.get("cache_ttl_store", 1800),
            "cache_ttl_profile": self.config.get("cache_ttl_profile", 300),
            "cache_ttl_workshop": self.config.get("cache_ttl_workshop", 600),
            "steam_api_key": self.steam_api_key,
            "show_friends": self.config.get("show_friends", False),
            "friends_status_limit": self.config.get("friends_status_limit", 10),
            "itad_api_key": self.itad_api_key,
            "show_price_history": self.config.get("show_price_history", False),
            "itad_country": self.config.get("itad_country", "CN"),
            "price_current_limit": self.config.get("price_current_limit", 3),
            "price_history_limit": self.config.get("price_history_limit", 5),
        })
        
    def _apply_config(self):
        """应用配置到插件功能"""
        from .steam_login import enable_steam_login, disable_steam_login, save_steam_cookies
        
        if self.enable_steam_login:
            # 应用Steam商店cookies
            if self.steam_store_cookies:
                save_steam_cookies(self.steam_store_cookies, "store")
                
            # 应用Steam社区cookies
            if self.steam_community_cookies:
                save_steam_cookies(self.steam_community_cookies, "community")
                
            # 启用Steam登录
            enable_steam_login()
        else:
            # 禁用Steam登录
            disable_steam_login()

    @filter.regex(STEAM_URL_PATTERN)
    async def handle_steam_store(self, event: AstrMessageEvent):
        seen = set()
        for match in re.finditer(STEAM_URL_PATTERN, event.message_str):
            steam_url = normalize_store_url(match)
            if steam_url in seen:
                continue
            seen.add(steam_url)
            await process_steam_store(event, steam_url, self.render_options)
            if len(seen) >= MAX_LINKS_PER_MESSAGE:
                break

    @filter.regex(STEAM_PROFILE_URL_PATTERN)
    async def handle_steam_profile(self, event: AstrMessageEvent):
        seen = set()
        for match in re.finditer(STEAM_PROFILE_URL_PATTERN, event.message_str):
            profile_url = normalize_profile_url(match)
            if profile_url in seen:
                continue
            seen.add(profile_url)
            await process_steam_profile(event, profile_url, self.render_options)
            if len(seen) >= MAX_LINKS_PER_MESSAGE:
                break

    @filter.regex(STEAM_WORKSHOP_URL_PATTERN)
    async def handle_steam_workshop(self, event: AstrMessageEvent):
        seen = set()
        for match in re.finditer(STEAM_WORKSHOP_URL_PATTERN, event.message_str):
            workshop_url = normalize_workshop_url(match)
            if workshop_url in seen:
                continue
            seen.add(workshop_url)
            await process_steam_workshop(event, workshop_url, self.render_options)
            if len(seen) >= MAX_LINKS_PER_MESSAGE:
                break

    async def _fetch_store_search_results(self, search_game_name, page):
        """按页抓取 Steam 商店搜索结果。"""
        driver = create_driver(apply_login=True, url="https://store.steampowered.com/")
        try:
            url = make_search_url(
                "https://store.steampowered.com/search/",
                {"term": search_game_name, "ndl": "1", "page": str(page)},
            )
            driver.get(url)
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            no_result_div = soup.select_one("#search_results .search_results_count")
            if no_result_div and "0 个匹配的搜索结果" in no_result_div.text:
                return []

            result_containers = soup.select("#search_resultsRows a")[:10]
            game_results = []

            for i, container in enumerate(result_containers, 1):
                try:
                    game_url = container.get("href", "")
                    if game_url.startswith("/"):
                        game_url = "https://store.steampowered.com" + game_url
                    if game_url:
                        game_url = with_lang(game_url)

                    title_elem = container.select_one(".title")
                    title = title_elem.text.strip() if title_elem else "未知标题"

                    image_elem = container.select_one(".search_capsule img")
                    image_url = image_elem["src"] if image_elem else None

                    release_date = container.select_one(".search_released")
                    release_date = release_date.text.strip() if release_date else "未知"

                    price = "未知"
                    price_container = container.select_one(".search_price_discount_combined")
                    if price_container:
                        if price_container.get("data-price-final") == "0":
                            price = "免费游戏"
                        else:
                            discount_block = price_container.select_one(".discount_block")
                            if discount_block:
                                has_discount = "no_discount" not in discount_block.get("class", [])
                                final_price_elem = discount_block.select_one(".discount_final_price")
                                final_price = final_price_elem.text.strip() if final_price_elem else ""

                                if has_discount:
                                    discount_pct_elem = discount_block.select_one(".discount_pct")
                                    discount_pct = discount_pct_elem.text.strip() if discount_pct_elem else ""
                                    if discount_pct and final_price:
                                        price = f"{discount_pct} {final_price}"
                                    elif final_price:
                                        price = final_price
                                elif final_price_elem:
                                    price = "免费游戏" if "free" in final_price_elem.get("class", []) else final_price

                    game_results.append({
                        "url": game_url,
                        "title": title,
                        "image_url": image_url,
                        "release_date": release_date,
                        "price": price,
                    })
                except Exception as e:
                    logger.warning(f"处理第 {page} 页第 {i} 个商店结果时出错: {e}")

            return game_results
        finally:
            driver.quit()

    async def _fetch_user_search_results(self, search_user_name, page):
        """按页抓取 Steam 用户搜索结果。"""
        url = make_search_url(
            "https://steamcommunity.com/search/users/",
            {},
            {"text": search_user_name, "page": str(page)},
        )
        driver = create_driver(apply_login=True, url=url)
        try:
            driver.get(url)
            time.sleep(3)

            soup = BeautifulSoup(driver.page_source, "html.parser")
            no_user = soup.select_one(".search_results_error h2")
            if no_user and "没有符合您搜索的用户" in no_user.text:
                return []

            user_results = []
            for i, row in enumerate(soup.select(".search_row")[:10], 1):
                try:
                    name_elem = row.select_one(".searchPersonaName")
                    if not name_elem:
                        continue

                    name = name_elem.text.strip()
                    profile_url = with_lang(name_elem["href"])

                    avatar_elem = row.select_one(".avatarMedium img")
                    avatar_url = avatar_elem["src"] if avatar_elem else None

                    location = None
                    persona_info = row.select_one(".searchPersonaInfo")
                    if persona_info:
                        flag_img = persona_info.select_one("img[src*='countryflags']")
                        if flag_img:
                            location_text = ""
                            for content in flag_img.parent.contents:
                                if content == flag_img:
                                    break
                                if isinstance(content, str):
                                    location_text += content

                            location = location_text.strip()
                            if "\n" in location:
                                location = location.split("\n")[-1].strip()
                            if "&nbsp;" in location:
                                location = location.replace("&nbsp;", "").strip()

                    custom_url = None
                    match_info = row.select_one(".search_match_info")
                    if match_info:
                        url_div = match_info.select_one("div")
                        if url_div and "自定义 URL：" in url_div.text:
                            custom_url = url_div.text.replace("自定义 URL：", "").strip()

                    user_results.append({
                        "url": profile_url,
                        "name": name,
                        "avatar_url": avatar_url,
                        "location": location,
                        "custom_url": custom_url,
                    })
                except Exception as e:
                    logger.warning(f"处理第 {page} 页第 {i} 个用户结果时出错: {e}")

            return user_results
        finally:
            driver.quit()

    async def _render_store_search_page(self, game_results, page):
        html_content = Template(self.HTML_STORE_TEMPLATE).render(games=game_results, page=page)
        return await self.html_render(html_content, {})

    async def _render_user_search_page(self, user_results, page):
        html_content = Template(self.HTML_USER_TEMPLATE).render(users=user_results, page=page)
        return await self.html_render(html_content, {})

    async def _send_image_result(self, event, image_url):
        await event.send(event.image_result(image_url))

    async def steam_store_search(self, search_game_name: str, event: AstrMessageEvent):
        """搜索 Steam 商店并返回前10个结果，支持翻页。"""
        cleanup_user_states()
        user_id = event.get_sender_id()
        session_key = get_session_key(event)

        if session_key in USER_STATES and USER_STATES[session_key]["type"] == "store_search":
            yield event.plain_result("您有一个正在进行的搜索会话，请先完成或等待会话超时。")
            return

        yield event.plain_result(f"🔍 正在搜索游戏: {search_game_name}...")

        try:
            page = 1
            game_results = await self._fetch_store_search_results(search_game_name, page)
            if not game_results:
                yield event.plain_result(f"❌ 没有找到名为 {search_game_name} 的游戏。")
                return

            USER_STATES[session_key] = {
                "type": "store_search",
                "timestamp": time.time(),
                "keyword": search_game_name,
                "page": page,
                "results": game_results,
                "processed": False,
            }

            image_url = await self._render_store_search_page(game_results, page)
            yield event.image_result(image_url)

            try:
                @session_waiter(timeout=30)
                async def wait_for_store_selection(controller: SessionController, response_event: AstrMessageEvent):
                    if response_event.get_sender_id() != user_id:
                        return
                    if get_session_key(response_event) != session_key:
                        return
                    if session_key not in USER_STATES or USER_STATES[session_key].get("processed", True):
                        return

                    state = USER_STATES[session_key]
                    message = response_event.message_str.strip()

                    if is_next_page_command(message):
                        next_page = state["page"] + 1
                        next_results = await self._fetch_store_search_results(state["keyword"], next_page)
                        if not next_results:
                            await response_event.send(response_event.plain_result("⚠️ 已经是最后一页了"))
                            controller.keep(timeout=20)
                            return

                        state.update({"page": next_page, "results": next_results, "timestamp": time.time()})
                        image_url = await self._render_store_search_page(next_results, next_page)
                        await response_event.send(response_event.plain_result(f"📄 已切换到第 {next_page} 页"))
                        await self._send_image_result(response_event, image_url)
                        controller.keep(timeout=30)
                        return

                    if is_prev_page_command(message):
                        if state["page"] <= 1:
                            await response_event.send(response_event.plain_result("⚠️ 已经是第一页了"))
                            controller.keep(timeout=20)
                            return

                        prev_page = state["page"] - 1
                        prev_results = await self._fetch_store_search_results(state["keyword"], prev_page)
                        if not prev_results:
                            await response_event.send(response_event.plain_result("⚠️ 上一页加载失败，请稍后重试"))
                            controller.keep(timeout=20)
                            return

                        state.update({"page": prev_page, "results": prev_results, "timestamp": time.time()})
                        image_url = await self._render_store_search_page(prev_results, prev_page)
                        await response_event.send(response_event.plain_result(f"📄 已切换到第 {prev_page} 页"))
                        await self._send_image_result(response_event, image_url)
                        controller.keep(timeout=30)
                        return

                    if message.isdigit():
                        selection = int(message)
                        current_results = state["results"]
                        if 1 <= selection <= len(current_results):
                            state["processed"] = True
                            selected_game = current_results[selection - 1]

                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"✅ 您选择了: {selected_game['title']}\n正在获取详情...")]
                            await response_event.send(message_result)

                            await process_steam_store(response_event, selected_game["url"], self.render_options)
                            controller.stop()
                        else:
                            await response_event.send(response_event.plain_result(f"⚠️ 请输入1-{len(current_results)}的数字"))
                            controller.keep(timeout=20)
                    else:
                        await response_event.send(response_event.plain_result("⚠️ 请输入数字选择游戏，或回复 n/下一页、p/上一页"))
                        controller.keep(timeout=20)

                await wait_for_store_selection(event)

            except TimeoutError:
                state = USER_STATES.get(session_key)
                if state and state["type"] == "store_search" and not state.get("processed", False):
                    state["processed"] = True
                    default_game = state["results"][0]
                    yield event.plain_result(f"⏱️ 等待选择超时，默认选择当前页第一项: {default_game['title']}")
                    await process_steam_store(event, default_game["url"], self.render_options)

            finally:
                if session_key in USER_STATES and USER_STATES[session_key]["type"] == "store_search":
                    del USER_STATES[session_key]

        except Exception as e:
            if session_key in USER_STATES and USER_STATES[session_key]["type"] == "store_search":
                del USER_STATES[session_key]
            yield event.plain_result(f"❌ 搜索失败: {e}")

    async def steam_user_search(self, search_user_name: str, event: AstrMessageEvent):
        """搜索 Steam 用户并返回前10个结果，支持翻页。"""
        cleanup_user_states()
        user_id = event.get_sender_id()
        session_key = get_session_key(event)

        if session_key in USER_STATES and USER_STATES[session_key]["type"] == "user_search":
            yield event.plain_result("您有一个正在进行的搜索会话，请先完成或等待会话超时。")
            return

        yield event.plain_result(f"🔍 正在搜索用户: {search_user_name}...")

        try:
            page = 1
            user_results = await self._fetch_user_search_results(search_user_name, page)
            if not user_results:
                yield event.plain_result(f"❌ 没有找到名为 {search_user_name} 的用户。")
                return

            USER_STATES[session_key] = {
                "type": "user_search",
                "timestamp": time.time(),
                "keyword": search_user_name,
                "page": page,
                "results": user_results,
                "processed": False,
            }

            image_url = await self._render_user_search_page(user_results, page)
            yield event.image_result(image_url)

            try:
                @session_waiter(timeout=30)
                async def wait_for_user_selection(controller: SessionController, response_event: AstrMessageEvent):
                    if response_event.get_sender_id() != user_id:
                        return
                    if get_session_key(response_event) != session_key:
                        return
                    if session_key not in USER_STATES or USER_STATES[session_key].get("processed", True):
                        return

                    state = USER_STATES[session_key]
                    message = response_event.message_str.strip()

                    if is_next_page_command(message):
                        next_page = state["page"] + 1
                        next_results = await self._fetch_user_search_results(state["keyword"], next_page)
                        if not next_results:
                            await response_event.send(response_event.plain_result("⚠️ 已经是最后一页了"))
                            controller.keep(timeout=20)
                            return

                        state.update({"page": next_page, "results": next_results, "timestamp": time.time()})
                        image_url = await self._render_user_search_page(next_results, next_page)
                        await response_event.send(response_event.plain_result(f"📄 已切换到第 {next_page} 页"))
                        await self._send_image_result(response_event, image_url)
                        controller.keep(timeout=30)
                        return

                    if is_prev_page_command(message):
                        if state["page"] <= 1:
                            await response_event.send(response_event.plain_result("⚠️ 已经是第一页了"))
                            controller.keep(timeout=20)
                            return

                        prev_page = state["page"] - 1
                        prev_results = await self._fetch_user_search_results(state["keyword"], prev_page)
                        if not prev_results:
                            await response_event.send(response_event.plain_result("⚠️ 上一页加载失败，请稍后重试"))
                            controller.keep(timeout=20)
                            return

                        state.update({"page": prev_page, "results": prev_results, "timestamp": time.time()})
                        image_url = await self._render_user_search_page(prev_results, prev_page)
                        await response_event.send(response_event.plain_result(f"📄 已切换到第 {prev_page} 页"))
                        await self._send_image_result(response_event, image_url)
                        controller.keep(timeout=30)
                        return

                    if message.isdigit():
                        selection = int(message)
                        current_results = state["results"]
                        if 1 <= selection <= len(current_results):
                            state["processed"] = True
                            selected_user = current_results[selection - 1]

                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"✅ 您选择了: {selected_user['name']}\n正在获取详情...")]
                            await response_event.send(message_result)

                            await process_steam_profile(response_event, selected_user["url"], self.render_options)
                            controller.stop()
                        else:
                            await response_event.send(response_event.plain_result(f"⚠️ 请输入1-{len(current_results)}的数字"))
                            controller.keep(timeout=20)
                    else:
                        await response_event.send(response_event.plain_result("⚠️ 请输入数字选择用户，或回复 n/下一页、p/上一页"))
                        controller.keep(timeout=20)

                await wait_for_user_selection(event)

            except TimeoutError:
                state = USER_STATES.get(session_key)
                if state and state["type"] == "user_search" and not state.get("processed", False):
                    state["processed"] = True
                    default_user = state["results"][0]
                    yield event.plain_result(f"⏱️ 等待选择超时，默认选择当前页第一项: {default_user['name']}")
                    await process_steam_profile(event, default_user["url"], self.render_options)

            finally:
                if session_key in USER_STATES and USER_STATES[session_key]["type"] == "user_search":
                    del USER_STATES[session_key]

        except Exception as e:
            if session_key in USER_STATES and USER_STATES[session_key]["type"] == "user_search":
                del USER_STATES[session_key]
            yield event.plain_result(f"❌ 搜索用户失败: {e}")

    @filter.command("sss")
    async def search_steam_store(self, event: AstrMessageEvent):
        """搜索 Steam 商店游戏信息\n用法：/sss 游戏名"""
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("请输入要搜索的游戏名称，例如：/sss 犹格索托斯的庭院")
            return

        search_game_name = args[1]
        async for response in self.steam_store_search(search_game_name, event):
            yield response

    @filter.command("ssu")
    async def search_steam_user(self, event: AstrMessageEvent):
        """搜索 Steam 用户信息\n用法：/ssu 用户名"""
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result("请输入要搜索的 Steam 用户名，例如：/ssu m4a1_death-Dawn")
            return

        search_user_name = args[1]
        async for result in self.steam_user_search(search_user_name, event):
            yield result

    @filter.command("ssl")
    async def steam_login(self, event: AstrMessageEvent):
        """设置Steam登录状态\n用法：
        /ssl enable - 启用Steam登录
        /ssl disable - 禁用Steam登录
        /ssl status - 查看当前登录状态
        /ssl store [cookies文本] - 设置Steam商店cookies
        /ssl community [cookies文本] - 设置Steam社区cookies
        /ssl test - 测试Steam登录状态"""
        # 在函数内部导入所需函数
        from .steam_login import enable_steam_login, disable_steam_login, save_steam_cookies, get_cookie_status, test_steam_login
        
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2:
            yield event.plain_result(
                "⚠️ 请提供参数:\n"
                "/ssl enable - 启用Steam登录\n"
                "/ssl disable - 禁用Steam登录\n"
                "/ssl status - 查看当前登录状态\n"
                "/ssl store [cookies文本] - 设置Steam商店cookies\n"
                "/ssl community [cookies文本] - 设置Steam社区cookies\n"
                "/ssl test - 测试Steam登录状态"
            )
            return
        
        cmd = args[1].strip()
        
        if cmd == "enable":
            if enable_steam_login():
                # 更新插件配置
                self.enable_steam_login = True
                self.config["enable_steam_login"] = True
                self.config.save_config()
                yield event.plain_result("✅ 已启用Steam登录功能")
            else:
                yield event.plain_result("❌ 启用Steam登录功能失败")
                
        elif cmd == "disable":
            if disable_steam_login():
                # 更新插件配置
                self.enable_steam_login = False
                self.config["enable_steam_login"] = False
                self.config.save_config()
                yield event.plain_result("✅ 已禁用Steam登录功能")
            else:
                yield event.plain_result("❌ 禁用Steam登录功能失败")
                
        elif cmd == "status":
            status = get_cookie_status()
            yield event.plain_result(f"当前状态:\n{status}")
                
        elif cmd.startswith("store"):
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                yield event.plain_result(
                    "⚠️ 请提供Steam商店(store)的cookies文本\n"
                    "格式如: /ssl store steamLoginSecure=xxx; steamid=xxx; ...\n\n"
                    "获取方法:\n"
                    "1. 在浏览器中登录Steam商店(https://store.steampowered.com)\n"
                    "2. 按F12打开开发者工具\n"
                    "3. 切换到'应用'/'Application'/'存储'/'Storage'标签\n"
                    "4. 左侧选择'Cookies' > 'https://store.steampowered.com'\n"
                    "5. 复制所有cookies内容 (至少需要包含steamLoginSecure)"
                )
                return
                    
            cookies_str = parts[1]
            success, message = save_steam_cookies(cookies_str, "store")
            if success:
                # 更新插件配置
                self.steam_store_cookies = cookies_str
                self.config["steam_store_cookies"] = cookies_str
                self.config.save_config()
            yield event.plain_result(message)
                
        elif cmd.startswith("community"):
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                yield event.plain_result(
                    "⚠️ 请提供Steam社区(community)的cookies文本\n"
                    "格式如: /ssl community steamLoginSecure=xxx; steamid=xxx; ...\n\n"
                    "获取方法:\n"
                    "1. 在浏览器中登录Steam社区(https://steamcommunity.com)\n"
                    "2. 按F12打开开发者工具\n"
                    "3. 切换到'应用'/'Application'/'存储'/'Storage'标签\n"
                    "4. 左侧选择'Cookies' > 'https://steamcommunity.com'\n"
                    "5. 复制所有cookies内容 (至少需要包含steamLoginSecure)"
                )
                return
                    
            cookies_str = parts[1]
            success, message = save_steam_cookies(cookies_str, "community")
            if success:
                # 更新插件配置
                self.steam_community_cookies = cookies_str
                self.config["steam_community_cookies"] = cookies_str
                self.config.save_config()
            yield event.plain_result(message)
                
        elif cmd == "test":
            yield event.plain_result("🔄 正在测试Steam登录状态，请稍候...")
            result = await test_steam_login()
            yield event.plain_result(result)
                
        else:
            yield event.plain_result(
                "⚠️ 未知命令，可用命令:\n"
                "/ssl enable - 启用Steam登录\n"
                "/ssl disable - 禁用Steam登录\n"
                "/ssl status - 查看当前登录状态\n"
                "/ssl store [cookies文本] - 设置Steam商店cookies\n"
                "/ssl community [cookies文本] - 设置Steam社区cookies\n"
                "/ssl test - 测试Steam登录状态"
            )

    # 在配置变更时应用新配置
    def on_config_changed(self):
        """当插件配置在WebUI上被修改时调用"""
        self._load_config_values()
        
        # 应用新配置
        self._apply_config()

    async def terminate(self):
        """插件卸载/重载时清理会话状态和临时截图。"""
        USER_STATES.clear()
        clear_info_cache()
        cleanup_temp_screenshots()
