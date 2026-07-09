import os
import json
import pickle
import tempfile
from datetime import datetime
import re
from astrbot.api import logger
from .browser_runtime import create_chrome_webdriver

# 存储目录和文件
DEFAULT_PLUGIN_DATA_DIR = "./data/plugins/astrbot_plugin_steamshot"
PLUGIN_DATA_DIR = os.getenv("STEAMSHOT_DATA_DIR", DEFAULT_PLUGIN_DATA_DIR)
STEAM_AUTH_DIR = os.path.join(PLUGIN_DATA_DIR, "auth")
STEAM_COOKIES_JSON_FILE = os.path.join(STEAM_AUTH_DIR, "steam_cookies.json")

# 兼容旧版文件路径 (用于迁移)
STEAM_STORE_COOKIES_FILE = os.path.join(STEAM_AUTH_DIR, "steam_store_cookies.pkl")
STEAM_COMMUNITY_COOKIES_FILE = os.path.join(STEAM_AUTH_DIR, "steam_community_cookies.pkl")
STEAM_LOGIN_CONFIG = os.path.join(STEAM_AUTH_DIR, "login_config.json")

# 定义域名常量
STEAM_STORE_DOMAIN = ".steampowered.com"
STEAM_COMMUNITY_DOMAIN = ".steamcommunity.com"
AUTH_DIR_MODE = 0o700
COOKIE_FILE_MODE = 0o600


def set_data_dir(data_dir):
    """运行时注入插件数据目录，避免 cookie 文件落到错误工作目录。"""
    if not data_dir:
        return

    global PLUGIN_DATA_DIR, STEAM_AUTH_DIR, STEAM_COOKIES_JSON_FILE
    global STEAM_STORE_COOKIES_FILE, STEAM_COMMUNITY_COOKIES_FILE, STEAM_LOGIN_CONFIG

    PLUGIN_DATA_DIR = os.path.abspath(os.fspath(data_dir))
    STEAM_AUTH_DIR = os.path.join(PLUGIN_DATA_DIR, "auth")
    STEAM_COOKIES_JSON_FILE = os.path.join(STEAM_AUTH_DIR, "steam_cookies.json")
    STEAM_STORE_COOKIES_FILE = os.path.join(STEAM_AUTH_DIR, "steam_store_cookies.pkl")
    STEAM_COMMUNITY_COOKIES_FILE = os.path.join(STEAM_AUTH_DIR, "steam_community_cookies.pkl")
    STEAM_LOGIN_CONFIG = os.path.join(STEAM_AUTH_DIR, "login_config.json")


def ensure_auth_dir():
    """确保认证目录存在"""
    os.makedirs(STEAM_AUTH_DIR, exist_ok=True)
    restrict_path_permissions(STEAM_AUTH_DIR, is_dir=True)


def restrict_path_permissions(path, is_dir=False):
    """尽力收紧认证目录/文件权限，降低 cookies 被同机其他用户读取的风险。"""
    if not path or not os.path.exists(path):
        return
    try:
        mode = AUTH_DIR_MODE if is_dir else COOKIE_FILE_MODE
        os.chmod(path, mode)
    except Exception as e:
        logger.warning(f"收紧 Steam 认证文件权限失败: {path}, {e}")


