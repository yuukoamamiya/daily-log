"""Cached, read-only iCalendar subscriptions for the local client."""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from icalendar import Calendar

from .database import default_state_dir
from .errors import ValidationError


MAX_ICS_BYTES = 5 * 1024 * 1024


class SubscriptionError(RuntimeError):
    pass


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Validate every redirect target before urllib follows it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_subscription_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def subscription_cache_path(identifier: object) -> Path:
    safe = "".join(character for character in str(identifier) if character.isalnum() or character in "-_")
    if not safe or safe != str(identifier):
        raise ValidationError("日历订阅标识无效。")
    return default_state_dir() / "subscriptions" / f"{safe}.ics"


def validate_subscription_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("日历订阅必须使用有效的 HTTPS 地址。")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal")):
        raise ValidationError("日历订阅不能指向本机或内部网络。")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise SubscriptionError("无法解析日历订阅地址。") from error
    for value in addresses:
        address = ipaddress.ip_address(value)
        if not address.is_global:
            raise ValidationError("日历订阅不能指向本机或内部网络。")
    return url


def refresh_subscription(url: object, cache_path: Path) -> dict:
    safe_url = validate_subscription_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"Accept": "text/calendar, text/plain;q=0.8", "User-Agent": "DailyLog/1.0"},
    )
    try:
        opener = urllib.request.build_opener(_SafeRedirect())
        with opener.open(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_ICS_BYTES:
                raise SubscriptionError("日历订阅文件过大。")
            payload = response.read(MAX_ICS_BYTES + 1)
    except SubscriptionError:
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise SubscriptionError(f"日历订阅更新失败：{type(error).__name__}") from error
    if len(payload) > MAX_ICS_BYTES:
        raise SubscriptionError("日历订阅文件过大。")
    try:
        calendar = Calendar.from_ical(payload)
        count = sum(1 for component in calendar.walk("VEVENT"))
    except Exception as error:  # icalendar exposes several parser error types
        raise SubscriptionError("订阅地址返回的不是有效日历。") from error
    if not count:
        raise SubscriptionError("订阅日历中没有事件。")
    target = Path(cache_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".ics.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return {
        "message": f"日历订阅已更新，共 {count} 项",
        "count": count,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _iso(value) -> tuple[str, str, bool]:
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.date().isoformat(), aware.isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), value.isoformat(), True
    parsed = str(value)
    return parsed[:10], parsed, "T" not in parsed


def load_subscription_events(
    cache_path: Path,
    *,
    subscription_id: str = "subscription",
    subscription_name: str = "订阅日历",
) -> list[dict]:
    path = Path(cache_path)
    if not path.is_file():
        return []
    try:
        calendar = Calendar.from_ical(path.read_bytes())
    except Exception as error:
        raise SubscriptionError("本地节假日缓存已经损坏，请重新更新订阅。") from error
    events = []
    for component in calendar.walk("VEVENT"):
        start_value = component.decoded("dtstart")
        event_date, start, all_day = _iso(start_value)
        end = ""
        if component.get("dtend") is not None:
            _, end, _ = _iso(component.decoded("dtend"))
        uid = str(component.get("uid") or f"{event_date}-{component.get('summary', '')}")
        digest = hashlib.sha256(f"{subscription_id}:{uid}".encode("utf-8")).hexdigest()[:24]
        events.append({
            "id": f"subscription-{digest}",
            "uid": uid,
            "title": str(component.get("summary") or "节假日"),
            "date": event_date,
            "start": start,
            "end": end,
            "allDay": all_day,
            "location": str(component.get("location") or ""),
            "description": str(component.get("description") or ""),
            "readOnly": True,
            "source": "subscription",
            "subscriptionId": subscription_id,
            "subscriptionName": subscription_name,
        })
    return sorted(events, key=lambda item: (item["date"], item["title"]))


def subscription_status(cache_path: Path) -> dict:
    path = Path(cache_path)
    if not path.is_file():
        return {"cached": False, "updatedAt": None, "count": 0}
    try:
        count = len(load_subscription_events(path))
    except SubscriptionError:
        count = 0
    updated = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return {"cached": True, "updatedAt": updated, "count": count}


def delete_subscription_cache(identifier: object) -> None:
    path = subscription_cache_path(identifier)
    if path.exists():
        path.unlink()
