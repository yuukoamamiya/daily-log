#!/usr/bin/env python3
"""可移植性静态审计：扫描 git 跟踪文件中的硬编码绝对路径和依赖完整性。

用途：一次性诊断「git clone 干净副本能否在别处运作」的静态隐患。
不接入 CI，按需运行：.venv/Scripts/python.exe scripts/audit_portability.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 可移植目标：这些目录里的文本文件不应含硬编码绝对路径。
SCAN_DIRS = ("scripts", "config", ".github", "tests")
SCAN_FILES = ("AGENTS.md", "SETUP.md", "requirements.txt")

# 本机专属 / 设计参考，允许含绝对路径。
SKIP_PATTERNS = (
    # This file contains the absolute-path regex literals it uses for scanning.
    re.compile(r"^scripts/audit_portability\.py$"),
    re.compile(r"^config/design\.md$"),
    re.compile(r"^config/secrets\.env$"),
)

# 常见绝对路径形态：盘符（排除 URL/file:// 协议）、Unix 用户目录。
# (?!/) 排除 "s://"、"e://" 这类协议写法的第二个斜杠，避免误报 URL。
ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/](?!/))"
    r"|(?:/home/[^/\s]+)"
    r"|(?:/Users/[^/\s]+)"
    r"|(?:/root/)"
    r"|(?:/tmp/)"
)

# 已知可接受的引用：脚本内可被环境变量覆盖的默认值、注释示例等
ALLOWED = (
    "%APPDATA%",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return b"\x00" in stream.read(4096)
    except OSError:
        return True


def check_absolute_paths() -> list[str]:
    errors: list[str] = []
    for name in tracked_files():
        if SKIP_PATTERNS and any(pat.match(name) for pat in SKIP_PATTERNS):
            continue
        if not (name.startswith(tuple(SCAN_DIRS)) or name in SCAN_FILES):
            continue
        path = ROOT / name
        if not path.exists() or is_binary(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if ABS_PATH_RE.search(line) and not any(allowed in stripped for allowed in ALLOWED):
                errors.append(f"{name}:{number}: 疑似硬编码绝对路径: {stripped}")
    return errors


def check_dependencies() -> list[str]:
    errors: list[str] = []
    req = ROOT / "requirements.txt"
    if not req.exists():
        return ["缺少 requirements.txt"]
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            continue
        package = re.split(r"[<>=!~]", line, maxsplit=1)[0].strip()
        if not package:
            continue
        try:
            module = package.replace("-", "_").split("[")[0]
            __import__(module)
        except ImportError:
            errors.append(f"requirements.txt 中 {package} 未安装（新环境需 pip install -r requirements.txt）")
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(check_absolute_paths())
    errors.extend(check_dependencies())

    if errors:
        print("可移植性审计失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("可移植性审计通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