def write_json_securely(path, data):
    """原子写入 JSON，并在落盘前后收紧文件权限。"""
    directory = os.path.dirname(path)
    ensure_auth_dir()
    fd = None
    temp_path = None

    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".steam_cookies_",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        restrict_path_permissions(temp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        restrict_path_permissions(temp_path)
        os.replace(temp_path, path)
        temp_path = None
        restrict_path_permissions(path)
        return True
    except Exception as e:
        logger.error(f"安全写入 cookies 数据失败: {e}")
        return False
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                logger.warning(f"清理临时 cookies 文件失败: {temp_path}, {e}")

def migrate_from_pickle():
    """从旧版的pickle文件迁移到JSON格式"""
    if not os.path.exists(STEAM_COOKIES_JSON_FILE) and (
        os.path.exists(STEAM_STORE_COOKIES_FILE) or 
        os.path.exists(STEAM_COMMUNITY_COOKIES_FILE)
    ):
        logger.info("检测到旧版 cookies 文件，正在迁移到 JSON 格式")
        cookies_data = {
            "store": {},
            "community": {},
            "config": {
                "enabled": False,
                "store_last_update": None,
                "community_last_update": None
            }
        }
        
        # 迁移配置
        if os.path.exists(STEAM_LOGIN_CONFIG):
            try:
                with open(STEAM_LOGIN_CONFIG, "r", encoding="utf-8") as f:
                    cookies_data["config"] = json.load(f)
            except Exception as e:
                logger.warning(f"迁移配置文件失败: {e}")
        
        # 迁移商店cookies
        if os.path.exists(STEAM_STORE_COOKIES_FILE):
            try:
                with open(STEAM_STORE_COOKIES_FILE, "rb") as f:
                    cookies_data["store"] = pickle.load(f)
                logger.info("商店 cookies 迁移成功")
            except Exception as e:
                logger.warning(f"商店 cookies 迁移失败: {e}")
        
        # 迁移社区cookies
        if os.path.exists(STEAM_COMMUNITY_COOKIES_FILE):
            try:
                with open(STEAM_COMMUNITY_COOKIES_FILE, "rb") as f:
                    cookies_data["community"] = pickle.load(f)
                logger.info("社区 cookies 迁移成功")
            except Exception as e:
                logger.warning(f"社区 cookies 迁移失败: {e}")
        
        # 保存为JSON格式
        save_cookies_data(cookies_data)
        logger.info("迁移完成，数据已保存为 JSON 格式")
        
        # 可选：备份并删除旧文件
        try:
            if os.path.exists(STEAM_STORE_COOKIES_FILE):
                os.rename(STEAM_STORE_COOKIES_FILE, f"{STEAM_STORE_COOKIES_FILE}.bak")
            if os.path.exists(STEAM_COMMUNITY_COOKIES_FILE):
                os.rename(STEAM_COMMUNITY_COOKIES_FILE, f"{STEAM_COMMUNITY_COOKIES_FILE}.bak")
            if os.path.exists(STEAM_LOGIN_CONFIG):
                os.rename(STEAM_LOGIN_CONFIG, f"{STEAM_LOGIN_CONFIG}.bak")
            logger.info("旧 cookies 文件已备份")
        except Exception as e:
            logger.warning(f"备份旧文件失败: {e}")

def get_cookies_data():
    """获取所有cookie数据"""
    ensure_auth_dir()
    migrate_from_pickle()  # 检查并迁移旧数据
    
    if not os.path.exists(STEAM_COOKIES_JSON_FILE):
        # 初始化默认数据结构
        default_data = {
            "store": {},
            "community": {},
            "config": {
                "enabled": False,
                "store_last_update": None,
                "community_last_update": None
            }
        }
        save_cookies_data(default_data)
        return default_data
    
    try:
        restrict_path_permissions(STEAM_COOKIES_JSON_FILE)
        with open(STEAM_COOKIES_JSON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取 cookies 数据失败: {e}")
        # 出错时返回默认数据
        return {
            "store": {},
            "community": {},
            "config": {
                "enabled": False,
                "store_last_update": None,
                "community_last_update": None
            }
        }

def save_cookies_data(data):
    """保存所有cookie数据"""
    return write_json_securely(STEAM_COOKIES_JSON_FILE, data)

def get_login_status():
    """获取当前登录状态配置"""
    data = get_cookies_data()
    return data["config"]

def save_login_config(config):
    """保存登录状态配置"""
    data = get_cookies_data()
    data["config"] = config
    return save_cookies_data(data)

def enable_steam_login():
    """启用Steam登录功能"""
    data = get_cookies_data()
    data["config"]["enabled"] = True
    return save_cookies_data(data)

def disable_steam_login():
    """禁用Steam登录功能"""
    data = get_cookies_data()
    data["config"]["enabled"] = False
    return save_cookies_data(data)

def parse_cookies_string(cookies_str):
    """
    将cookies字符串解析为字典
    参数:
    - cookies_str: 用户输入的cookies字符串，通常为 name=value; name2=value2; 格式
    返回:
    - dict: 解析后的cookies字典
    """
    cookies_dict = {}
    for cookie in cookies_str.split(';'):
        if not cookie.strip():
            continue
        parts = cookie.strip().split('=', 1)
        if len(parts) != 2:  # 跳过无效的cookie
            continue
        name, value = parts
        cookies_dict[name.strip()] = value.strip()
    return cookies_dict

def save_steam_cookies(cookies_str, domain_type="store"):
    """
    保存Steam Cookies
    参数:
    - cookies_str: 用户输入的cookies字符串，通常为 name=value; name2=value2; 格式
    - domain_type: 域名类型，"store" 或 "community"
    返回:
    - (bool, str): 成功与否及提示信息
    """
    ensure_auth_dir()
    
    # 选择正确的域名类型和配置键
    if domain_type == "store":
        config_key = "store_last_update"
        domain_name = "商店(Store)"
    elif domain_type == "community":
        config_key = "community_last_update"
        domain_name = "社区(Community)"
    else:
        return False, f"❌ 不支持的域名类型: {domain_type}"
    
    try:
        # 解析cookies字符串
        cookies_dict = parse_cookies_string(cookies_str)
        
        # 检查是否包含必要的Steam身份验证cookie
        if 'steamLoginSecure' not in cookies_dict:
            return False, f"❌ 缺少必要的Steam {domain_name} Cookie: steamLoginSecure"
        
        # 尝试从steamLoginSecure中提取steamid (可选)
        if 'steamLoginSecure' in cookies_dict:
            # steamLoginSecure通常格式为: steamid%7C%7Ctoken
            # %7C 是 | 的URL编码
            match = re.match(r'(\d+)(?:%7C%7C|\|\|)', cookies_dict['steamLoginSecure'])
            if match:
                steamid = match.group(1)
                if steamid and 'steamid' not in cookies_dict:
                    cookies_dict['steamid'] = steamid
                    logger.info(f"从 {domain_name} steamLoginSecure 中提取到 steamid: {steamid}")
        
        # 获取当前数据并更新
        data = get_cookies_data()
        data[domain_type] = cookies_dict
        data["config"][config_key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["config"]["enabled"] = True  # 自动启用登录
        
        # 保存更新后的数据
        if save_cookies_data(data):
            return True, f"✅ Steam {domain_name} 登录信息已保存，已启用登录功能"
        else:
            return False, f"❌ 保存Steam {domain_name} Cookie失败"
    except Exception as e:
        return False, f"❌ 保存Steam {domain_name} Cookie失败: {e}"

def load_steam_cookies(domain_type="store"):
    """
    加载Steam Cookies
    参数:
    - domain_type: 域名类型，"store" 或 "community"
    返回:
    - 字典形式的cookies或None
    """
    if domain_type not in ["store", "community"]:
        logger.error(f"不支持的域名类型: {domain_type}")
        return None
    
    data = get_cookies_data()
    cookies = data.get(domain_type, {})
    
    # 检查是否为空
    if not cookies:
        return None
    
    return cookies

def apply_cookies_to_driver(driver, url=None):
    """
    将保存的cookies应用到WebDriver，根据URL自动选择应用的cookie域
    参数:
    - driver: Selenium WebDriver实例
    - url: 目标URL，用于确定应用哪个域的cookies
    返回:
    - bool: 是否成功应用了cookies
    """
    data = get_cookies_data()
    if not data["config"]["enabled"]:
        return False
    
    # 根据URL确定域名类型
    domain_type = "store"
    domain = STEAM_STORE_DOMAIN
    
    if url and "steamcommunity.com" in url:
        domain_type = "community"
        domain = STEAM_COMMUNITY_DOMAIN
    
    # 加载对应域名的cookies
    cookies = data.get(domain_type, {})
    if not cookies:
        logger.warning(f"未找到 {domain_type} 域的 cookies")
        return False
    
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        cookies_added = 0
        for name, value in cookies.items():
            try:
                result = driver.execute_cdp_cmd("Network.setCookie", {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                })
                if result.get("success", False):
                    cookies_added += 1
                else:
                    logger.warning(f"CDP 注入 {domain_type} cookie '{name}' 未返回成功")
            except Exception as e:
                logger.warning(f"CDP 注入 {domain_type} cookie '{name}' 失败: {e}")

        logger.info(f"已通过 CDP 注入 {cookies_added} 个 {domain_type} cookies")
        return cookies_added > 0
    except Exception as e:
        logger.warning(f"CDP 注入 Steam {domain_type} Cookie 失败，回退到 Selenium add_cookie: {e}")

    try:
        initial_url = "https://store.steampowered.com" if domain_type == "store" else "https://steamcommunity.com"
        driver.get(initial_url)

        current_url = driver.current_url.lower()
        if (domain_type == "store" and "steampowered.com" not in current_url) or \
           (domain_type == "community" and "steamcommunity.com" not in current_url):
            logger.warning(f"当前URL ({current_url}) 与目标域名 ({domain_type}) 不匹配")
            return False

        cookies_added = 0
        for name, value in cookies.items():
            try:
                driver.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "secure": True,
                })
                cookies_added += 1
            except Exception as e:
                logger.warning(f"添加 {domain_type} cookie '{name}' 失败: {e}")

        logger.info(f"已应用 {cookies_added} 个 {domain_type} cookies")
        return cookies_added > 0
    except Exception as e:
        logger.error(f"应用 Steam {domain_type} Cookie 失败: {e}")
        return False

def get_cookie_status():
    """获取当前cookie状态信息"""
    data = get_cookies_data()
    config = data["config"]
    store_cookies = data.get("store", {})
    community_cookies = data.get("community", {})
    
    if not config["enabled"]:
        return "🔴 当前未启用Steam登录"
    
    status_lines = []
    
    # 检查是否有任何cookies
    if not store_cookies and not community_cookies:
        return "⚠️ 已启用Steam登录，但未找到任何有效的Cookie"
    
    # 商店cookies状态
    if store_cookies:
        store_login_secure = store_cookies.get('steamLoginSecure', None)
        store_steamid = store_cookies.get('steamid', None)
        store_update = config.get("store_last_update", "未知")
        
        store_status = f"🟢 Steam商店(Store)登录已配置 (更新: {store_update})"
        if store_login_secure:
            store_status += "\n   ✓ 已保存steamLoginSecure"
        else:
            store_status += "\n   ⚠️ 未找到steamLoginSecure"
            
        if store_steamid:
            store_status += f"\n   ✓ steamid: {store_steamid}"
        
        store_status += f"\n   📝 共 {len(store_cookies)} 个cookies"
        status_lines.append(store_status)
    else:
        status_lines.append("⚠️ 未配置Steam商店(Store)登录")
    
    # 社区cookies状态
    if community_cookies:
        community_login_secure = community_cookies.get('steamLoginSecure', None)
        community_steamid = community_cookies.get('steamid', None)
        community_update = config.get("community_last_update", "未知")
        
        community_status = f"🟢 Steam社区(Community)登录已配置 (更新: {community_update})"
        if community_login_secure:
            community_status += "\n   ✓ 已保存steamLoginSecure"
        else:
            community_status += "\n   ⚠️ 未找到steamLoginSecure"
            
        if community_steamid:
            community_status += f"\n   ✓ steamid: {community_steamid}"
        
        community_status += f"\n   📝 共 {len(community_cookies)} 个cookies"
        status_lines.append(community_status)
    else:
        status_lines.append("⚠️ 未配置Steam社区(Community)登录")
    
    return "\n\n".join(status_lines)

def verify_steam_login(driver, domain_type="store"):
    """
    验证Steam登录状态是否有效
    参数:
    - driver: Selenium WebDriver实例
    - domain_type: 域名类型，"store" 或 "community"
    返回:
    - (bool, str): 登录状态和用户名(如有)
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import time
    
    try:
        # 访问对应的Steam页面
        if domain_type == "store":
            driver.get("https://store.steampowered.com/")
            domain_name = "商店(Store)"
        else:
            driver.get("https://steamcommunity.com/")
            domain_name = "社区(Community)"
            
        time.sleep(2)
        
        # 尝试从cookies中提取steamid以获取更多信息
        steam_id = None
        for cookie in driver.get_cookies():
            if cookie['name'] == 'steamid':
                steam_id = cookie['value']
                break
        
        # 首先尝试通用的获取用户名方法（适用于两个域）
        # 方法1: 检查顶部导航栏中的账户名元素
        try:
            account_menu = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "account_pulldown"))
            )
            if account_menu:
                username = account_menu.text.strip()
                if username and username not in ["登录", "Sign In", "登入", "Connexion", "Anmelden"]:
                    return True, username
        except Exception:
            pass
        
        # 特定于商店页面的检查
        if domain_type == "store":            
            # 方法2: 检查账户下拉菜单中是否有"查看个人资料"链接
            try:
                driver.find_element(By.ID, "account_pulldown").click()
                time.sleep(1)
                
                profile_links = driver.find_elements(By.XPATH, 
                    "//a[contains(@href, '/profiles/') or contains(@href, '/id/')]")
                
                if profile_links:
                    for link in profile_links:
                        if "profile" in link.get_attribute("href").lower():
                            # 尝试获取个人资料中的用户名
                            try:
                                driver.get(link.get_attribute("href"))
                                time.sleep(2)
                                name_element = driver.find_element(By.CLASS_NAME, "actual_persona_name")
                                if name_element:
                                    return True, name_element.text.strip()
                            except Exception:
                                pass
                            return True, f"{domain_name}已登录 (未获取到用户名)"
            except Exception:
                pass
        
        # 特定于社区页面的检查
        else:
            # 如果有steamid，尝试直接访问个人资料页面获取用户名
            if steam_id:
                try:
                    driver.get(f"https://steamcommunity.com/profiles/{steam_id}")
                    time.sleep(2)
                    name_element = driver.find_element(By.CLASS_NAME, "actual_persona_name")
                    if name_element:
                        return True, name_element.text.strip()
                except Exception:
                    pass
            
            # 尝试从社区页面上找到个人资料链接
            try:
                profile_links = driver.find_elements(By.XPATH, 
                    "//a[contains(@href, '/profiles/') or contains(@href, '/id/')]")
                
                if profile_links:
                    for link in profile_links:
                        if "myprofile" in link.get_attribute("href").lower() or "my/profile" in link.get_attribute("href").lower():
                            try:
                                driver.get(link.get_attribute("href"))
                                time.sleep(2)
                                name_element = driver.find_element(By.CLASS_NAME, "actual_persona_name")
                                if name_element:
                                    return True, name_element.text.strip()
                            except Exception:
                                pass
            except Exception:
                pass
                
            # 检查社区页面上的其他用户名指示器
            try:
                user_panel = driver.find_element(By.ID, "global_header")
                if user_panel:
                    user_links = user_panel.find_elements(By.XPATH, ".//a[contains(@class, 'username')]")
                    if user_links and len(user_links) > 0:
                        return True, user_links[0].text.strip()
            except Exception:
                pass
                
            # 查找社区页面上的steamcommunity_header
            try:
                header_element = driver.find_element(By.ID, "steamcommunity_header")
                if header_element:
                    persona_links = header_element.find_elements(By.XPATH, ".//span[contains(@class, 'persona')]")
                    if persona_links and len(persona_links) > 0:
                        return True, persona_links[0].text.strip()
            except Exception:
                pass
        
        # 通用方法: 检查是否有登出按钮
        try:
            logout_links = driver.find_elements(By.XPATH, 
                "//a[contains(@href, 'logout')]")
            
            if logout_links:
                # 如果找到登出按钮，但没找到用户名，尝试从cookies查找steamLoginSecure
                for cookie in driver.get_cookies():
                    if cookie['name'] == 'steamLoginSecure':
                        # 尝试从steamLoginSecure提取steamid
                        import re
                        match = re.match(r'(\d+)(?:%7C%7C|\|\|)', cookie['value'])
                        if match:
                            steam_id = match.group(1)
                            # 如果是社区域，尝试访问个人资料页面
                            if domain_type == "community" and steam_id:
                                try:
                                    driver.get(f"https://steamcommunity.com/profiles/{steam_id}")
                                    time.sleep(2)
                                    name_element = driver.find_element(By.CLASS_NAME, "actual_persona_name")
                                    if name_element:
                                        return True, name_element.text.strip()
                                except Exception:
                                    pass
                        # 如果没有成功，但至少我们确认已登录
                        return True, f"{domain_name}已登录 (从steamLoginSecure确认)"
                return True, f"{domain_name}已登录 (通过登出按钮确认)"
        except Exception:
            pass
        
        # 通用方法: 检查页面源代码中是否包含某些只有登录用户才会有的标记
        page_source = driver.page_source.lower()
        if "account_name" in page_source or "accountname" in page_source:
            # 尝试从页面源码提取用户名
            import re
            match = re.search(r'account_name["\s:>]+([^<>"]+)', page_source)
            if match:
                return True, match.group(1)
                
            # 另一种模式，特别是社区页面
            match = re.search(r'class="persona\s+([^"]+)"', page_source)
            if match:
                return True, match.group(1)
                
            # 如果我们有steamid，尝试通过直接访问个人资料页面获取名称
            if steam_id and domain_type == "community":
                try:
                    driver.get(f"https://steamcommunity.com/profiles/{steam_id}")
                    time.sleep(2)
                    name_element = driver.find_element(By.CLASS_NAME, "actual_persona_name")
                    if name_element:
                        return True, name_element.text.strip()
                except Exception:
                    pass
                    
            return True, f"{domain_name}已登录 (通过页面源码确认)"
            
        return False, f"{domain_name}未登录"
    except Exception as e:
        logger.error(f"验证 Steam {domain_name} 登录状态失败: {e}")
        return False, f"{domain_name}验证失败: {str(e)}"

async def test_steam_login():
    """测试Steam登录状态"""
    store_driver = None
    community_driver = None
    try:
        # 测试商店登录
        store_driver = create_chrome_webdriver()
        store_success = apply_cookies_to_driver(store_driver, "https://store.steampowered.com")
        store_status, store_username = verify_steam_login(store_driver, "store")
        
        # 测试社区登录 - 使用新的driver实例
        community_driver = create_chrome_webdriver()
        community_success = apply_cookies_to_driver(community_driver, "https://steamcommunity.com")
        community_status, community_username = verify_steam_login(community_driver, "community")
        
        # 整合结果
        result_lines = []
        
        # 清理用户名中的状态描述文本
        if store_status:
            # 清理可能混入的状态描述
            clean_store_username = store_username
            if "已登录" in store_username:
                clean_store_username = "获取用户名失败"
            result_lines.append(f"✅ Steam商店(Store)登录成功! 用户名: {clean_store_username}")
        else:
            store_cookies = load_steam_cookies("store")
            if store_cookies and 'steamLoginSecure' in store_cookies:
                result_lines.append(f"❌ Steam商店(Store)登录失败: Cookie可能已过期或无效。{store_username}")
            else:
                result_lines.append(f"❌ Steam商店(Store)登录失败: 缺少必要的Cookie。{store_username}")
        
        # 同样清理社区用户名
        if community_status:
            clean_community_username = community_username
            if "已登录" in community_username:
                clean_community_username = "获取用户名失败"
            result_lines.append(f"✅ Steam社区(Community)登录成功! 用户名: {clean_community_username}")
        else:
            community_cookies = load_steam_cookies("community")
            if community_cookies and 'steamLoginSecure' in community_cookies:
                result_lines.append(f"❌ Steam社区(Community)登录失败: Cookie可能已过期或无效。{community_username}")
            else:
                result_lines.append(f"❌ Steam社区(Community)登录失败: 缺少必要的Cookie。{community_username}")
        
        return "\n".join(result_lines)
    except Exception as e:
        return f"❌ 测试Steam登录出错: {e}"
    finally:
        if store_driver:
            store_driver.quit()
        if community_driver:
            community_driver.quit()

# 新增函数：从配置加载和设置登录信息
def load_from_config(config):
    """
    从配置对象加载Steam登录设置
    参数:
    - config: AstrBot插件配置对象
    返回:
    - bool: 是否成功加载
    """
    try:
        # 读取登录开关
        enable_login = config.get("enable_steam_login", False)
        
        # 读取商店cookies
        store_cookies = config.get("steam_store_cookies", "")
        if store_cookies:
            save_steam_cookies(store_cookies, "store")
        
        # 读取社区cookies
        community_cookies = config.get("steam_community_cookies", "")
        if community_cookies:
            save_steam_cookies(community_cookies, "community")
        
        # 设置登录状态
        if enable_login:
            enable_steam_login()
        else:
            disable_steam_login()
        
        return True
    except Exception as e:
        logger.error(f"从配置加载 Steam 登录设置失败: {e}")
        return False
