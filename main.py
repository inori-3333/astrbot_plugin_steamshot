import os
import time

# **🔹 依赖导入**
import ssl
import re
import asyncio
import requests
from bs4 import BeautifulSoup
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.all import *
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)
from jinja2 import Template
import json
# 从steam_login导入需要的函数，但不在顶层使用
from .steam_login import apply_cookies_to_driver, get_login_status
from .browser_runtime import create_chrome_webdriver

# 用户状态跟踪
USER_STATES = {}

# **🔹 Steam 链接匹配正则**
STEAM_URL_PATTERN = r"https://store\.steampowered\.com/app/(\d+)/[\w\-]+/?"
STEAM_PROFILE_URL_PATTERN = r"https://steamcommunity\.com/(profiles/\d{17}|id/[A-Za-z0-9\-_]+)/?"
STEAM_WORKSHOP_URL_PATTERN = r"https://steamcommunity\.com/(sharedfiles/filedetails|workshop/filedetails)/\?id=(\d+)"

# **🔹 截图路径**
STORE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/store_screenshot.png"
PROFILE_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/profile_screenshot.png"
WORKSHOP_SCREENSHOT_PATH = "./data/plugins/astrbot_plugin_steamshot/screenshots/workshop_screenshot.png"

def create_driver(apply_login=True, url=None):
    """ 创建 Selenium WebDriver，支持可选的Steam登录 """
    driver = create_chrome_webdriver()
    
    # 如果启用了登录并且传入了apply_login参数，应用Steam登录cookies
    if apply_login:
        from .steam_login import apply_cookies_to_driver
        # 传入URL参数，让函数根据URL自动选择应用哪个域的cookies
        login_applied = apply_cookies_to_driver(driver, url)
        if login_applied:
            print("✅ 已应用Steam登录信息")
    
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
                print("🔞 检测到 Steam 年龄验证页面，正在自动跳过...")
            # 检测敏感内容验证页面特有元素
            elif driver.find_elements(By.ID, "app_agegate") and driver.find_elements(By.ID, "view_product_page_btn"):
                is_content_verification = True
                print("🔞 检测到 Steam 敏感内容验证页面，正在自动跳过...")
        except:
            # 如果上述检测失败，尝试通过页面源码判断
            page_source = driver.page_source
            if "ageYear" in page_source:
                is_age_verification = True
                print("🔞 检测到 Steam 年龄验证页面，正在自动跳过...")
            elif "app_agegate" in page_source and "view_product_page_btn" in page_source:
                is_content_verification = True
                print("🔞 检测到 Steam 敏感内容验证页面，正在自动跳过...")
        
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
            except:
                # 如果按钮点击失败，尝试执行JS函数
                driver.execute_script("ViewProductPage()")
        
        else:
            # 如果无法确定验证类型，但确实在agecheck页面，尝试通用方法
            print("⚠️ 未能识别验证类型，尝试通用方法跳转...")
            try:
                # 尝试执行 JS 跳转函数 (两种验证页面都使用这个函数)
                driver.execute_script("ViewProductPage()")
            except:
                # 尝试点击任何可能的"查看页面"按钮
                buttons = driver.find_elements(By.CSS_SELECTOR, ".btnv6_blue_hoverfade")
                for button in buttons:
                    if "查看" in button.text:
                        button.click()
                        break
        
        # 等待 URL 发生变化，表示跳转成功
        WebDriverWait(driver, 10).until(EC.url_changes(before_url))
        print("✅ 已跳转至游戏页面")

        # 等待游戏页面加载完成 (寻找游戏名称元素)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "apphub_AppName"))
        )

    except Exception as e:
        print(f"⚠️ Steam 验证页面跳过失败: {e}")

