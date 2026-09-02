"""Local INI configuration stored outside the Git repository."""
from __future__ import annotations

import configparser
import io
import json
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .database import default_state_dir
from .errors import ValidationError


CHINA_HOLIDAY_URL = "https://raw.githubusercontent.com/Lancetwang/china-mainland-calendar/main/china-mainland.ics"
DEFAULT_SUBSCRIPTIONS = [{
    "id": "china-mainland",
    "name": "中国大陆节假日",
    "url": CHINA_HOLIDAY_URL,
    "enabled": False,
}]
THEMES = {"light", "dark", "system"}

DEFAULTS = {
    "ai": {
        "enabled": "false",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "api_key": "",
    },
    "backup": {
        "auto_backup": "false",
        "idle_seconds": "60",
        "backend": "local",
        "include_data": "true",
        "encrypt_backup": "false",
        "encryption_password": "",
    },
    "webdav": {
        "url": "",
        "username": "",
        "password": "",
        "allow_private": "false",
    },
    "s3": {
        "endpoint": "https://s3.amazonaws.com",
        "region": "us-east-1",
        "bucket": "",
        "prefix": "daily-log",
        "access_key": "",
        "secret_key": "",
        "allow_private": "false",
    },
    "backup_proxy": {
        "mode": "system",
        "url": "",
        "username": "",
        "password": "",
    },
    "application": {
        "start_minimized": "false",
        "onboarding_completed": "false",
        "theme": "system",
    },
    "calendar_subscriptions": {
        "items_json": json.dumps(DEFAULT_SUBSCRIPTIONS, ensure_ascii=False),
    },
}


