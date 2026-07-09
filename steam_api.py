import asyncio
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup


STEAM_API_TIMEOUT = 8
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


class SteamAPIError(Exception):
    """Steam API 请求或解析失败。"""


def extract_appid_from_url(url):
    match = re.search(r"/app/(\d+)", url or "")
    return match.group(1) if match else None


def extract_profile_steamid_from_url(url):
    match = re.search(r"steamcommunity\.com/profiles/(\d{17})", url or "")
    return match.group(1) if match else None


def extract_profile_vanity_from_url(url):
    match = re.search(r"steamcommunity\.com/id/([^/?#]+)", url or "")
    return match.group(1) if match else None


def _get_json(url, params):
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=STEAM_API_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _fetch_appdetails(appid, lang="schinese", cc="cn"):
    data = _get_json(
        "https://store.steampowered.com/api/appdetails",
        {"appids": appid, "l": lang, "cc": cc},
    )
    app_data = data.get(str(appid), {})
    if not app_data.get("success"):
        raise SteamAPIError(f"Steam appdetails 未返回有效数据: {appid}")
    return app_data.get("data") or {}


def _fetch_appreviews(appid, lang="schinese"):
    data = _get_json(
        f"https://store.steampowered.com/appreviews/{appid}",
        {
            "json": 1,
            "language": lang,
            "purchase_type": "all",
            "num_per_page": 0,
        },
    )
    return data.get("query_summary") or {}


def _fetch_current_players(appid):
    data = _get_json(
        "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/",
        {"appid": appid},
    )
    response = data.get("response") or {}
    if "player_count" not in response:
        return None
    return int(response["player_count"])


def _resolve_vanity_url(vanity, api_key):
    data = _get_json(
        "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
        {"key": api_key, "vanityurl": vanity},
    )
    response = data.get("response") or {}
    if int(response.get("success") or 0) != 1 or not response.get("steamid"):
        raise SteamAPIError(f"无法解析 Steam 自定义 URL: {vanity}")
    return response["steamid"]


def _fetch_friend_list(steamid, api_key):
    data = _get_json(
        "https://api.steampowered.com/ISteamUser/GetFriendList/v1/",
        {
            "key": api_key,
            "steamid": steamid,
            "relationship": "friend",
        },
    )
    friends = (data.get("friendslist") or {}).get("friends")
    if friends is None:
        raise SteamAPIError("好友列表不可见或 Steam API 未返回有效数据")
    return friends


def _fetch_player_summaries(steamids, api_key):
    if not steamids:
        return []
    data = _get_json(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
        {
            "key": api_key,
            "steamids": ",".join(steamids),
        },
    )
    return (data.get("response") or {}).get("players") or []


def _fetch_player_bans(steamids, api_key):
    if not steamids:
        return []
    data = _get_json(
        "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/",
        {
            "key": api_key,
            "steamids": ",".join(steamids),
        },
    )
    return data.get("players") or []


def _fetch_steam_level(steamid, api_key):
    data = _get_json(
        "https://api.steampowered.com/IPlayerService/GetSteamLevel/v1/",
        {
            "key": api_key,
            "steamid": steamid,
        },
    )
    response = data.get("response") or {}
    if response.get("player_level") is None:
        return None
    return int(response["player_level"])


def _fetch_owned_game_count(steamid, api_key):
    data = _get_json(
        "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/",
        {
            "key": api_key,
            "steamid": steamid,
            "include_played_free_games": 1,
            "include_appinfo": 0,
        },
    )
    response = data.get("response") or {}
    if response.get("game_count") is not None:
        return int(response["game_count"])
    games = response.get("games")
    if isinstance(games, list):
        return len(games)
    return None


def _fetch_recently_played_games(steamid, api_key, limit=3):
    data = _get_json(
        "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v0001/",
        {
            "key": api_key,
            "steamid": steamid,
            "count": limit,
        },
    )
    return data.get("response") or {}


def _strip_html(value):
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _format_price(details):
    if details.get("is_free"):
        return "\n免费游戏"

    price = details.get("price_overview") or {}
    if not price:
        return "\n暂无售价"

    final_price = price.get("final_formatted") or ""
    initial_price = price.get("initial_formatted") or ""
    discount_percent = price.get("discount_percent") or 0

    if discount_percent and initial_price and final_price:
        return f"\n-{discount_percent}%   {initial_price} -> {final_price}"
    if final_price:
        return f"\n{final_price}"
    return "\n暂无售价"


def _format_review_summary(summary):
    total_reviews = int(summary.get("total_reviews") or 0)
    total_positive = int(summary.get("total_positive") or 0)
    score_desc = summary.get("review_score_desc") or "暂无评分"

    if total_reviews <= 0:
        return score_desc

    positive_percent = round(total_positive * 100 / total_reviews)
    return f"{score_desc} ({positive_percent}% / {total_reviews:,} 篇)"


def _format_chinese_support(details):
    supported_languages = _strip_html(details.get("supported_languages"))
    if not supported_languages:
        return "未知"

    has_simplified = "简体中文" in supported_languages or "Simplified Chinese" in supported_languages
    has_traditional = "繁体中文" in supported_languages or "Traditional Chinese" in supported_languages

    supported = []
    if has_simplified:
        supported.append("简体中文")
    if has_traditional:
        supported.append("繁体中文")

    return " / ".join(supported) if supported else "不支持"


