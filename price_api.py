import asyncio

import requests


ITAD_API_BASE = "https://api.isthereanydeal.com"
ITAD_TIMEOUT = 8
STEAM_SHOP_ID = 61
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


class PriceAPIError(Exception):
    """价格历史接口请求或解析失败。"""


def _post_json(path, api_key, payload, params=None):
    query = {"key": api_key}
    if params:
        query.update(params)

    response = requests.post(
        f"{ITAD_API_BASE}{path}",
        params=query,
        json=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=ITAD_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _get_json(path, api_key, params=None):
    query = {"key": api_key}
    if params:
        query.update(params)

    response = requests.get(
        f"{ITAD_API_BASE}{path}",
        params=query,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=ITAD_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _extract_game_id(lookup_data, steam_store_id):
    if isinstance(lookup_data, list):
        for item in lookup_data:
            if not isinstance(item, dict):
                continue
            if item.get("id"):
                return item["id"]

    if isinstance(lookup_data, dict):
        direct = lookup_data.get(steam_store_id)
        if isinstance(direct, dict) and direct.get("id"):
            return direct["id"]
        if isinstance(direct, str):
            return direct
        if lookup_data.get("id"):
            return lookup_data["id"]

        for value in lookup_data.values():
            if isinstance(value, dict) and value.get("id"):
                return value["id"]

    raise PriceAPIError(f"ITAD 未能映射 Steam 商店 ID: {steam_store_id}")


def _lookup_steam_game_id(appid, api_key):
    steam_store_id = f"app/{appid}"
    data = _post_json(
        f"/lookup/id/shop/{STEAM_SHOP_ID}/v1",
        api_key,
        [steam_store_id],
    )
    return _extract_game_id(data, steam_store_id)


def _extract_history_low(history_data, game_id):
    items = history_data if isinstance(history_data, list) else None
    if items is None and isinstance(history_data, dict):
        if isinstance(history_data.get(game_id), dict):
            return history_data[game_id]
        if isinstance(history_data.get("data"), list):
            items = history_data["data"]
        elif history_data.get("id") == game_id:
            return history_data

    if items:
        for item in items:
            if isinstance(item, dict) and item.get("id") == game_id:
                return item
        for item in items:
            if isinstance(item, dict):
                return item

    raise PriceAPIError(f"ITAD 未返回史低数据: {game_id}")


def _extract_game_item(data, game_id, list_keys=("data", "prices")):
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id") == game_id:
                return item
        for item in data:
            if isinstance(item, dict):
                return item

    if isinstance(data, dict):
        if isinstance(data.get(game_id), dict):
            return data[game_id]
        if data.get("id") == game_id:
            return data
        for key in list_keys:
            if isinstance(data.get(key), list):
                return _extract_game_item(data[key], game_id, ())

    raise PriceAPIError(f"ITAD 未返回游戏数据: {game_id}")


def _fetch_history_low(game_id, api_key, country):
    data = _post_json(
        "/games/historylow/v1",
        api_key,
        [game_id],
        {"country": country},
    )
    return _extract_history_low(data, game_id)


def _fetch_current_prices(game_id, api_key, country, capacity=3):
    data = _post_json(
        "/games/prices/v3",
        api_key,
        [game_id],
        {
            "country": country,
            "capacity": max(1, min(10, int(capacity or 3))),
            "vouchers": "true",
        },
    )
    item = _extract_game_item(data, game_id)
    return item.get("deals") or item.get("prices") or []


def _fetch_history_log(game_id, api_key, country):
    return _get_json(
        "/games/history/v2",
        api_key,
        {
            "id": game_id,
            "country": country,
            "shops": str(STEAM_SHOP_ID),
        },
    )


def _coerce_price_amount(price):
    if not isinstance(price, dict):
        return None

    amount = price.get("amount")
    if amount is not None:
        try:
            return float(amount)
        except (TypeError, ValueError):
            return None

    amount_int = price.get("amountInt")
    if amount_int is not None:
        try:
            return float(amount_int) / 100
        except (TypeError, ValueError):
            return None

    return None


def _format_price(price):
    if not isinstance(price, dict):
        return None

    amount = _coerce_price_amount(price)
    if amount is None:
        return None

    currency = price.get("currency") or ""
    if currency:
        return f"{currency} {amount:.2f}"
    return f"{amount:.2f}"


def _format_date(value):
    if not value:
        return None
    return str(value)[:10]


def _format_shop(shop):
    if isinstance(shop, dict):
        return shop.get("name") or str(shop.get("id") or "")
    return str(shop) if shop else ""


def _format_cut(cut):
    if cut in (None, ""):
        return None
    try:
        cut_value = int(float(cut))
    except (TypeError, ValueError):
        return None
    return f"-{cut_value}%" if cut_value > 0 else None


def _deal_price_amount(deal):
    if not isinstance(deal, dict):
        return None
    return _coerce_price_amount(deal.get("price"))


def _format_deal(deal, include_date=False):
    if not isinstance(deal, dict):
        return None

    price = _format_price(deal.get("price"))
    if not price:
        return None

    parts = [price]
    cut = _format_cut(deal.get("cut"))
    if cut:
        parts.append(cut)

    shop = _format_shop(deal.get("shop"))
    if shop:
        parts.append(shop)

    if include_date:
        date = _format_date(deal.get("timestamp"))
        if date:
            parts.append(date)

    return price if len(parts) == 1 else f"{price}（{'，'.join(parts[1:])}）"


def _build_current_low_text(deals, limit=3):
    if not isinstance(deals, list):
        return None

    valid_deals = [deal for deal in deals if _deal_price_amount(deal) is not None]
    valid_deals.sort(key=_deal_price_amount)

    lines = []
    for deal in valid_deals[:max(1, limit)]:
        text = _format_deal(deal)
        if text:
            lines.append(text)

    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    return "\n" + "\n".join(lines)


def _extract_low_payload(history_item):
    if not isinstance(history_item, dict):
        raise PriceAPIError("ITAD 史低数据格式无效")
    return (
        history_item.get("low")
        or history_item.get("historyLow")
        or history_item.get("historylow")
        or history_item
    )


def _format_history_low(history_item):
    low = _extract_low_payload(history_item)
    price = _format_price(low.get("price"))
    if not price:
        raise PriceAPIError("ITAD 史低价格缺失")

    parts = [price]

    cut = low.get("cut")
    if cut not in (None, ""):
        parts.append(f"-{cut}%")

    timestamp = low.get("timestamp") or low.get("date")
    if timestamp:
        parts.append(str(timestamp)[:10])

    shop = low.get("shop")
    if isinstance(shop, dict) and shop.get("name"):
        parts.append(shop["name"])

    return "（" + "，".join(parts[1:]) + "）" if len(parts) > 1 else price, price


def _format_history_log_entry(entry):
    if not isinstance(entry, dict):
        return None

    deal = entry.get("deal") or {}
    price = _format_price(deal.get("price"))
    if not price:
        return None

    date = _format_date(entry.get("timestamp"))
    cut = _format_cut(deal.get("cut"))
    parts = [part for part in (date, price, cut) if part]
    return " ".join(parts)


def _build_history_log_text(history_log, limit=5):
    if not isinstance(history_log, list):
        return None

    lines = []
    seen = set()
    for entry in history_log:
        text = _format_history_log_entry(entry)
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(text)
        if len(lines) >= max(1, limit):
            break

    if not lines:
        return None
    return "\n" + "\n".join(lines)


def _build_history_low_text(history_item):
    suffix, price = _format_history_low(history_item)
    if suffix.startswith("（"):
        return f"{price}{suffix}"
    return suffix


async def get_history_low_text(appid, api_key, country="CN"):
    if not api_key:
        return None

    country = (country or "CN").strip().upper()
    if len(country) != 2:
        country = "CN"

    game_id = await asyncio.to_thread(_lookup_steam_game_id, appid, api_key)
    history_item = await asyncio.to_thread(_fetch_history_low, game_id, api_key, country)
    return _build_history_low_text(history_item)


async def get_price_summary(appid, api_key, country="CN", current_limit=3, history_limit=5):
    if not api_key:
        return {}

    country = (country or "CN").strip().upper()
    if len(country) != 2:
        country = "CN"

    try:
        current_limit = max(1, min(10, int(current_limit)))
    except (TypeError, ValueError):
        current_limit = 3

    try:
        history_limit = max(1, min(10, int(history_limit)))
    except (TypeError, ValueError):
        history_limit = 5

    game_id = await asyncio.to_thread(_lookup_steam_game_id, appid, api_key)
    history_low_result, current_prices_result, history_log_result = await asyncio.gather(
        asyncio.to_thread(_fetch_history_low, game_id, api_key, country),
        asyncio.to_thread(_fetch_current_prices, game_id, api_key, country, current_limit),
        asyncio.to_thread(_fetch_history_log, game_id, api_key, country),
        return_exceptions=True,
    )

    summary = {}
    format_errors = []
    if not isinstance(history_low_result, Exception):
        try:
            summary["💹 史低"] = _build_history_low_text(history_low_result)
        except Exception as e:
            format_errors.append(str(e))

    if not isinstance(current_prices_result, Exception):
        try:
            current_low = _build_current_low_text(current_prices_result, current_limit)
            if current_low:
                summary["🛒 当前低价"] = current_low
        except Exception as e:
            format_errors.append(str(e))

    if not isinstance(history_log_result, Exception):
        try:
            history_log = _build_history_log_text(history_log_result, history_limit)
            if history_log:
                summary["📉 近期价格"] = history_log
        except Exception as e:
            format_errors.append(str(e))

    if not summary:
        errors = [
            str(result)
            for result in (history_low_result, current_prices_result, history_log_result)
            if isinstance(result, Exception)
        ]
        errors.extend(format_errors)
        if errors:
            raise PriceAPIError("; ".join(errors))

    return summary
