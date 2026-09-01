"""Password-encrypted credentials and scoped restoration of Daily Log backups."""
from __future__ import annotations

import base64
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import LocalConfig
from .database import DailyLogDatabase
from .errors import ValidationError


AAD = b"daily-log-secrets-v1"
ARCHIVE_AAD = b"daily-log-archive-v1"
ENCRYPTED_SUFFIX = ".dlenc"


def _key(password: str, salt: bytes) -> bytes:
    import hashlib

    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def encrypt_secrets(secrets: dict, password: str) -> bytes:
    import os

    if len(password) < 8:
        raise ValidationError("备份加密密码至少需要 8 个字符。")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    plaintext = json.dumps(secrets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(_key(password, salt)).encrypt(nonce, plaintext, AAD)
    envelope = {
        "format": 1,
        "kdf": "scrypt-32768-8-1",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def decrypt_secrets(blob: bytes, password: str) -> dict:
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if envelope.get("format") != 1:
            raise ValueError
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        plaintext = AESGCM(_key(password, salt)).decrypt(nonce, ciphertext, AAD)
        result = json.loads(plaintext)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag, json.JSONDecodeError) as error:
        raise ValidationError("备份密码错误，或密钥备份已经损坏。") from error
    if not isinstance(result, dict):
        raise ValidationError("密钥备份格式无效。")
    return result


def _encrypt_blob(plaintext: bytes, password: str, aad: bytes) -> bytes:
    import os

    if len(password) < 8:
        raise ValidationError("备份密码至少需要 8 个字符。")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(password, salt)).encrypt(nonce, plaintext, aad)
    return json.dumps({
        "format": 1,
        "kdf": "scrypt-32768-8-1",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }, separators=(",", ":")).encode("utf-8")


def _decrypt_blob(blob: bytes, password: str, aad: bytes) -> bytes:
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if envelope.get("format") != 1:
            raise ValueError
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        return AESGCM(_key(password, salt)).decrypt(nonce, ciphertext, aad)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, InvalidTag, json.JSONDecodeError) as error:
        raise ValidationError("备份密码错误，或加密备份已经损坏。") from error


def is_encrypted_archive(path: Path) -> bool:
    return str(path).endswith(ENCRYPTED_SUFFIX)


def encrypt_archive(archive: Path, password: str, *, remove_source: bool = False) -> Path:
    archive = Path(archive)
    target = archive.with_name(archive.name + ENCRYPTED_SUFFIX)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(_encrypt_blob(archive.read_bytes(), password, ARCHIVE_AAD))
    temporary.replace(target)
    if remove_source:
        archive.unlink()
    return target


def decrypt_archive(archive: Path, password: str, destination: Path) -> Path:
    archive = Path(archive)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _decrypt_blob(archive.read_bytes(), password, ARCHIVE_AAD)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target


def inspect_backup(archive: Path) -> dict:
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            manifest = json.loads(bundle.read("backup-manifest.json"))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise ValidationError("备份文件无效或已经损坏。") from error
    if manifest.get("format") != 1:
        raise ValidationError("暂不支持这个版本的备份。")
    return {
        "includesData": bool(manifest.get("includesData")) and "daily-log.db" in names,
        "includesSecrets": bool(manifest.get("includesSecrets")) and bool({"secrets.json", "secrets.enc"} & names),
        "createdAt": manifest.get("createdAt"),
    }


def restore_backup(
    archive: Path,
    database: DailyLogDatabase,
    config: LocalConfig,
    portable_root: Path | None = None,
    *,
    password: str = "",
) -> dict:
    details = inspect_backup(archive)
    with tempfile.TemporaryDirectory() as directory:
        extracted = Path(directory)
        try:
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if stat.S_ISLNK(member.external_attr >> 16):
                        raise ValidationError("备份文件包含不安全的链接。")
                    target = (extracted / member.filename).resolve()
                    try:
                        target.relative_to(extracted.resolve())
                    except ValueError as error:
                        raise ValidationError("备份文件包含不安全的路径。") from error
                bundle.extractall(extracted)
        except zipfile.BadZipFile as error:
            raise ValidationError("备份文件无效或已经损坏。") from error

        secrets = None
        plain_secret_path = extracted / "secrets.json"
        secret_path = extracted / "secrets.enc"
        if plain_secret_path.is_file():
            try:
                secrets = json.loads(plain_secret_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValidationError("备份中的密钥文件无效。") from error
            if not isinstance(secrets, dict):
                raise ValidationError("备份中的密钥文件无效。")
        elif details["includesSecrets"]:
            if not password:
                raise ValidationError("这个备份包含加密密钥，请输入备份密码。")
            secrets = decrypt_secrets(secret_path.read_bytes(), password)

        if details["includesData"]:
            source_database = extracted / "daily-log.db"
            if not source_database.is_file():
                raise ValidationError("备份缺少可恢复的数据。")
            database.restore_from(source_database)
            portable_snapshot = extracted / "portable"
            if portable_root is not None and portable_snapshot.is_dir():
                target = Path(portable_root)
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(portable_snapshot, target)

        portable_path = extracted / "config.portable.ini"
        config.restore(
            portable_path.read_text(encoding="utf-8") if portable_path.is_file() else None,
            secrets,
        )
    return details