def default_config_path() -> Path:
    return default_state_dir() / "config.ini"


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class LocalConfig:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_config_path())
        self._lock = threading.Lock()

    def load(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read_dict(DEFAULTS)
        if self.path.exists():
            parser.read(self.path, encoding="utf-8")
        return parser

    def public(self) -> dict:
        parser = self.load()
        theme = parser.get("application", "theme", fallback="system").strip().lower()
        if theme not in THEMES:
            theme = "system"
        return {
            "ai": {
                "enabled": parser.getboolean("ai", "enabled", fallback=False),
                "provider": parser.get("ai", "provider", fallback="deepseek"),
                "baseUrl": parser.get("ai", "base_url", fallback=DEFAULTS["ai"]["base_url"]),
                "model": parser.get("ai", "model", fallback="deepseek-chat"),
                "apiKeyConfigured": bool(parser.get("ai", "api_key", fallback="")),
            },
            "backup": {
                "autoBackup": parser.getboolean("backup", "auto_backup", fallback=False),
                "idleSeconds": parser.getint("backup", "idle_seconds", fallback=60),
                "backend": parser.get("backup", "backend", fallback="local"),
                "includeData": parser.getboolean("backup", "include_data", fallback=True),
                "encryptBackup": parser.getboolean("backup", "encrypt_backup", fallback=False),
                "encryptionPasswordConfigured": bool(
                    parser.get("backup", "encryption_password", fallback="")
                ),
                "webdav": {
                    "url": parser.get("webdav", "url", fallback=""),
                    "username": parser.get("webdav", "username", fallback=""),
                    "passwordConfigured": bool(parser.get("webdav", "password", fallback="")),
                    "allowPrivate": parser.getboolean("webdav", "allow_private", fallback=False),
                },
                "s3": {
                    "endpoint": parser.get("s3", "endpoint", fallback=DEFAULTS["s3"]["endpoint"]),
                    "region": parser.get("s3", "region", fallback="us-east-1"),
                    "bucket": parser.get("s3", "bucket", fallback=""),
                    "prefix": parser.get("s3", "prefix", fallback="daily-log"),
                    "accessKeyConfigured": bool(parser.get("s3", "access_key", fallback="")),
                    "secretKeyConfigured": bool(parser.get("s3", "secret_key", fallback="")),
                    "allowPrivate": parser.getboolean("s3", "allow_private", fallback=False),
                },
                "proxy": {
                    "mode": parser.get("backup_proxy", "mode", fallback="system"),
                    "url": parser.get("backup_proxy", "url", fallback=""),
                    "username": parser.get("backup_proxy", "username", fallback=""),
                    "passwordConfigured": bool(parser.get("backup_proxy", "password", fallback="")),
                },
            },
            "application": {
                "startMinimized": parser.getboolean("application", "start_minimized", fallback=False),
                "onboardingCompleted": parser.getboolean("application", "onboarding_completed", fallback=False),
                "theme": theme,
            },
            "calendarSubscriptions": self._subscription_items(parser),
            "configPath": str(self.path),
        }

    def ai_credentials(self) -> dict:
        parser = self.load()
        return {
            "enabled": parser.getboolean("ai", "enabled", fallback=False),
            "provider": parser.get("ai", "provider", fallback="deepseek"),
            "base_url": parser.get("ai", "base_url", fallback=DEFAULTS["ai"]["base_url"]),
            "model": parser.get("ai", "model", fallback="deepseek-chat"),
            "api_key": parser.get("ai", "api_key", fallback=""),
        }

    def backup_settings(self) -> dict:
        parser = self.load()
        return {
            "backend": parser.get("backup", "backend", fallback="local"),
            "include_data": parser.getboolean("backup", "include_data", fallback=True),
            "encrypt_backup": parser.getboolean("backup", "encrypt_backup", fallback=False),
            "encryption_password": parser.get("backup", "encryption_password", fallback=""),
            "webdav": dict(parser["webdav"]),
            "s3": dict(parser["s3"]),
            "proxy": dict(parser["backup_proxy"]),
        }

    @staticmethod
    def _subscription_items(parser: configparser.ConfigParser) -> list[dict]:
        # One-time compatibility for the original single-subscription setting.
        if parser.has_section("calendar_subscription"):
            return [{
                "id": "china-mainland",
                "name": "中国大陆节假日",
                "url": parser.get("calendar_subscription", "url", fallback=CHINA_HOLIDAY_URL),
                "enabled": parser.getboolean("calendar_subscription", "enabled", fallback=False),
            }]
        try:
            raw = json.loads(parser.get("calendar_subscriptions", "items_json"))
        except (json.JSONDecodeError, configparser.Error) as error:
            raise ValidationError("日历订阅设置已经损坏。") from error
        if not isinstance(raw, list):
            raise ValidationError("日历订阅设置已经损坏。")
        items = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            url = str(item.get("url", "")).strip()
            if identifier and name and url:
                items.append({"id": identifier, "name": name, "url": url, "enabled": _bool(item.get("enabled"))})
        return items

    @staticmethod
    def _save_subscription_items(parser: configparser.ConfigParser, items: list[dict]) -> None:
        if parser.has_section("calendar_subscription"):
            parser.remove_section("calendar_subscription")
        if not parser.has_section("calendar_subscriptions"):
            parser.add_section("calendar_subscriptions")
        parser["calendar_subscriptions"]["items_json"] = json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    def calendar_subscriptions(self) -> list[dict]:
        return self._subscription_items(self.load())

    @staticmethod
    def _validate_subscription(name: object, url: object) -> tuple[str, str]:
        clean_name = str(name or "").strip()
        clean_url = str(url or "").strip()
        if not clean_name or len(clean_name) > 80:
            raise ValidationError("订阅名称不能为空，且最多 80 个字符。")
        parsed = urlparse(clean_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValidationError("日历订阅必须使用有效的 HTTPS 地址。")
        return clean_name, clean_url

    def add_calendar_subscription(self, name: object, url: object) -> dict:
        clean_name, clean_url = self._validate_subscription(name, url)
        with self._lock:
            parser = self.load()
            items = self._subscription_items(parser)
            if any(item["url"] == clean_url for item in items):
                raise ValidationError("这个日历地址已经订阅。")
            item = {"id": uuid.uuid4().hex, "name": clean_name, "url": clean_url, "enabled": True}
            items.append(item)
            self._save_subscription_items(parser, items)
            self._write(parser)
        return item

    def toggle_calendar_subscription(self, identifier: object, enabled: object) -> dict:
        with self._lock:
            parser = self.load()
            items = self._subscription_items(parser)
            for item in items:
                if item["id"] == str(identifier):
                    item["enabled"] = _bool(enabled)
                    self._save_subscription_items(parser, items)
                    self._write(parser)
                    return item
        raise ValidationError("日历订阅不存在。")

    def delete_calendar_subscription(self, identifier: object) -> None:
        with self._lock:
            parser = self.load()
            items = self._subscription_items(parser)
            remaining = [item for item in items if item["id"] != str(identifier)]
            if len(remaining) == len(items):
                raise ValidationError("日历订阅不存在。")
            self._save_subscription_items(parser, remaining)
            self._write(parser)

    def secrets(self) -> dict:
        """Return credentials that may only enter a password-encrypted backup member."""
        parser = self.load()
        return {
            "ai": {"api_key": parser.get("ai", "api_key", fallback="")},
            "webdav": {"password": parser.get("webdav", "password", fallback="")},
            "s3": {
                "access_key": parser.get("s3", "access_key", fallback=""),
                "secret_key": parser.get("s3", "secret_key", fallback=""),
            },
            "backup_proxy": {
                "username": parser.get("backup_proxy", "username", fallback=""),
                "password": parser.get("backup_proxy", "password", fallback=""),
            },
        }

    def portable_text(self) -> str:
        """Export useful settings while deliberately excluding every credential."""
        source = self.load()
        parser = configparser.ConfigParser()
        parser["ai"] = {key: value for key, value in source["ai"].items() if key != "api_key"}
        parser["backup"] = {
            key: value for key, value in source["backup"].items()
            if key not in {"encryption_password", "include_secrets", "include_settings"}
        }
        parser["webdav"] = {key: value for key, value in source["webdav"].items() if key != "password"}
        parser["s3"] = {
            key: value for key, value in source["s3"].items() if key not in {"access_key", "secret_key"}
        }
        parser["backup_proxy"] = {
            key: value for key, value in source["backup_proxy"].items()
            if key not in {"username", "password"}
        }
        parser["application"] = dict(source["application"])
        parser["calendar_subscriptions"] = {
            "items_json": json.dumps(self._subscription_items(source), ensure_ascii=False, separators=(",", ":"))
        }
        stream = io.StringIO()
        parser.write(stream)
        return stream.getvalue()

    def restore(self, portable_text: str | None, secrets: dict | None = None) -> dict:
        """Merge restored settings into the local INI while preserving absent credentials."""
        with self._lock:
            parser = self.load()
            if portable_text:
                incoming = configparser.ConfigParser()
                try:
                    incoming.read_string(portable_text)
                except configparser.Error as error:
                    raise ValidationError("备份中的设置文件无效。") from error
                for section in DEFAULTS:
                    if incoming.has_section(section):
                        if not parser.has_section(section):
                            parser.add_section(section)
                        for key, value in incoming[section].items():
                            if key not in {"api_key", "password", "access_key", "secret_key", "encryption_password", "include_secrets"}:
                                parser[section][key] = value
            if secrets:
                for section, keys in (
                    ("ai", ("api_key",)),
                    ("webdav", ("password",)),
                    ("s3", ("access_key", "secret_key")),
                    ("backup_proxy", ("username", "password")),
                ):
                    values = secrets.get(section, {})
                    if isinstance(values, dict):
                        for key in keys:
                            if key in values:
                                parser[section][key] = str(values[key])
            self._write(parser)
        return self.public()

    def complete_onboarding(self) -> dict:
        with self._lock:
            parser = self.load()
            parser["application"]["onboarding_completed"] = "true"
            self._write(parser)
        return self.public()

    def _write(self, parser: configparser.ConfigParser) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".ini.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            parser.write(stream)
        temporary.replace(self.path)

    def update(self, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValidationError("设置格式无效。")
        ai = payload.get("ai", {})
        backup = payload.get("backup", {})
        application = payload.get("application", {})
        if not all(isinstance(section, dict) for section in (ai, backup, application)):
            raise ValidationError("设置分组格式无效。")
        with self._lock:
            parser = self.load()
            provider = str(ai.get("provider", parser["ai"]["provider"])).strip().lower()
            if provider not in {"deepseek", "openai-compatible"}:
                raise ValidationError("暂不支持这个 AI 服务类型。")
            base_url = str(ai.get("baseUrl", parser["ai"]["base_url"])).strip()
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValidationError("AI API 地址必须是有效的 HTTP 或 HTTPS 地址。")
            model = str(ai.get("model", parser["ai"]["model"])).strip()
            if not model or len(model) > 120:
                raise ValidationError("AI 模型名称无效。")
            parser["ai"]["enabled"] = str(_bool(ai.get("enabled", parser["ai"]["enabled"]))).lower()
            parser["ai"]["provider"] = provider
            parser["ai"]["base_url"] = base_url
            parser["ai"]["model"] = model
            if _bool(ai.get("clearApiKey", False)):
                parser["ai"]["api_key"] = ""
            elif str(ai.get("apiKey", "")).strip():
                parser["ai"]["api_key"] = str(ai["apiKey"]).strip()

            idle_seconds = int(backup.get("idleSeconds", parser["backup"]["idle_seconds"]))
            if idle_seconds < 10 or idle_seconds > 3600:
                raise ValidationError("自动备份等待时间必须在 10 到 3600 秒之间。")
            parser["backup"]["auto_backup"] = str(
                _bool(backup.get("autoBackup", parser["backup"]["auto_backup"]))
            ).lower()
            parser["backup"]["idle_seconds"] = str(idle_seconds)
            backend = str(backup.get("backend", parser["backup"]["backend"])).strip().lower()
            if backend not in {"local", "webdav", "s3"}:
                raise ValidationError("暂不支持这个备份方式。")
            parser["backup"]["backend"] = backend
            parser["backup"].pop("include_settings", None)
            include_data = _bool(backup.get("includeData", parser["backup"]["include_data"]))
            encrypt_backup = _bool(backup.get("encryptBackup", parser["backup"].get("encrypt_backup", "false")))
            parser["backup"]["include_data"] = str(include_data).lower()
            parser["backup"]["encrypt_backup"] = str(encrypt_backup).lower()
            supplied_password = str(backup.get("encryptionPassword", ""))
            if supplied_password:
                if len(supplied_password) < 8:
                    raise ValidationError("备份加密密码至少需要 8 个字符。")
                parser["backup"]["encryption_password"] = supplied_password
            if encrypt_backup and not parser["backup"].get("encryption_password"):
                raise ValidationError("加密整个备份前，请设置至少 8 个字符的备份密码。")

            webdav = backup.get("webdav", {})
            s3 = backup.get("s3", {})
            if not isinstance(webdav, dict) or not isinstance(s3, dict):
                raise ValidationError("备份服务配置格式无效。")
            proxy = backup.get("proxy", {})
            if not isinstance(proxy, dict):
                raise ValidationError("代理配置格式无效。")
            proxy_mode = str(proxy.get("mode", parser["backup_proxy"]["mode"])).strip().lower()
            if proxy_mode not in {"system", "none", "custom"}:
                raise ValidationError("代理方式无效。")
            proxy_url = str(proxy.get("url", parser["backup_proxy"]["url"])).strip().rstrip("/")
            if proxy_mode == "custom":
                try:
                    parsed_proxy = urlparse(proxy_url)
                except ValueError as error:
                    raise ValidationError("代理地址无效。") from error
                try:
                    proxy_port = parsed_proxy.port
                except ValueError as error:
                    raise ValidationError("代理地址无效。") from error
                if (
                    parsed_proxy.scheme not in {"http", "https"}
                    or not parsed_proxy.hostname
                    or parsed_proxy.username
                    or parsed_proxy.password
                    or parsed_proxy.path not in {"", "/"}
                    or parsed_proxy.query
                    or parsed_proxy.fragment
                    or (proxy_port is not None and not 1 <= proxy_port <= 65535)
                ):
                    raise ValidationError("代理地址必须是有效的 HTTP 或 HTTPS 代理地址，且不能内嵌用户名密码。")
                if not proxy_url:
                    raise ValidationError("使用自定义代理前请填写代理地址。")
            elif proxy_url:
                try:
                    parsed_proxy = urlparse(proxy_url)
                except ValueError as error:
                    raise ValidationError("代理地址无效。") from error
                if parsed_proxy.scheme not in {"http", "https"} or not parsed_proxy.hostname:
                    raise ValidationError("代理地址无效。")
            parser["backup_proxy"]["mode"] = proxy_mode
            parser["backup_proxy"]["url"] = proxy_url
            parser["backup_proxy"]["username"] = str(
                proxy.get("username", parser["backup_proxy"]["username"])
            ).strip()
            if str(proxy.get("password", "")).strip():
                parser["backup_proxy"]["password"] = str(proxy["password"]).strip()
            webdav_url = str(webdav.get("url", parser["webdav"]["url"])).strip()
            if webdav_url:
                webdav_parsed = urlparse(webdav_url)
                if webdav_parsed.scheme not in {"http", "https"} or not webdav_parsed.netloc:
                    raise ValidationError("WebDAV 地址无效。")
            parser["webdav"]["url"] = webdav_url
            parser["webdav"]["username"] = str(webdav.get("username", parser["webdav"]["username"])).strip()
            if str(webdav.get("password", "")).strip():
                parser["webdav"]["password"] = str(webdav["password"]).strip()
            parser["webdav"]["allow_private"] = str(_bool(webdav.get("allowPrivate", parser["webdav"]["allow_private"]))).lower()

            for key, field in (("endpoint", "endpoint"), ("region", "region"), ("bucket", "bucket"), ("prefix", "prefix")):
                parser["s3"][field] = str(s3.get(key, parser["s3"][field])).strip()
            if str(s3.get("accessKey", "")).strip():
                parser["s3"]["access_key"] = str(s3["accessKey"]).strip()
            if str(s3.get("secretKey", "")).strip():
                parser["s3"]["secret_key"] = str(s3["secretKey"]).strip()
            parser["s3"]["allow_private"] = str(_bool(s3.get("allowPrivate", parser["s3"]["allow_private"]))).lower()
            if backend == "webdav" and not parser["webdav"]["url"]:
                raise ValidationError("启用 WebDAV 前请填写服务器地址。")
            if backend == "s3" and not all(parser["s3"][key] for key in ("endpoint", "region", "bucket", "access_key", "secret_key")):
                raise ValidationError("启用 S3 前请填写完整配置和密钥。")
            parser["application"]["start_minimized"] = str(
                _bool(application.get("startMinimized", parser["application"]["start_minimized"]))
            ).lower()
            parser["application"]["onboarding_completed"] = str(
                _bool(application.get("onboardingCompleted", parser["application"].get("onboarding_completed", "false")))
            ).lower()
            theme = str(application.get("theme", parser["application"].get("theme", "system"))).strip().lower()
            if theme not in THEMES:
                raise ValidationError("主题必须是浅色、深色或跟随系统。")
            parser["application"]["theme"] = theme
            self._save_subscription_items(parser, self._subscription_items(parser))
            self._write(parser)
        return self.public()
