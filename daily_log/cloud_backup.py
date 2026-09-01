"""Local, WebDAV and S3-compatible backup backends."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .errors import ValidationError


class BackupError(RuntimeError):
    pass


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _validated_url(value: object, label: str, *, allow_path: bool, allow_private: bool = False) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError(f"{label}地址无效。")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"}:
        loopback = True
    else:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        loopback = bool(address and address.is_loopback)
        if address and (address.is_private or address.is_link_local or address.is_reserved) and not loopback and not allow_private:
            raise ValidationError(f"{label}地址不能指向本机以外的私有网络。")
        if hostname.endswith((".local", ".internal")) and not allow_private:
            raise ValidationError(f"{label}地址不能指向内部网络主机。")
    if parsed.scheme == "http" and not loopback:
        raise ValidationError(f"远程{label}必须使用 HTTPS。")
    if not allow_path and parsed.path not in {"", "/"}:
        raise ValidationError(f"{label}端点不能包含路径。")
    return parsed


def _http_error(error: Exception) -> BackupError:
    if isinstance(error, urllib.error.HTTPError):
        error.close()
        return BackupError(f"远程备份请求失败（HTTP {error.code}）。")
    return BackupError(f"远程备份连接失败：{type(error).__name__}")


def upload_webdav(archive: Path, settings: dict) -> str:
    base_url = str(settings.get("url") or "").strip().rstrip("/")
    _validated_url(base_url, "WebDAV", allow_path=True, allow_private=_as_bool(settings.get("allow_private")))
    target = f"{base_url}/{urllib.parse.quote(archive.name)}"
    headers = {"Content-Type": "application/octet-stream" if archive.name.endswith(".dlenc") else "application/zip"}
    username = str(settings.get("username") or "")
    password = str(settings.get("password") or "")
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(target, data=archive.read_bytes(), headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status not in {200, 201, 204}:
                raise BackupError(f"WebDAV 返回了未预期状态：{response.status}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise _http_error(error) from error
    return target


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _s3_request(method: str, settings: dict, key: str = "", *, query: dict[str, str] | None = None, payload: bytes = b""):
    endpoint = str(settings.get("endpoint") or "").strip().rstrip("/")
    region = str(settings.get("region") or "").strip()
    bucket = str(settings.get("bucket") or "").strip()
    access_key = str(settings.get("access_key") or "").strip()
    secret_key = str(settings.get("secret_key") or "").strip()
    if not all((endpoint, region, bucket, access_key, secret_key)):
        raise ValidationError("S3 配置不完整。")
    parsed = _validated_url(endpoint, "S3", allow_path=False, allow_private=_as_bool(settings.get("allow_private")))
    parts = [bucket, *(part for part in key.split("/") if part)]
    canonical_uri = "/" + "/".join(urllib.parse.quote(part, safe="-_.~") for part in parts)
    query = query or {}
    canonical_query = urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote, safe="-_.~")
    target = endpoint + canonical_uri + (("?" + canonical_query) if canonical_query else "")
    payload_hash = hashlib.sha256(payload).hexdigest()
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    host = parsed.netloc
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    date_key = _sign(("AWS4" + secret_key).encode(), date_stamp)
    region_key = _sign(date_key, region)
    service_key = _sign(region_key, "s3")
    signing_key = _sign(service_key, "aws4_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return urllib.request.Request(target, data=payload if method in {"PUT", "POST"} else None, method=method, headers={
        "Authorization": authorization,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    })


def upload_s3(archive: Path, settings: dict) -> str:
    prefix = str(settings.get("prefix") or "").strip("/")
    key = "/".join(part for part in (prefix, archive.name) if part)
    payload = archive.read_bytes()
    request = _s3_request("PUT", settings, key, payload=payload)
    request.add_header("Content-Type", "application/octet-stream" if archive.name.endswith(".dlenc") else "application/zip")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status not in {200, 201, 204}:
                raise BackupError(f"S3 返回了未预期状态：{response.status}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise _http_error(error) from error
    return request.full_url


def upload_archive(archive: Path, settings: dict) -> str:
    backend = settings.get("backend", "local")
    if backend == "local":
        return str(archive)
    if backend == "webdav":
        return upload_webdav(archive, settings["webdav"])
    if backend == "s3":
        return upload_s3(archive, settings["s3"])
    raise ValidationError("未知的备份方式。")


def test_webdav_connection(settings: dict) -> dict:
    """Probe a WebDAV collection without creating or modifying a file."""
    base_url = str(settings.get("url") or "").strip().rstrip("/")
    _validated_url(base_url, "WebDAV", allow_path=True, allow_private=_as_bool(settings.get("allow_private")))
    headers = {**_webdav_headers(settings), "Depth": "0", "Content-Type": "application/xml"}
    body = b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>'
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base_url + "/", data=body, headers=headers, method="PROPFIND"), timeout=30
        ) as response:
            status = getattr(response, "status", 200)
            if status not in {200, 204, 207}:
                raise BackupError(f"WebDAV 返回了未预期状态（HTTP {status}）。")
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in {401, 403}:
            raise BackupError("WebDAV 连接失败：用户名或密码不正确，或没有访问权限。") from error
        raise BackupError(f"WebDAV 连接失败（HTTP {error.code}）。") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise BackupError(f"WebDAV 连接失败：{type(error).__name__}") from error
    return {"ok": True, "message": "WebDAV 连接成功，可以访问目标文件夹。"}


def test_s3_connection(settings: dict) -> dict:
    """List at most one object to verify S3 credentials and bucket access."""
    request = _s3_request("GET", settings, query={"list-type": "2", "max-keys": "1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if not 200 <= status < 300:
                raise BackupError(f"S3 返回了未预期状态（HTTP {status}）。")
    except urllib.error.HTTPError as error:
        error.close()
        if error.code in {401, 403}:
            raise BackupError("S3 连接失败：密钥无效或没有存储桶访问权限。") from error
        if error.code == 404:
            raise BackupError("S3 连接失败：端点或存储桶不存在。") from error
        raise BackupError(f"S3 连接失败（HTTP {error.code}）。") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise BackupError(f"S3 连接失败：{type(error).__name__}") from error
    return {"ok": True, "message": "S3 连接成功，可以访问目标存储桶。"}


def _webdav_headers(settings: dict) -> dict[str, str]:
    headers: dict[str, str] = {}
    username = str(settings.get("username") or "")
    password = str(settings.get("password") or "")
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def download_latest_webdav(settings: dict, destination: Path) -> Path:
    base_url = str(settings.get("url") or "").strip().rstrip("/")
    _validated_url(base_url, "WebDAV", allow_path=True, allow_private=_as_bool(settings.get("allow_private")))
    headers = {**_webdav_headers(settings), "Depth": "1", "Content-Type": "application/xml"}
    body = b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop><getlastmodified/></prop></propfind>'
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url + "/", data=body, headers=headers, method="PROPFIND"), timeout=120) as response:
            listing = response.read()
        names = []
        for node in ET.fromstring(listing).iter():
            if node.tag.endswith("href") and node.text:
                name = urllib.parse.unquote(urllib.parse.urlparse(node.text).path.rsplit("/", 1)[-1])
                if name.startswith("daily-log-") and (name.endswith(".zip") or name.endswith(".zip.dlenc")):
                    names.append(name)
        if not names:
            raise BackupError("WebDAV 中还没有可恢复的备份。")
        name = max(names)
        target_url = f"{base_url}/{urllib.parse.quote(name)}"
        with urllib.request.urlopen(urllib.request.Request(target_url, headers=_webdav_headers(settings)), timeout=120) as response:
            payload = response.read()
    except BackupError:
        raise
    except (ET.ParseError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise _http_error(error) from error
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    target.write_bytes(payload)
    return target


def download_latest_s3(settings: dict, destination: Path) -> Path:
    prefix = str(settings.get("prefix") or "").strip("/")
    query = {"list-type": "2", "prefix": (prefix + "/") if prefix else "daily-log-"}
    try:
        with urllib.request.urlopen(_s3_request("GET", settings, query=query), timeout=120) as response:
            listing = response.read()
        keys = [node.text for node in ET.fromstring(listing).iter() if node.tag.endswith("Key") and node.text]
        keys = [key for key in keys if key.rsplit("/", 1)[-1].startswith("daily-log-") and (key.endswith(".zip") or key.endswith(".zip.dlenc"))]
        if not keys:
            raise BackupError("S3 中还没有可恢复的备份。")
        key = max(keys)
        with urllib.request.urlopen(_s3_request("GET", settings, key), timeout=120) as response:
            payload = response.read()
    except BackupError:
        raise
    except (ET.ParseError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise _http_error(error) from error
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / key.rsplit("/", 1)[-1]
    target.write_bytes(payload)
    return target


def download_latest_archive(settings: dict, destination: Path) -> Path:
    backend = settings.get("backend", "local")
    if backend == "local":
        candidates = sorted(
            [*Path(destination).glob("daily-log-*.zip"), *Path(destination).glob("daily-log-*.zip.dlenc")],
            key=lambda path: (path.stat().st_mtime, path.name),
        )
        if not candidates:
            raise BackupError("本机还没有可恢复的备份。")
        return candidates[-1]
    downloads = Path(destination).parent / "restore-downloads"
    if backend == "webdav":
        return download_latest_webdav(settings["webdav"], downloads)
    if backend == "s3":
        return download_latest_s3(settings["s3"], downloads)
    raise ValidationError("未知的备份方式。")
