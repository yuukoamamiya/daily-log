# Daily Log 开发环境

## 运行环境

推荐 Python 3.11 或更高版本。仓库只包含通用 SQLite 客户端及其发布工具。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m daily_log
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m daily_log
```

通用客户端入口不要求 Git Bash。

## 用户数据

默认数据位于 `%LOCALAPPDATA%/DailyLog` 或 `$XDG_STATE_HOME/DailyLog`：

- `daily-log.db`：唯一可写真源。
- `config.ini`：AI、预算、备份和日历订阅设置。
- `portable/`：数据库生成的 Markdown、CSV、todo.txt 和 ICS 投影。
- `backups/`：本机完整备份。
- `exports/`：给其他软件使用的导出文件。
- `restore-safety/`：恢复前自动生成的安全副本。

开发和测试时使用独立目录：

```powershell
.\.venv\Scripts\python.exe -m daily_log --data-dir .\.local-test-profile
```

测试结束后再删除该目录，不要把真实用户目录用于自动化测试。

## 旧数据迁移

默认首次启动创建空数据库，程序不会扫描源码目录。需要从旧版仓库迁移时，在目标用户目录初始化之前运行：

```powershell
.\.venv\Scripts\python.exe -m daily_log --migrate-from "<旧版daily-log目录>"
```

迁移会复制旧文本投影并导入 SQLite；之后只修改用户数据目录。已初始化目录会拒绝重复迁移。

## 检查

Windows 下完整检查仍建议使用 Git Bash：

```bash
bash scripts/check
```

只检查通用 Python 核心：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

启动与隔离相关改动还应手工验证：

```powershell
.\.venv\Scripts\python.exe -m daily_log --help
.\.venv\Scripts\python.exe -m daily_log --data-dir .\.fresh-profile --no-browser
```

确认 `/api/data` 为空且 `.runtime-layout-v1.json` 的 `mode` 为 `empty`。

## 依赖边界

- `requirements-app.txt`：发布客户端的最小运行依赖。
- `requirements.txt`：开发和运行客户端所需依赖。
- 安装包不得包含仓库个人数据、本机 `config.ini`、数据库或备份文件。

下一阶段的打包与发布工作记录在 [TODO.md](TODO.md)。

## Windows 桌面构建

桌面模式复用现有 WebUI 和本地服务，不需要单独的数据迁移流程：

```powershell
.\.venv\Scripts\python.exe -m daily_log --desktop
```

生成 Windows 构建产物需要安装 `requirements-build.txt`，然后在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
pyinstaller --noconfirm --clean packaging\DailyLog.spec
```

上面的命令生成安装版使用的 `dist\DailyLog`。绿色版使用同一 spec，但需要打开便携构建开关：

```powershell
$env:DAILY_LOG_BUILD_PORTABLE = "1"
pyinstaller --noconfirm --clean --distpath dist\portable --workpath build\portable packaging\DailyLog.spec
Copy-Item packaging\DailyLog-HumanTest.cmd dist\portable\DailyLog\DailyLog-HumanTest.cmd
Compress-Archive -Path dist\portable\DailyLog -DestinationPath dist\DailyLog-Windows-Portable.zip -Force
```

绿色版目录为 `dist\portable\DailyLog`，直接运行其中的 `DailyLog.exe` 会把数据保存到同目录的 `data\`。其中的 `DailyLog-HumanTest.cmd` 是给人工验收已有数据用的特殊入口，默认指向已经存在的 `%LOCALAPPDATA%\DailyLog`；找不到 `daily-log.db` 时会拒绝启动，不会创建新数据。需要测试其他已有数据时，可先设置 `DAILY_LOG_EXISTING_DATA_DIR`。

安装版使用 `packaging\DailyLog.iss` 生成，安装到 `{localappdata}\Programs\DailyLog`；安装版数据仍位于 `%LOCALAPPDATA%\DailyLog`，升级和卸载不会删除用户数据。

### PortableApps 构建

在 Windows 上先完成 PyInstaller 绿色版构建，再执行：

```powershell
python packaging\portableapps.py --app-dir dist\portable\DailyLog --output-dir dist\portableapps --tools-dir build\portableapps-tools --version 0.1.0
```

脚本会按固定版本下载并校验 PortableApps.com Launcher 和 Installer，生成 `dist\portableapps\DailyLog-PortableApps.zip` 与 `DailyLog-PortableApps.paf.exe`。工具只放在本机构建缓存中，不提交到仓库。PortableApps 版的可写数据通过启动器指向包内 `Data\DailyLog`；升级只替换 `App`，不删除 `Data`。
