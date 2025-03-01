import subprocess
import sys
import ssl

# 检查并安装selenium库

def install_selenium():
    try:
        import selenium
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "selenium"])
        import selenium


install_selenium()

def install_webdriver_manager():
    try:
        import webdriver_manager
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "webdriver_manager"])
        import webdriver_manager


install_webdriver_manager()


import re  # 导入正则表达式模块
# import uuid  # 导入UUID模块，用于生成唯一标识符
from astrbot.api.event import filter, AstrMessageEvent  # 导入AstrBot的事件过滤器和消息事件类
from astrbot.api.star import Context, Star, register  # 导入AstrBot的上下文、Star类和注册函数
from astrbot.api import logger  # 导入日志记录器
from astrbot.api.all import *
import requests
# from typing import Optional
import os
from bs4 import BeautifulSoup  # 导入BeautifulSoup库
from selenium import webdriver  # 导入Selenium WebDriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
# from selenium.webdriver.chrome.options import Options


ssl._create_default_https_context = ssl._create_unverified_context

# 定义Steam URL的正则表达式模式
STEAM_URL_PATTERN = r"https://store\.steampowered\.com/app/\d+/[\w\-]+/?"
save_path = "./data/plugins/astrbot_plugin_steamshot/screenshots/screenshot.png"
# current_dir = os.path.dirname(os.path.abspath(__file__))
# crawler_path = os.path.join(current_dir, "crawler1.py")

# 定义获取网页截图的函数
# def get_webpage_screenshot(url):
#     # API地址
#     api_url = "https://api.pearktrue.cn/api/screenweb/"
#     # 请求参数
#     params = {
#         "url": url,
#         "type": "image",
#     }
#     try:
#         # 发送GET请求
#         response = requests.get(api_url, params=params)
#         response.raise_for_status()  # 检查请求是否成功
#         # 将返回的图片内容保存到本地
#         image_data = response.content
#         screenshot_path = f"./data/plugins/astrbot_plugin_steamshot/screenshot.png"
#         with open(screenshot_path, "wb") as file:
#             file.write(image_data)
#         result = MessageChain()  # 创建消息链对象
#         result.chain = []
#         if screenshot_path:
#             # result.chain.append(Plain("截图结果：\n"))  # 添加文本消息
#             return screenshot_path
#         else:
#             result.chain.append(Plain("网页截图失败，请检查URL格式是否正确或稍后再试。"))  # 添加失败消息
#             return 0
#     except requests.exceptions.RequestException as e:
#         print(f"请求异常: {e}")  # 打印请求异常信息
#         return None


def capture_screenshot(url, save_path):
    try:
        # 配置Chrome无头模式（不显示浏览器界面）
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")  # 无头模式
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")

        # 自动下载并配置ChromeDriver
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.set_window_size(1440, 1600)

        # 访问URL并等待页面加载
        driver.get(url)
        driver.implicitly_wait(3)  # 隐式等待3秒

        # 创建保存目录（如果不存在）
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        # 截图并保存
        driver.save_screenshot(save_path)
        print(f"截图已保存至: {save_path}")

    except Exception as e:
        print(f"错误发生: {str(e)}")
    finally:
        driver.quit()


# 定义下载和解析Steam网页的函数
def get_steam_page_info(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.5'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # 检查年龄验证页面
        if "agecheck" in response.url:
            print("需要年龄验证，跳过此页面")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取游戏信息
        game_name = soup.find('div', class_='apphub_AppName').text.strip()
        release_date = soup.find('div', class_='date').text.strip()

        # 开发商可能有多个
        developers = [a.text.strip() for a in soup.select('div#developers_list a')]

        # 查找发行商信息
        publisher_div = soup.find('div', class_='dev_row')
        if publisher_div:
            publisher = publisher_div.find_next_sibling('div').text.strip()
            publisher = publisher.replace("Publisher:\n\n", "")
        else:
            publisher = "未知"

        return {
            '游戏名称\n': game_name,
            '发行时间\n': release_date,
            '开发商\n': ", ".join(developers),
            '发行商\n': publisher
        }

    except requests.exceptions.RequestException as e:
        print(f"请求错误: {e}")
        return None
    except AttributeError as e:
        print(f"解析错误，可能页面结构已改变: {e}")
        return None


# 注册插件
@register("astrbot_plugin_steamshot", "Inori-3333", "根据群聊中 Steam 相关链接自动发送 Steam 页面信息", "1.0.0", "https://github.com/inori-3333/astrbot_plugin_steamshot")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)  # 调用父类的构造函数

    # 使用正则表达式过滤Steam URL
    @filter.regex(STEAM_URL_PATTERN)
    async def github_repo(self, event: AstrMessageEvent):
        '''解析 steam 链接'''
        msg = event.message_str  # 获取消息字符串
        match = re.search(STEAM_URL_PATTERN, msg)  # 匹配Steam URL
        steam_url = match.group(0)  # 获取匹配的URL
        # result_t = get_steam_page_info(steam_url)
        # , description = get_steam_page_info(steam_url)  # 获取Steam页面信息
        # result_t = f"游戏标题: {title}\n游戏描述: {description}"
        # await event.send(result_p)
        # 运行 crawler.py 并传递歌名作为参数
        result = MessageChain()  # 创建消息链对象
        result.chain = []
        try:
            capture_screenshot(steam_url, save_path)
            # subprocess.run(["python", "./data/plugins/astrbot_plugin_steamshot/r2.py", "--Steam_URL", steam_url])
            if os.path.exists("./data/plugins/astrbot_plugin_steamshot/screenshots/screenshot.png"):
                result.chain.append(Image.fromFileSystem("./data/plugins/astrbot_plugin_steamshot/screenshots/screenshot.png"))
            else:
                result.chain.append(Plain("网页截图失败，请检查URL格式是否正确或稍后再试。"))
            await event.send(result)
        except Exception as e:
            print(f"执行r2.py错误: {e}")  # 打印执行错误信息
            return None
        result.chain = []
        try:
            game_info = get_steam_page_info(steam_url)
            if game_info:
                game_info_str = "\n".join([f"{key}: {value}" for key, value in game_info.items()])
                result.chain.append(Plain(game_info_str))
            else:
                result.chain.append(Plain("摘要抓取失败，请检查URL格式是否正确或稍后再试。"))  # 添加失败消息
            await event.send(result)
        except requests.exceptions.RequestException as e:
            print(f"请求异常: {e}")  # 打印请求异常信息
            return None
