import os
import re
import shutil
import subprocess
import sys

from astrbot.api import logger
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

try:
    from webdriver_manager.core.os_manager import ChromeType
except Exception:
    ChromeType = None


DEFAULT_PLUGIN_DATA_DIR = "./data/plugins/astrbot_plugin_steamshot"
PLUGIN_DATA_DIR = os.getenv("STEAMSHOT_DATA_DIR", DEFAULT_PLUGIN_DATA_DIR)
RUNTIME_DIR = os.path.join(PLUGIN_DATA_DIR, "runtime")
CHROMEDRIVER_PATH_FILE = os.path.join(RUNTIME_DIR, "chromedriver_path.txt")
LEGACY_CHROMEDRIVER_PATH_FILE = "./chromedriver_path.txt"

ENV_BROWSER_BIN = "STEAMSHOT_BROWSER_BIN"
ENV_CHROMEDRIVER_BIN = "STEAMSHOT_CHROMEDRIVER_BIN"
ENV_BROWSER_KIND = "STEAMSHOT_BROWSER_KIND"
ENV_SKIP_WDM = "STEAMSHOT_SKIP_WDM"

VALID_BROWSER_KINDS = {"auto", "chrome", "chromium"}


def set_data_dir(data_dir):
    """运行时注入插件数据目录，避免依赖当前工作目录。"""
    if not data_dir:
        return

    global PLUGIN_DATA_DIR, RUNTIME_DIR, CHROMEDRIVER_PATH_FILE
    PLUGIN_DATA_DIR = os.path.abspath(os.fspath(data_dir))
    RUNTIME_DIR = os.path.join(PLUGIN_DATA_DIR, "runtime")
    CHROMEDRIVER_PATH_FILE = os.path.join(RUNTIME_DIR, "chromedriver_path.txt")


def _is_windows():
    return sys.platform.startswith("win")


def _is_truthy(value):
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_browser_kind(value):
    kind = (value or "auto").strip().lower()
    if kind not in VALID_BROWSER_KINDS:
        return "auto"
    return kind


def _ensure_runtime_dir():
    os.makedirs(RUNTIME_DIR, exist_ok=True)


def _extract_version(text):
    if not text:
        return None
    match = re.search(r"(\d+\.\d+\.\d+\.\d+|\d+\.\d+\.\d+)", text)
    if not match:
        return None
    return match.group(1)


def _extract_major_version(version):
    if not version:
        return None
    try:
        return version.split(".")[0]
    except Exception:
        return None


