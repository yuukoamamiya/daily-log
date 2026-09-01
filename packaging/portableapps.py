"""Build and validate a PortableApps.com Format package for Daily Log.

The PortableApps.com Launcher and Installer are downloaded at build time.  They
are intentionally not checked into this repository.  The generated package
keeps executable files under ``App`` and all mutable application state under
``Data\\DailyLog``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


APP_ID = "DailyLogPortable"
APP_NAME = "Daily Log Portable"
PACKAGE_NAME = "DailyLog-PortableApps"
FORMAT_VERSION = "3.9"
LAUNCHER_VERSION = "2.2.9"
INSTALLER_VERSION = "3.9.18"
LAUNCHER_URL = (
    "https://portableapps.com/redir2/?a=PortableApps.comLauncher&s=s&d=pa&f="
    "PortableApps.comLauncher_2.2.9.paf.exe"
)
INSTALLER_URL = (
    "https://portableapps.com/redir2/?a=PortableApps.comInstaller&s=s&d=pa&f="
    "PortableApps.comInstaller_3.9.18.paf.exe"
)
LAUNCHER_SHA256 = "7bc93e47b885d5e953fa2082a2e65169ae98128026ac4d747f774a23bd01345b"
INSTALLER_SHA256 = "8dc84002f08ae7bf31dd2f422f6f173b6b8ba2371fd508a9876e13f2eb6ef75a"


class BuildError(RuntimeError):
    """An actionable package-build error."""


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True, errors="replace")
    except FileNotFoundError as error:
        raise BuildError(f"找不到构建工具：{command[0]}") from error
    except subprocess.CalledProcessError as error:
        rendered = " ".join(str(part) for part in command)
        details = (error.stderr or error.stdout or "").strip()[-2000:]
        suffix = f"\n{details}" if details else ""
        raise BuildError(f"构建工具执行失败（{error.returncode}）：{rendered}{suffix}") from error


def _find_7z() -> str:
    for name in ("7z", "7z.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise BuildError("需要 7-Zip 来解压 PortableApps 工具；请先安装 7-Zip 并加入 PATH。")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, expected_sha256: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and _sha256(target) == expected_sha256:
        return target
    if target.exists():
        target.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": "DailyLog build"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream)
    except Exception as error:  # noqa: BLE001
        raise BuildError(f"下载 PortableApps 构建工具失败：{url}") from error
    actual = _sha256(target)
    if actual != expected_sha256:
        target.unlink(missing_ok=True)
        raise BuildError(f"PortableApps 工具校验失败：{target.name}")
    return target


def _extract_archive(archive: Path, destination: Path, seven_zip: str) -> Path:
    marker = destination / ".extracted"
    if marker.is_file():
        return destination
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _run([seven_zip, "x", str(archive), f"-o{destination}", "-y"])
    marker.write_text("ok\n", encoding="ascii")
    return destination


def _tool_directories(tools_dir: Path) -> tuple[Path, Path]:
    seven_zip = _find_7z()
    launcher_archive = _download(
        LAUNCHER_URL, tools_dir / f"PortableApps.comLauncher_{LAUNCHER_VERSION}.paf.exe", LAUNCHER_SHA256
    )
    installer_archive = _download(
        INSTALLER_URL, tools_dir / f"PortableApps.comInstaller_{INSTALLER_VERSION}.paf.exe", INSTALLER_SHA256
    )
    launcher_dir = _extract_archive(launcher_archive, tools_dir / "launcher", seven_zip)
    installer_dir = _extract_archive(installer_archive, tools_dir / "installer", seven_zip)
    return launcher_dir, installer_dir


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\r\n")


def _make_icons(app_info: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise BuildError("生成 PortableApps 图标需要 Pillow。") from error

    image = Image.new("RGBA", (256, 256), (27, 33, 29, 255))
    draw = ImageDraw.Draw(image)
    colors = ((255, 255, 255, 255), (246, 199, 91, 255), (232, 132, 112, 255), (121, 182, 154, 255))
    for index, color in enumerate(colors):
        x = 48 + (index % 2) * 70
        y = 48 + (index // 2) * 70
        draw.rounded_rectangle((x, y, x + 48, y + 48), radius=10, fill=color)
    for size in (16, 32, 75, 128, 256):
        image.resize((size, size), Image.Resampling.LANCZOS).save(app_info / f"appicon_{size}.png")
    image.save(app_info / "appicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


def _appinfo(version: str) -> str:
    return f"""[Format]
Type=PortableApps.comFormat
Version={FORMAT_VERSION}

[Details]
Name={APP_NAME}
AppID={APP_ID}
Publisher=Daily Log contributors
Homepage=https://github.com/yuukoamamiya/daily-log
Category=Office
Description=Local-first journal, expenses, tasks and calendar
Language=ChineseSimplified