def _format_player_count(player_count):
    if player_count is None:
        return None
    return f"{player_count:,}"


def _format_persona_state(state):
    state = _persona_state_value(state)
    states = {
        0: "离线",
        1: "在线",
        2: "忙碌",
        3: "离开",
        4: "打盹",
        5: "想交易",
        6: "想玩",
    }
    return states.get(state, "未知")


def _to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_timestamp(value):
    timestamp = _to_int(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _format_minutes(minutes):
    minutes = _to_int(minutes)
    if not minutes or minutes <= 0:
        return None
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes / 60
    if hours < 10:
        return f"{hours:.1f} 小时"
    return f"{round(hours):,} 小时"


def _format_profile_status(player):
    if player.get("gameextrainfo"):
        return f"当前状态: 当前正在游戏 \n                     {player['gameextrainfo']}"

    state = _persona_state_value(player.get("personastate"))
    if state == 0:
        return "当前状态: 当前离线"
    if state == 1:
        return "当前状态: 当前在线"
    return f"当前状态: {_format_persona_state(state)}"


def _format_profile_location(player):
    parts = [
        player.get("loccountrycode"),
        player.get("locstatecode"),
        str(player.get("loccityid")) if player.get("loccityid") else None,
    ]
    return " / ".join([part for part in parts if part])


def _format_ban_summary(ban):
    if not isinstance(ban, dict):
        return None

    community_banned = bool(ban.get("CommunityBanned"))
    vac_bans = _to_int(ban.get("NumberOfVACBans"), 0) or 0
    game_bans = _to_int(ban.get("NumberOfGameBans"), 0) or 0
    days_since = _to_int(ban.get("DaysSinceLastBan"), None)
    economy_ban = str(ban.get("EconomyBan") or "none")

    parts = []
    if community_banned:
        parts.append("社区封禁")
    if vac_bans:
        parts.append(f"VAC 封禁 {vac_bans} 次")
    if game_bans:
        parts.append(f"游戏封禁 {game_bans} 次")
    if economy_ban.lower() not in {"none", "no", ""}:
        parts.append(f"经济状态 {economy_ban}")
    if days_since is not None and (vac_bans or game_bans):
        parts.append(f"距上次封禁 {days_since} 天")

    return "，".join(parts) if parts else "无公开封禁记录"


def _format_recent_games(recent_response):
    if not isinstance(recent_response, dict):
        return []

    games = recent_response.get("games") or []
    if not games:
        return []

    total_count = _to_int(recent_response.get("total_count"), len(games)) or len(games)
    lines = [f"🕒 最新动态: 最近两周游玩 {total_count} 款游戏"]
    for game in games[:3]:
        name = game.get("name") or f"App {game.get('appid')}"
        details = []
        recent_playtime = _format_minutes(game.get("playtime_2weeks"))
        total_playtime = _format_minutes(game.get("playtime_forever"))
        if recent_playtime:
            details.append(f"最近两周 {recent_playtime}")
        if total_playtime:
            details.append(f"总时数 {total_playtime}")
        lines.append(f"\n🎮 {name}: {'，'.join(details) if details else '有最近游玩记录'}")

    return lines


def _first_item(items):
    return items[0] if isinstance(items, list) and items else None


def _persona_state_value(state):
    try:
        return int(state)
    except (TypeError, ValueError):
        return 0


def _chunked(values, size):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def build_store_info(appid, details, review_summary=None, current_players=None):
    release_date = details.get("release_date") or {}
    genres = details.get("genres") or []
    metacritic = details.get("metacritic") or {}
    dlc = details.get("dlc") or []

    info = {
        "🎮 游戏名称": details.get("name") or "未知",
        "📅 发行时间": release_date.get("date") or "未知",
        "🏗 开发商": ", ".join(details.get("developers") or []) or "未知",
        "🏛 发行商": ", ".join(details.get("publishers") or []) or "未知",
        "🎭 游戏类别": "，".join([genre.get("description", "") for genre in genres[:5] if genre.get("description")]) or "未知",
        "📜 简介": _strip_html(details.get("short_description")) or "暂无简介",
        "⭐ 评分": _format_review_summary(review_summary or {}),
        "💰 价格": _format_price(details),
        "🌐 中文支持": _format_chinese_support(details),
    }

    if metacritic.get("score"):
        info["🏆 Metacritic"] = str(metacritic["score"])

    player_text = _format_player_count(current_players)
    if player_text:
        info["👥 当前在线"] = player_text

    if dlc:
        info["🧩 DLC 数量"] = str(len(dlc))

    return info


def build_profile_info(
    steamid,
    player,
    level=None,
    owned_game_count=None,
    friend_count=None,
    ban=None,
    recent_response=None,
):
    lines = []
    name = player.get("personaname") or steamid or "未知用户"
    lines.append(f"steam id: {name}")
    if steamid:
        lines.append(f"SteamID64: {steamid}")

    ban_summary = _format_ban_summary(ban)
    if ban_summary:
        lines.append(f"🚫 封禁纪录: {ban_summary}")

    visibility = _to_int(player.get("communityvisibilitystate"), None)
    if visibility is not None and visibility != 3:
        lines.append("此个人资料是私密的")

    if level is not None:
        lines.append(f"steam等级: {level}")

    location = _format_profile_location(player)
    if location:
        lines.append(f"地区: {location}")

    lines.append(_format_profile_status(player))

    if owned_game_count is not None:
        lines.append(f"游戏数: {owned_game_count:,}")
    if friend_count is not None:
        lines.append(f"好友数: {friend_count:,}")

    created_at = _format_timestamp(player.get("timecreated"))
    if created_at:
        lines.append(f"账号创建: {created_at}")

    lines.extend(_format_recent_games(recent_response))
    return lines


async def get_store_api_info(appid):
    details = await asyncio.to_thread(_fetch_appdetails, appid)

    review_result, player_result = await asyncio.gather(
        asyncio.to_thread(_fetch_appreviews, appid),
        asyncio.to_thread(_fetch_current_players, appid),
        return_exceptions=True,
    )

    if isinstance(review_result, Exception):
        review_result = {}
    if isinstance(player_result, Exception):
        player_result = None

    return build_store_info(appid, details, review_result, player_result)


async def get_profile_api_info(profile_url, api_key):
    if not api_key:
        raise SteamAPIError("未配置 Steam Web API Key，无法使用 API 解析个人主页")

    steamid = await resolve_profile_steamid(profile_url, api_key)
    players = await asyncio.to_thread(_fetch_player_summaries, [steamid], api_key)
    player = _first_item(players)
    if not player:
        raise SteamAPIError(f"Steam API 未返回个人资料: {steamid}")

    level_result, ban_result, game_count_result, friend_result, recent_result = await asyncio.gather(
        asyncio.to_thread(_fetch_steam_level, steamid, api_key),
        asyncio.to_thread(_fetch_player_bans, [steamid], api_key),
        asyncio.to_thread(_fetch_owned_game_count, steamid, api_key),
        asyncio.to_thread(_fetch_friend_list, steamid, api_key),
        asyncio.to_thread(_fetch_recently_played_games, steamid, api_key),
        return_exceptions=True,
    )

    level = None if isinstance(level_result, Exception) else level_result
    ban = None if isinstance(ban_result, Exception) else _first_item(ban_result)
    game_count = None if isinstance(game_count_result, Exception) else game_count_result
    friend_count = None if isinstance(friend_result, Exception) else len(friend_result)
    recent_response = {} if isinstance(recent_result, Exception) else recent_result

    return build_profile_info(
        steamid,
        player,
        level=level,
        owned_game_count=game_count,
        friend_count=friend_count,
        ban=ban,
        recent_response=recent_response,
    )


async def resolve_profile_steamid(profile_url, api_key):
    steamid = extract_profile_steamid_from_url(profile_url)
    if steamid:
        return steamid

    vanity = extract_profile_vanity_from_url(profile_url)
    if not vanity:
        raise SteamAPIError("无法从个人主页链接中识别 SteamID 或自定义 URL")

    return await asyncio.to_thread(_resolve_vanity_url, vanity, api_key)


async def get_friend_status_lines(profile_url, api_key, limit=10):
    if not api_key:
        return []

    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    steamid = await resolve_profile_steamid(profile_url, api_key)
    friends = await asyncio.to_thread(_fetch_friend_list, steamid, api_key)
    friend_ids = [friend.get("steamid") for friend in friends if friend.get("steamid")]

    online_players = []
    checked_friend_ids = 0
    for chunk in _chunked(friend_ids, 100):
        players = await asyncio.to_thread(_fetch_player_summaries, chunk, api_key)
        checked_friend_ids += len(chunk)
        for player in players:
            state = _persona_state_value(player.get("personastate"))
            if state > 0 or player.get("gameextrainfo"):
                online_players.append(player)
        if len(online_players) >= limit:
            break

    online_players.sort(
        key=lambda player: (
            0 if player.get("gameextrainfo") else 1,
            -_persona_state_value(player.get("personastate")),
            player.get("personaname") or "",
        )
    )

    if not online_players:
        return [f"👥 好友在线概览: 暂无在线好友（好友总数 {len(friend_ids)}）"]

    shown_players = online_players[:limit]
    total_text = f"至少 {len(online_players)}" if checked_friend_ids < len(friend_ids) else str(len(online_players))
    lines = [f"👥 好友在线概览: 在线 {total_text} / 好友 {len(friend_ids)}"]
    for player in shown_players:
        name = player.get("personaname") or player.get("steamid") or "未知用户"
        if player.get("gameextrainfo"):
            status = f"正在玩 {player['gameextrainfo']}"
        else:
            status = _format_persona_state(player.get("personastate"))
        lines.append(f"  - {name}: {status}")

    if len(online_players) > len(shown_players):
        lines.append(f"  - 其余 {len(online_players) - len(shown_players)} 位在线好友已省略")

    return lines