def _get_browser_version_from_binary(browser_binary):
    if not browser_binary:
        return None
    try:
        result = subprocess.run(
            [browser_binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return _extract_version(result.stdout or result.stderr)
    except Exception:
        return None


def _get_windows_chrome_version():
    if not _is_windows():
        return None
    try:
        import winreg  # pylint: disable=import-outside-toplevel

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Google\\Chrome\\BLBeacon")
        version, _ = winreg.QueryValueEx(key, "version")
        return version
    except Exception:
        return None


def _get_chromedriver_version(chromedriver_path):
    if not chromedriver_path:
        return None
    try:
        result = subprocess.run(
            [chromedriver_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return _extract_version(result.stdout or result.stderr)
    except Exception:
        return None


def _is_driver_compatible(browser_version, driver_version):
    browser_major = _extract_major_version(browser_version)
    driver_major = _extract_major_version(driver_version)
    if not browser_major or not driver_major:
        return True
    return browser_major == driver_major


def _read_cached_driver_path():
    for path_file in (CHROMEDRIVER_PATH_FILE, LEGACY_CHROMEDRIVER_PATH_FILE):
        if not os.path.exists(path_file):
            continue
        try:
            with open(path_file, "r", encoding="utf-8") as f:
                cached_path = f.read().strip()
            if cached_path and os.path.exists(cached_path):
                return cached_path
        except Exception:
            continue
    return None


def _write_cached_driver_path(driver_path):
    if not driver_path:
        return
    try:
        _ensure_runtime_dir()
        with open(CHROMEDRIVER_PATH_FILE, "w", encoding="utf-8") as f:
            f.write(driver_path)
    except Exception as e:
        logger.warning(f"保存 ChromeDriver 缓存路径失败: {e}")


def resolve_browser_binary():
    env_browser = os.getenv(ENV_BROWSER_BIN, "").strip()
    if env_browser:
        if os.path.exists(env_browser):
            logger.info(f"使用环境变量指定浏览器: {env_browser}")
            return env_browser
        logger.warning(f"环境变量 {ENV_BROWSER_BIN} 指定路径不存在: {env_browser}")

    browser_kind = _normalize_browser_kind(os.getenv(ENV_BROWSER_KIND))
    command_candidates = {
        "auto": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"],
        "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
        "chromium": ["chromium", "chromium-browser"],
    }
    for command in command_candidates[browser_kind]:
        resolved = shutil.which(command)
        if resolved:
            logger.info(f"检测到浏览器可执行文件: {resolved}")
            return resolved

    if sys.platform == "darwin":
        mac_chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(mac_chrome):
            logger.info(f"检测到浏览器可执行文件: {mac_chrome}")
            return mac_chrome

    logger.warning("未检测到浏览器可执行文件，将使用 Selenium 默认浏览器解析策略")
    return None


def _resolve_browser_version(browser_binary):
    version = _get_browser_version_from_binary(browser_binary)
    if version:
        return version
    return _get_windows_chrome_version()


def _resolve_env_chromedriver():
    env_driver = os.getenv(ENV_CHROMEDRIVER_BIN, "").strip()
    if not env_driver:
        return None
    if os.path.exists(env_driver):
        logger.info(f"使用环境变量指定驱动: {env_driver}")
        return env_driver
    logger.warning(f"环境变量 {ENV_CHROMEDRIVER_BIN} 指定路径不存在: {env_driver}")
    return None


def _resolve_system_chromedriver():
    system_driver = shutil.which("chromedriver")
    if system_driver and os.path.exists(system_driver):
        logger.info(f"使用系统驱动: {system_driver}")
        return system_driver
    return None


def _download_chromedriver(browser_kind):
    skip_wdm = _is_truthy(os.getenv(ENV_SKIP_WDM))
    if skip_wdm:
        return None

    logger.warning("未找到可用 ChromeDriver，开始使用 webdriver-manager 下载")
    if browser_kind == "chromium" and ChromeType is not None:
        return ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
    return ChromeDriverManager().install()


def resolve_chromedriver_path(browser_binary=None):
    browser_kind = _normalize_browser_kind(os.getenv(ENV_BROWSER_KIND))
    if browser_kind == "auto" and browser_binary:
        binary_name = os.path.basename(browser_binary).lower()
        if "chromium" in binary_name:
            browser_kind = "chromium"
        else:
            browser_kind = "chrome"

    browser_version = _resolve_browser_version(browser_binary)
    logger.info(f"检测到浏览器版本: {browser_version or '未知'}")

    env_driver = _resolve_env_chromedriver()
    if env_driver:
        return env_driver

    driver_candidates = []
    system_driver = _resolve_system_chromedriver()
    if system_driver:
        driver_candidates.append(system_driver)

    cached_driver = _read_cached_driver_path()
    if cached_driver:
        driver_candidates.append(cached_driver)

    checked = set()
    for candidate in driver_candidates:
        if candidate in checked:
            continue
        checked.add(candidate)
        driver_version = _get_chromedriver_version(candidate)
        if _is_driver_compatible(browser_version, driver_version):
            logger.info(f"使用本地 ChromeDriver: {candidate} (版本: {driver_version or '未知'})")
            return candidate
        logger.warning(
            f"⚠️ 驱动版本可能不匹配，跳过: {candidate} "
            f"(浏览器: {browser_version or '未知'}, 驱动: {driver_version or '未知'})"
        )

    try:
        downloaded = _download_chromedriver(browser_kind)
        if downloaded:
            logger.info(f"已下载并缓存 ChromeDriver: {downloaded}")
            _write_cached_driver_path(downloaded)
            return downloaded
    except Exception as e:
        logger.warning(f"webdriver-manager 下载失败: {e}")

    if driver_candidates:
        fallback = driver_candidates[0]
        logger.warning(f"回退使用本地驱动: {fallback}")
        return fallback

    raise RuntimeError(
        "无法解析 ChromeDriver。请设置环境变量 STEAMSHOT_CHROMEDRIVER_BIN，"
        "或安装系统 chromedriver，或允许 webdriver-manager 下载。"
    )


def create_chrome_webdriver():
    browser_binary = resolve_browser_binary()
    chromedriver_path = resolve_chromedriver_path(browser_binary=browser_binary)

    options = Options()
    options.page_load_strategy = "eager"
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-usb-device-detection")
    options.add_argument("--lang=zh-CN")
    options.add_argument("--log-level=3")
    options.add_argument("--silent")
    options.add_experimental_option(
        "excludeSwitches",
        ["enable-logging", "enable-automation", "disable-usb", "enable-devtools"],
    )
    options.add_experimental_option("prefs", {"intl.accept_languages": "zh-CN,zh;q=0.9"})
    if browser_binary:
        options.binary_location = browser_binary

    service = Service(chromedriver_path)
    if _is_windows():
        service.creation_flags = 0x08000000
    try:
        service.log_output = subprocess.DEVNULL
    except Exception:
        pass

    return webdriver.Chrome(service=service, options=options)