[License]
Shareable=true
OpenSource=true
Freeware=true
CommercialUse=true

[Version]
PackageVersion={version}.0
DisplayVersion={version}

[Control]
Icons=1
Start={APP_ID}.exe
BaseAppID=%BASELAUNCHERPATH%\\{APP_ID}.exe
"""


def _launcher_ini() -> str:
    return """[Launch]
ProgramExecutable=DailyLog\\DailyLog.exe
WorkingDirectory=%PAL:AppDir%\\DailyLog
WaitForProgram=true

[Environment]
DAILY_LOG_STATE_DIR=%PAL:DataDir%\\DailyLog
"""


def _installer_config(version: str) -> str:
    return f"""; Generated by packaging/portableapps.py. Do not edit.
!define PORTABLEAPPNAME \"{APP_NAME}\"
!define PORTABLEAPPNAMEDOUBLEDAMPERSANDS \"{APP_NAME}\"
!define APPID \"{APP_ID}\"
!define VERSION \"{version}.0\"
!define FILENAME \"{PACKAGE_NAME}\"
!define FINISHPAGERUN \"{APP_ID}.exe\"
!define CHECKRUNNING \"DailyLog.exe\"
!define CLOSENAME \"Daily Log\"
!define INSTALLERCOMMENTS \"Daily Log PortableApps package\"
!define INSTALLERLANGUAGE \"English\"
!define REMOVEAPPDIRECTORY
!define REMOVEOTHERDIRECTORY
!define ADDONSDIRECTORYPRESERVE \"NONE\"
!define REQUIRESADMIN \"no\"
"""


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"构建输入目录不存在：{source}")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _prepare_staging(app_dir: Path, staging: Path, version: str) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "App" / "DailyLog").mkdir(parents=True)
    (staging / "App" / "AppInfo" / "Launcher").mkdir(parents=True)
    (staging / "App" / "DefaultData").mkdir(parents=True)
    (staging / "Data").mkdir(parents=True)
    _copy_tree(app_dir, staging / "App" / "DailyLog")
    for forbidden in ("data", "daily-log.db", "config.ini"):
        target = staging / "App" / "DailyLog" / forbidden
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    app_info = staging / "App" / "AppInfo"
    _write_text(app_info / "appinfo.ini", _appinfo(version))
    _write_text(app_info / "Launcher" / f"{APP_ID}.ini", _launcher_ini())
    _write_text(app_info / "Launcher" / "Custom.nsh", "; Daily Log does not require custom launcher code.\r\n")
    _write_text(app_info / "Launcher" / "Debug.nsh", "; Daily Log does not require launcher debug code.\r\n")
    _make_icons(app_info)
    _write_text(staging / "App" / "Readme.txt", "Daily Log Portable\r\n\r\n数据保存在本包的 Data\\DailyLog 目录。\r\n")
    _write_text(
        staging / "help.html",
        "<!doctype html><meta charset=\"utf-8\"><title>Daily Log Portable</title>"
        "<h1>Daily Log Portable</h1><p>本包使用 PortableApps.com 格式，数据保存在 Data\\DailyLog。</p>",
    )


def _generate_launcher(staging: Path, launcher_dir: Path, version: str) -> None:
    source = launcher_dir / "Other" / "Source"
    makensis = launcher_dir / "App" / "NSIS" / "makensis.exe"
    if not source.is_dir() or not makensis.is_file():
        raise BuildError("PortableApps Launcher 解压内容不完整。")
    _copy_tree(source, staging / "Other" / "Source")
    _run(
        [
            str(makensis),
            "/V0",
            f"/DPACKAGE={staging.resolve()}",
            f"/DNamePortable={APP_NAME}",
            f"/DAppID={APP_ID}",
            f"/DVersion={version}.0",
            "PortableApps.comLauncher.nsi",
        ],
        cwd=staging / "Other" / "Source",
    )
    launcher = staging / f"{APP_ID}.exe"
    if not launcher.is_file():
        raise BuildError("PortableApps Launcher 没有生成启动器。")


def _generate_installer(staging: Path, installer_dir: Path, version: str) -> Path:
    source = installer_dir / "App" / "installer"
    makensis = installer_dir / "App" / "nsis" / "makensis.exe"
    make_header = installer_dir / "App" / "bin" / "MakeHeader.exe"
    if not source.is_dir() or not makensis.is_file() or not make_header.is_file():
        raise BuildError("PortableApps Installer 解压内容不完整。")
    destination = staging / "Other" / "Source"
    if destination.exists():
        shutil.rmtree(destination)
    _copy_tree(source, destination)
    _write_text(staging / "Other" / "Readme.txt", "PortableApps.com build sources are generated temporarily and are not included in the application data.\r\n")
    _write_text(destination / "PortableApps.comInstallerConfig.nsh", _installer_config(version))
    _run([str(make_header), str(staging)])
    _run([str(makensis), "/V0", "PortableApps.comInstaller.nsi"], cwd=destination)
    output = staging.parent / f"{PACKAGE_NAME}.paf.exe"
    if not output.is_file():
        raise BuildError("PortableApps Installer 没有生成 .paf.exe。")
    return output


def validate_package(package: Path) -> list[str]:
    errors: list[str] = []
    required_files = (
        package / f"{APP_ID}.exe",
        package / "App" / "AppInfo" / "appinfo.ini",
        package / "App" / "AppInfo" / "Launcher" / f"{APP_ID}.ini",
        package / "App" / "AppInfo" / "appicon.ico",
        package / "App" / "DailyLog" / "DailyLog.exe",
    )
    for path in required_files:
        if not path.is_file():
            errors.append(f"缺少 PortableApps 必需文件：{path.relative_to(package)}")
    for path in (package / "Data", package / "App" / "DefaultData"):
        if not path.is_dir():
            errors.append(f"缺少 PortableApps 数据目录：{path.relative_to(package)}")
    appinfo = package / "App" / "AppInfo" / "appinfo.ini"
    launcher_ini = package / "App" / "AppInfo" / "Launcher" / f"{APP_ID}.ini"
    if appinfo.is_file():
        text = appinfo.read_text(encoding="utf-8")
        for value in ("Type=PortableApps.comFormat", f"Version={FORMAT_VERSION}", f"AppID={APP_ID}"):
            if value not in text:
                errors.append(f"appinfo.ini 缺少：{value}")
    if launcher_ini.is_file():
        text = launcher_ini.read_text(encoding="utf-8")
        for value in ("ProgramExecutable=DailyLog\\DailyLog.exe", "DAILY_LOG_STATE_DIR=%PAL:DataDir%\\DailyLog"):
            if value not in text:
                errors.append(f"启动器配置缺少：{value}")
    forbidden = (package / "App" / "DailyLog" / "data", package / "App" / "DailyLog" / "daily-log.db")
    for path in forbidden:
        if path.exists():
            errors.append(f"程序目录不应包含用户数据：{path.relative_to(package)}")
    return errors


def _zip_package(package: Path, output: Path) -> None:
    import zipfile

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.rglob("*")):
            if path.is_dir():
                archive.writestr(path.relative_to(package.parent).as_posix().rstrip("/") + "/", "")
        for path in sorted(package.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package.parent).as_posix())


def build(*, app_dir: Path, output_dir: Path, tools_dir: Path, version: str) -> tuple[Path, Path]:
    if os.name != "nt":
        raise BuildError("PortableApps 产物只能在 Windows 上构建。")
    launcher_dir, installer_dir = _tool_directories(tools_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / APP_ID
    _prepare_staging(app_dir.resolve(), staging, version)
    _generate_launcher(staging, launcher_dir, version)
    paf = _generate_installer(staging, installer_dir, version)
    errors = validate_package(staging)
    if errors:
        raise BuildError("PortableApps 包校验失败：\n" + "\n".join(errors))
    source_dir = staging / "Other" / "Source"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    other_dir = staging / "Other"
    if other_dir.exists() and not any(other_dir.iterdir()):
        other_dir.rmdir()
    zip_path = output_dir / f"{PACKAGE_NAME}.zip"
    _zip_package(staging, zip_path)
    return zip_path, paf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 Daily Log PortableApps 产物")
    parser.add_argument("--app-dir", type=Path, help="PyInstaller one-folder 输出目录")
    parser.add_argument("--output-dir", type=Path, help="产物目录")
    parser.add_argument("--tools-dir", type=Path, default=Path("build/portableapps-tools"))
    parser.add_argument("--version", help="三段式版本号，例如 0.1.0")
    parser.add_argument("--validate", type=Path, help="只校验已有的 PortableApps 根目录")
    args = parser.parse_args(argv)
    try:
        if args.validate:
            errors = validate_package(args.validate.resolve())
            if errors:
                raise BuildError("\n".join(errors))
            print("PortableApps package layout is valid.")
            return 0
        if not args.app_dir or not args.output_dir or not args.version:
            parser.error("构建时必须同时提供 --app-dir、--output-dir 和 --version")
        zip_path, paf_path = build(
            app_dir=args.app_dir,
            output_dir=args.output_dir,
            tools_dir=args.tools_dir,
            version=args.version,
        )
        print(f"Created {zip_path}")
        print(f"Created {paf_path}")
        return 0
    except BuildError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