async def capture_screenshot(url, save_path):
    """ 截取网页完整截图（支持懒加载内容） """
    def run():
        driver = None
        try:
            # 修改：传递URL参数以应用正确的cookies
            driver = create_driver(apply_login=True, url=url)
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
        # 传入URL以便应用正确的cookies
        driver = create_driver(apply_login=True, url=url)
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
        # 导入Tag类用于类型检查
        from bs4.element import Tag
        
        # 传入URL以便应用正确的cookies
        driver = create_driver(apply_login=True, url=url)
        if not driver:
            return []
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

            # 修改价格解析逻辑
            price_items = []
            
            # 检查是否为预购游戏
            is_preorder = False
            preorder_date = None
            coming_soon_div = soup.find("div", class_="game_area_comingsoon")
            if coming_soon_div:
                is_preorder = True
                coming_soon_h1 = coming_soon_div.find("h1")
                if coming_soon_h1:
                    preorder_date = coming_soon_h1.text.strip()
                print(f"✅ 检测到预购游戏: {preorder_date}")

            # 检查是否为免费游戏
            is_free_game = False
            free_tag = soup.find("div", class_="game_purchase_price", string=lambda s: s and ("免费" in s or "free" in s.lower()))
            if free_tag:
                is_free_game = True
                print("✅ 检测到免费游戏")

            try:
                # 根据游戏类型选择不同的处理逻辑
                if is_free_game:
                    price_items.append("免费游戏")
                elif is_preorder:  # 添加这个条件分支处理预购游戏
                    print("🔍 尝试提取预购游戏的价格信息")
                    
                    # 专门处理预购游戏的价格提取
                    purchase_area = soup.find("div", id="game_area_purchase")
                    if purchase_area:
                        print("✅ 找到预购游戏购买区域")
                        
                        # 1. 查找所有可能的预购选项容器
                        preorder_containers = []
                        
                        # 搜索所有可能的预购容器类型
                        # 只使用顶层容器，避免重复选择
                        for container in purchase_area.select(".game_area_purchase_game_wrapper"):
                            preorder_containers.append(container)

                        # 如果找不到上面的容器，尝试其他选择器
                        if not preorder_containers:
                            for container in purchase_area.select(".game_area_purchase_game"):
                                # 确保这不是某个已选择容器的子元素
                                if not any(c.find(container) for c in preorder_containers):
                                    preorder_containers.append(container)

                        # 最后，如果仍然找不到，尝试从版本选项容器中查找
                        if not preorder_containers:
                            for container in purchase_area.select(".game_purchase_options_editions_container > div"):
                                if container.select_one("h2.title, h1.title") is not None:
                                    preorder_containers.append(container)

                        # 去重处理 - 使用URL或标题作为唯一标识
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

                        print(f"✅ 找到 {len(preorder_containers)} 个唯一预购选项容器")
                        
                        if not preorder_containers:
                            # 如果没有找到标准容器，尝试直接从purchase_area获取信息
                            print("⚠️ 没有找到标准预购容器，尝试直接分析")
                            
                            # 从购买区域直接提取价格信息
                            price_elems = purchase_area.select(".game_purchase_price, .discount_final_price")
                            for price_elem in price_elems:
                                price_text = price_elem.text.strip()
                                if price_text:
                                    preorder_title = f"预购 {game_name}"
                                    if preorder_date:
                                        preorder_title += f" ({preorder_date})"
                                    
                                    # 检查是否有折扣
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
                                    
                                    print(f"💲 预购价格: {formatted_price}")
                                    price_items.append(formatted_price)
                        else:
                            # 处理找到的预购容器
                            for i, container in enumerate(preorder_containers):
                                try:
                                    # 尝试查找标题
                                    title_elem = container.select_one("h1.title, h2.title, .game_purchase_options_editions_header_title")
                                    title = title_elem.text.strip() if title_elem else f"预购 {game_name}"
                                    
                                    # 确保标题包含"预购"字样
                                    if "预购" not in title:
                                        title = f"预购 {title}"
                                    
                                    # 如果有预购日期，添加到标题
                                    if preorder_date and preorder_date not in title:
                                        title += f" ({preorder_date})"
                                    
                                    # 查找价格元素
                                    price_elem = container.select_one(".game_purchase_price, .discount_final_price")
                                    if price_elem:
                                        price_text = price_elem.text.strip()
                                        
                                        # 检查是否有折扣
                                        discount_elem = container.select_one(".discount_pct")
                                        if discount_elem:
                                            discount_text = discount_elem.text.strip()
                                            formatted_price = f"{title}   {discount_text}   {price_text}"
                                        else:
                                            formatted_price = f"{title}   {price_text}"
                                        
                                        print(f"💲 预购价格选项 {i+1}: {formatted_price}")
                                        price_items.append(formatted_price)
                                    else:
                                        # 如果没有找到价格，至少显示预购信息
                                        price_items.append(f"{title}   价格未知")
                                except Exception as e:
                                    print(f"❌ 处理预购选项 {i+1} 时出错: {e}")
                        
                        # 如果所有方法都失败，至少显示它是预购游戏
                        if not price_items:
                            preorder_info = f"预购 {game_name}"
                            if preorder_date:
                                preorder_info += f" ({preorder_date})"
                            price_items.append(f"{preorder_info}   价格未知")
                    else:
                        # 如果没有购买区域，添加基本预购信息
                        preorder_info = f"预购 {game_name}"
                        if preorder_date:
                            preorder_info += f" ({preorder_date})"
                        price_items.append(preorder_info)
                else:
                    # 找到游戏购买区域
                    purchase_area = soup.find("div", id="game_area_purchase")
                    if purchase_area:
                        print("✅ 找到游戏购买区域")
                        
                        # 获取所有购买选项包装器，但排除DLC部分
                        purchase_wrappers = []
                        
                        for child in purchase_area.children:
                            if not isinstance(child, Tag):
                                continue
                            
                            # 一旦遇到DLC部分，停止收集
                            if child.get("id") == "gameAreaDLCSection":
                                print("✅ 找到DLC部分，停止收集购买选项")
                                break
                            
                            if "game_area_purchase_game_wrapper" in child.get("class", []):
                                purchase_wrappers.append(child)
                        
                        print(f"✅ 找到 {len(purchase_wrappers)} 个购买选项")
                        
                        # 处理每个购买选项
                        for i, wrapper in enumerate(purchase_wrappers):
                            try:
                                # 跳过下拉框部分
                                if wrapper.find("div", class_="game_purchase_sub_dropdown"):
                                    print(f"⏩ 跳过第 {i+1} 个购买选项，因为它是下拉框")
                                    continue
                                
                                # 处理动态捆绑包
                                if "dynamic_bundle_description" in wrapper.get("class", []):
                                    print(f"🔍 第 {i+1} 个购买选项是动态捆绑包")
                                    
                                    # 查找捆绑包标题
                                    bundle_title_elem = wrapper.find("h2", class_="title")
                                    if not bundle_title_elem:
                                        print(f"⚠️ 第 {i+1} 个捆绑包没有找到标题元素")
                                        continue
                                    
                                    # 清理捆绑包标题，移除多余文本
                                    bundle_title = bundle_title_elem.get_text(strip=True)
                                    if bundle_title.startswith("购买 "):
                                        bundle_title = bundle_title[3:]
                                    
                                    # 移除可能的"(?)"符号
                                    bundle_title = bundle_title.replace("(?)", "").strip()
                                    
                                    print(f"📦 捆绑包标题: {bundle_title}")
                                    
                                    # 检查是否已完成合集
                                    collection_complete = wrapper.find("span", class_="collectionComplete")
                                    if collection_complete:
                                        print(f"✓ 捆绑包 \"{bundle_title}\" 已完成合集")
                                        price_items.append(f"{bundle_title}   已完成合集")
                                        continue
                                    
                                    # 获取折扣和价格
                                    discount_block = wrapper.find("div", class_="discount_block")
                                    if discount_block:
                                        discount_pct = discount_block.find("div", class_="bundle_base_discount")
                                        final_price = discount_block.find("div", class_="discount_final_price")
                                        
                                        if discount_pct and final_price:
                                            # 清理价格文本，确保格式正确
                                            discount_text = discount_pct.text.strip()
                                            price_text = final_price.text.strip()
                                            # 如果价格文本包含"您的价格："，只保留价格部分
                                            if "您的价格：" in price_text:
                                                price_parts = price_text.split("您的价格：")
                                                price_text = price_parts[-1].strip()
                                            
                                            formatted_price = f"{bundle_title}   {discount_text}   {price_text}"
                                            print(f"💲 捆绑包价格: {formatted_price}")
                                            price_items.append(formatted_price)
                                        elif final_price:
                                            price_text = final_price.text.strip()
                                            # 如果价格文本包含"您的价格："，只保留价格部分
                                            if "您的价格：" in price_text:
                                                price_parts = price_text.split("您的价格：")
                                                price_text = price_parts[-1].strip()
                                                
                                            formatted_price = f"{bundle_title}   {price_text}"
                                            print(f"💲 捆绑包价格: {formatted_price}")
                                            price_items.append(formatted_price)
                                    
                                    continue
                                
                                # 处理普通游戏购买选项
                                print(f"🔍 第 {i+1} 个购买选项是普通游戏")
                                
                                game_purchase = wrapper.find("div", class_="game_area_purchase_game")
                                if not game_purchase:
                                    print(f"⚠️ 第 {i+1} 个购买选项没有找到game_area_purchase_game元素")
                                    continue
                                
                                title_elem = game_purchase.find("h2", class_="title")
                                if not title_elem:
                                    print(f"⚠️ 第 {i+1} 个购买选项没有找到标题元素")
                                    continue
                                
                                title = title_elem.text.strip()
                                if title.startswith("购买 "):
                                    title = title[3:]
                                
                                print(f"🎮 游戏标题: {title}")
                                
                                # 检查是否在库中
                                in_library = game_purchase.find("div", class_="package_in_library_flag")
                                
                                if in_library:
                                    print(f"✓ 游戏 \"{title}\" 已在库中")
                                    price_items.append(f"{title}   在库中")
                                    continue
                                
                                # 获取价格信息
                                discount_block = game_purchase.find("div", class_="discount_block")
                                regular_price = game_purchase.find("div", class_="game_purchase_price")
                                
                                if discount_block:
                                    discount_pct = discount_block.find("div", class_="discount_pct")
                                    final_price = discount_block.find("div", class_="discount_final_price")
                                    
                                    if discount_pct and final_price:
                                        price_text = f"{title}   {discount_pct.text.strip()}   {final_price.text.strip()}"
                                        print(f"💲 折扣价格: {price_text}")
                                        price_items.append(price_text)
                                    elif final_price:
                                        price_text = f"{title}   {final_price.text.strip()}"
                                        print(f"💲 最终价格: {price_text}")
                                        price_items.append(price_text)
                                elif regular_price:
                                    price_text = f"{title}   {regular_price.text.strip()}"
                                    print(f"💲 常规价格: {price_text}")
                                    price_items.append(price_text)
                                else:
                                    print(f"⚠️ 游戏 \"{title}\" 没有找到价格信息")
                                    price_items.append(f"{title}   价格未知")
                            except Exception as e:
                                print(f"❌ 处理第 {i+1} 个购买选项时出错: {e}")
                                continue
                    else:
                        print("⚠️ 没有找到游戏购买区域")
            except Exception as e:
                print(f"❌ 解析价格信息时出错: {e}")
            
            # 格式化价格信息
            price_text = "\n".join(price_items) if price_items else "暂无售价"

            return {
                "🎮 游戏名称": game_name,
                "📅 发行时间": release_date,
                "🏗 开发商": developers,
                "🏛 发行商": publisher,
                "🎭 游戏类别": tags,
                "📜 简介": description,
                "⭐ 评分": review_summary,
                "💰 价格": f"\n{price_text}"
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
        # 传入URL以便应用正确的cookies
        driver = create_driver(apply_login=True, url=url)
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
                    standard_profile_lines.append(f"🚫 封禁纪录: \n" + "\n".join(ban_records))

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

def verify_steam_login(driver):
    """
    验证Steam登录状态是否有效
    参数:
    - driver: Selenium WebDriver实例
    返回:
    - (bool, str): 登录状态和用户名(如有)
    """
    try:
        # 访问Steam首页
        driver.get("https://store.steampowered.com/")
        time.sleep(2)
        
        # 检查登录状态 - 查找顶部导航栏中的账户名元素
        account_menu = driver.find_element(By.ID, "account_pulldown")
        if account_menu:
            username = account_menu.text.strip()
            if username and username != "登录" and username != "Sign In":
                return True, username
        
        # 尝试其他方法 - 查找账户下拉菜单中是否有"查看个人资料"链接
        try:
            profile_link = driver.find_element(By.XPATH, "//a[contains(@href, '/profiles/') or contains(@href, '/id/')]")
            if profile_link:
                return True, "已登录 (未获取到用户名)"
        except:
            pass
            
        return False, "未登录"
    except Exception as e:
        print(f"❌ 验证Steam登录状态失败: {e}")
        return False, f"验证失败: {str(e)}"

async def test_steam_login():
    """测试Steam登录状态"""
    driver = None
    try:
        driver = create_driver(apply_login=True)
        login_status, username = verify_steam_login(driver)
        
        if login_status:
            return f"✅ Steam登录成功! 用户名: {username}"
        else:
            return f"❌ Steam登录失败: {username}"
    except Exception as e:
        return f"❌ 测试Steam登录出错: {e}"
    finally:
        if driver:
            driver.quit()

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
            <div class="note">请在30秒内回复对应游戏的序号，否则将默认访问第一个游戏</div>
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
            <div class="note">请在30秒内回复对应用户的序号，否则将默认访问第一个用户</div>
        </div>
    </body>
    </html>
    """

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # 初始化配置
        self.config = config or {}
        
        # 从配置中读取Steam登录设置
        self.enable_steam_login = self.config.get("enable_steam_login", False)
        self.steam_store_cookies = self.config.get("steam_store_cookies", "")
        self.steam_community_cookies = self.config.get("steam_community_cookies", "")
        
        # 应用配置
        self._apply_config()
        
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

    async def steam_store_search(self, search_game_name: str, event: AstrMessageEvent):
        """搜索 Steam 商店并返回前10个结果"""
        user_id = event.get_sender_id()
        
        # 检查用户是否已经有搜索会话
        if user_id in USER_STATES and USER_STATES[user_id]["type"] == "store_search":
            yield event.plain_result("您有一个正在进行的搜索会话，请先完成或等待会话超时。")
            return
        
        yield event.plain_result(f"🔍 正在搜索游戏: {search_game_name}...")
        
        try:
            # 使用登录状态搜索
            login_driver = create_driver(apply_login=True, url="https://store.steampowered.com/")
            url = f"https://store.steampowered.com/search/?term={search_game_name}&ndl=1"
            game_results = []
            
            try:
                login_driver.get(url)
                time.sleep(2)
                
                soup = BeautifulSoup(login_driver.page_source, "html.parser")
                
                # 检查是否有结果
                no_result_div = soup.select_one("#search_results .search_results_count")
                if no_result_div and "0 个匹配的搜索结果" in no_result_div.text:
                    yield event.plain_result(f"❌ 没有找到名为 {search_game_name} 的游戏。")
                    return
                
                # 获取搜索结果
                result_containers = soup.select("#search_resultsRows a")
                
                if not result_containers:
                    yield event.plain_result("⚠️ 未找到搜索结果。")
                    return
                
                # 限制为前10个结果
                result_containers = result_containers[:10]
                
                # 在for循环中修改价格提取部分
                for i, container in enumerate(result_containers, 1):
                    try:
                        game_url = container["href"]
                        title = container.select_one(".title").text.strip() if container.select_one(".title") else "未知标题"
                        
                        # 获取封面图片
                        image_elem = container.select_one(".search_capsule img")
                        image_url = image_elem["src"] if image_elem else None
                        
                        # 获取发布日期
                        release_date = container.select_one(".search_released")
                        release_date = release_date.text.strip() if release_date else "未知"
                        
                        # 改进价格提取逻辑
                        price = "未知"

                        # 直接获取价格容器
                        price_container = container.select_one(".search_price_discount_combined")
                        if price_container:
                            # 检查游戏是否免费
                            if price_container.get("data-price-final") == "0":
                                price = "免费游戏"
                            else:
                                # 检查是否有折扣区块
                                discount_block = price_container.select_one(".discount_block")
                                if discount_block:
                                    # 判断是否有折扣
                                    has_discount = "no_discount" not in discount_block.get("class", [])
                                    
                                    if has_discount:
                                        # 获取折扣百分比
                                        discount_pct_elem = discount_block.select_one(".discount_pct")
                                        discount_pct = discount_pct_elem.text.strip() if discount_pct_elem else ""
                                        
                                        # 获取折扣后价格
                                        final_price_elem = discount_block.select_one(".discount_final_price")
                                        final_price = final_price_elem.text.strip() if final_price_elem else ""
                                        
                                        # 获取原价
                                        original_price_elem = discount_block.select_one(".discount_original_price")
                                        original_price = original_price_elem.text.strip() if original_price_elem else ""
                                        
                                        # 组合价格信息
                                        if discount_pct and final_price:
                                            price = f"{discount_pct} {final_price}"
                                        elif final_price:
                                            price = final_price
                                    else:
                                        # 无折扣游戏
                                        final_price_elem = discount_block.select_one(".discount_final_price")
                                        if final_price_elem:
                                            if "free" in final_price_elem.get("class", []):
                                                price = "免费游戏"
                                            else:
                                                price = final_price_elem.text.strip()
                        
                        game_results.append({
                            "url": game_url,
                            "title": title,
                            "image_url": image_url,
                            "release_date": release_date,
                            "price": price
                        })
                    except Exception as e:
                        print(f"处理结果 {i} 时出错: {e}")
                        continue
            finally:
                login_driver.quit()
            
            if not game_results:
                yield event.plain_result("⚠️ 解析搜索结果失败，请尝试其他关键词。")
                return
            
            USER_STATES[user_id] = {
                "type": "store_search",
                "timestamp": time.time(),
                "results": game_results,
                "processed": False  # 添加新标志，标记是否已处理用户选择
            }
            
            # 渲染HTML为图片
            html_content = Template(self.HTML_STORE_TEMPLATE).render(games=game_results)
            image_url = await self.html_render(html_content, {})
            yield event.image_result(image_url)
            
            # 启动会话控制器等待用户选择
            try:
                @session_waiter(timeout=30)
                async def wait_for_store_selection(controller: SessionController, response_event: AstrMessageEvent):
                    if response_event.get_sender_id() != user_id:
                        return
                    
                    # 检查会话是否已处理
                    if user_id not in USER_STATES or USER_STATES[user_id].get("processed", True):
                        return
                        
                    message = response_event.message_str.strip()
                    
                    # 检查是否是数字选择
                    if message.isdigit():
                        selection = int(message)
                        if 1 <= selection <= len(game_results):
                            # 标记已处理
                            USER_STATES[user_id]["processed"] = True
                            
                            # 获取选中的游戏链接
                            selected_game = game_results[selection - 1]
                            
                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"✅ 您选择了: {selected_game['title']}\n正在获取详情...")]
                            await response_event.send(message_result)
                            
                            # 跳转到选中的游戏页面
                            await process_steam_store(response_event, selected_game["url"])
                            controller.stop()
                        else:
                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"⚠️ 请输入1-{len(game_results)}的数字")]
                            await response_event.send(message_result)
                            controller.keep(timeout=20)
                    else:
                        message_result = response_event.make_result()
                        message_result.chain = [Comp.Plain("⚠️ 请输入数字选择游戏")]
                        await response_event.send(message_result)
                        controller.keep(timeout=20)
                
                await wait_for_store_selection(event)
                
            except TimeoutError:
                # 超时处理 - 默认选择第一项
                # 检查是否已经处理，避免重复处理
                if user_id in USER_STATES and USER_STATES[user_id]["type"] == "store_search" and not USER_STATES[user_id].get("processed", False):
                    USER_STATES[user_id]["processed"] = True
                    default_game = USER_STATES[user_id]["results"][0]
                    yield event.plain_result(f"⏱️ 等待选择超时，默认选择第一项: {default_game['title']}")
                    await process_steam_store(event, default_game["url"])
                    
            finally:
                # 清理用户状态
                if user_id in USER_STATES and USER_STATES[user_id]["type"] == "store_search":
                    del USER_STATES[user_id]
                    
        except Exception as e:
            if user_id in USER_STATES and USER_STATES[user_id]["type"] == "store_search":
                del USER_STATES[user_id]
            yield event.plain_result(f"❌ 搜索失败: {e}")

    async def steam_user_search(self, search_user_name: str, event: AstrMessageEvent):
        """搜索 Steam 用户并返回前10个结果"""
        user_id = event.get_sender_id()
        
        # 检查用户是否已经有搜索会话
        if user_id in USER_STATES and USER_STATES[user_id]["type"] == "user_search":
            yield event.plain_result("您有一个正在进行的搜索会话，请先完成或等待会话超时。")
            return
        
        yield event.plain_result(f"🔍 正在搜索用户: {search_user_name}...")
        
        try:
            url = f"https://steamcommunity.com/search/users/#text={search_user_name}"
            driver = create_driver(apply_login=True, url=url)
            user_results = []
            
            try:
                driver.get(url)
                # 等待页面加载，Steam用户搜索需要额外时间渲染结果
                time.sleep(3)
                
                soup = BeautifulSoup(driver.page_source, "html.parser")
                
                # 检查是否没有用户
                no_user = soup.select_one(".search_results_error h2")
                if no_user and "没有符合您搜索的用户" in no_user.text:
                    yield event.plain_result(f"❌ 没有找到名为 {search_user_name} 的用户。")
                    return
                
                # 获取搜索结果
                search_rows = soup.select(".search_row")
                
                if not search_rows:
                    yield event.plain_result("⚠️ 未找到用户搜索结果。")
                    return
                
                # 限制为前10个结果
                search_rows = search_rows[:10]
                
                for row in search_rows:
                    try:
                        # 获取用户名和链接
                        name_elem = row.select_one(".searchPersonaName")
                        if not name_elem:
                            continue
                            
                        name = name_elem.text.strip()
                        profile_url = name_elem["href"]
                        
                        # 获取头像
                        avatar_elem = row.select_one(".avatarMedium img")
                        avatar_url = avatar_elem["src"] if avatar_elem else None
                        
                        # 获取地区信息
                        location = None
                        persona_info = row.select_one(".searchPersonaInfo")
                        if persona_info:
                            # 寻找国旗图标，它总是紧跟在地区信息后面
                            flag_img = persona_info.select_one("img[src*='countryflags']")
                            if flag_img:
                                # 提取国旗前的文本，但只取同一行的文本（地区信息）
                                location_text = ""
                                
                                # 获取国旗图片的父节点内容
                                for content in flag_img.parent.contents:
                                    # 只提取国旗图片前的文本节点
                                    if content == flag_img:
                                        break
                                    if isinstance(content, str):
                                        location_text += content
                                
                                # 清理文本
                                location = location_text.strip()
                                
                                # 如果包含换行符，说明可能混入了别名，只取最后一部分
                                if "\n" in location:
                                    location = location.split("\n")[-1].strip()
                                
                                # 替换HTML特殊字符
                                if "&nbsp;" in location:
                                    location = location.replace("&nbsp;", "").strip()
                        
                        # 获取自定义URL
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
                            "custom_url": custom_url
                        })
                    except Exception as e:
                        print(f"处理用户结果时出错: {e}")
                        continue
            finally:
                driver.quit()
                
            if not user_results:
                yield event.plain_result("⚠️ 解析用户搜索结果失败，请尝试其他关键词。")
                return
                
            # 保存搜索结果到用户状态
            USER_STATES[user_id] = {
                "type": "user_search",
                "timestamp": time.time(),
                "results": user_results,
                "processed": False  # 添加新标志
            }
            
            # 渲染HTML为图片
            html_content = Template(self.HTML_USER_TEMPLATE).render(users=user_results)
            image_url = await self.html_render(html_content, {})
            yield event.image_result(image_url)
            
            # 启动会话控制器等待用户选择
            try:
                @session_waiter(timeout=30)
                async def wait_for_user_selection(controller: SessionController, response_event: AstrMessageEvent):
                    if response_event.get_sender_id() != user_id:
                        return
                    
                    # 检查会话是否已处理
                    if user_id not in USER_STATES or USER_STATES[user_id].get("processed", True):
                        return
                        
                    message = response_event.message_str.strip()
                    
                    # 检查是否是数字选择
                    if message.isdigit():
                        selection = int(message)
                        if 1 <= selection <= len(user_results):
                            # 标记已处理
                            USER_STATES[user_id]["processed"] = True
                            
                            # 获取选中的用户链接
                            selected_user = user_results[selection - 1]
                            
                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"✅ 您选择了: {selected_user['name']}\n正在获取详情...")]
                            await response_event.send(message_result)
                            
                            # 跳转到选中的用户页面
                            await process_steam_profile(response_event, selected_user["url"])
                            controller.stop()
                        else:
                            message_result = response_event.make_result()
                            message_result.chain = [Comp.Plain(f"⚠️ 请输入1-{len(user_results)}的数字")]
                            await response_event.send(message_result)
                            controller.keep(timeout=20)
                    else:
                        message_result = response_event.make_result()
                        message_result.chain = [Comp.Plain("⚠️ 请输入数字选择用户")]
                        await response_event.send(message_result)
                        controller.keep(timeout=20)
                    
                await wait_for_user_selection(event)
                
            except TimeoutError:
                # 超时处理 - 默认选择第一项，增加条件判断
                if user_id in USER_STATES and USER_STATES[user_id]["type"] == "user_search" and not USER_STATES[user_id].get("processed", False):
                    USER_STATES[user_id]["processed"] = True
                    default_user = USER_STATES[user_id]["results"][0]
                    yield event.plain_result(f"⏱️ 等待选择超时，默认选择第一项: {default_user['name']}")
                    await process_steam_profile(event, default_user["url"])
                    
            finally:
                # 清理用户状态
                if user_id in USER_STATES and USER_STATES[user_id]["type"] == "user_search":
                    del USER_STATES[user_id]
                    
        except Exception as e:
            if user_id in USER_STATES and USER_STATES[user_id]["type"] == "user_search":
                del USER_STATES[user_id]
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
        # 读取新配置
        self.enable_steam_login = self.config.get("enable_steam_login", False)
        self.steam_store_cookies = self.config.get("steam_store_cookies", "")
        self.steam_community_cookies = self.config.get("steam_community_cookies", "")
        
        # 应用新配置
        self._apply_config()

