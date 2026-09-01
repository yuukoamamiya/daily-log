# Daily Log

Daily Log 是一个本地优先的个人生活记录工具，把记账、日记、待办和日程放在同一个界面里。所有手动操作都可以在不接入 AI 的情况下完成；也可以配置兼容 OpenAI API 的模型，把一段混合描述自动拆成多类记录。

当前版本已经完成可发布客户端的 P0–P1 功能，WebUI、本地核心和 Windows 桌面客户端可以日常使用；正式 GitHub Release 仍需最后的安装包验收。

## 已有功能

- 本机 SQLite 是唯一可写真源，保存后立即返回，文本投影在后台生成。
- 记账支持月度预算、预算外支出、一级/二级分类、分类维护和未分类记录。
- 日记正文原样保存，标签可选，可在事后编辑或交给 AI 补充。
- 待办支持可选截止日期、逾期高亮、快速改期、近期完成和恢复误操作。
- 日程支持月历、ICS 文件以及中国节假日等外部日历订阅。
- AI 录入直接调用用户配置的 API，不依赖 GitHub Issue。
- 支持本机 ZIP、WebDAV、S3 兼容存储、手动/闲时备份和恢复。
- 支持导出账目 CSV、日记 Markdown、todo.txt、ICS 和 Org。
- 日历点击日期会打开“当天详情”，可以分别查看并编辑当天的日记、支出、日程和待办；新增操作使用独立按钮。

## 数据与程序的边界

通用客户端不会读取或修改源码仓库中的个人数据。新用户首次启动时始终得到空数据库。

| 范围 | 作用 | 是否属于发布客户端 |
| --- | --- | --- |
| `daily_log/` | SQLite、校验、备份、AI、投影等本地核心 | 是 |
| `web/` | 本地 WebUI | 是 |
| `%LOCALAPPDATA%/DailyLog` | 数据库、配置、备份和便携投影 | 用户本机数据 |
| `portable/` | 数据库生成的可移植文本投影 | 用户本机数据 |

程序默认数据目录：

- Windows：`%LOCALAPPDATA%\DailyLog`
- Linux：`$XDG_STATE_HOME/DailyLog`，未设置时为 `~/.local/state/DailyLog`

其中 `daily-log.db` 是真源，`config.ini` 保存本机设置，`portable/` 保存便携文本投影，`backups/` 和 `exports/` 保存本机输出。设置页的“高级设置 → 数据目录”可以把整套数据复制到用户指定的空目录；迁移前会留下安全副本，重启后生效，原目录不会被删除。使用 `--data-dir` 或 `DAILY_LOG_STATE_DIR` 启动时，以显式指定为准，设置页不会覆盖它。

## 从源码启动

目前需要 Python 3.11 或更高版本。下面的命令可以在 PowerShell、Windows Terminal 或普通命令行中执行，不要求 Git Bash。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m daily_log
```

默认打开 <http://127.0.0.1:8765>。只启动后台服务：

```powershell
.\.venv\Scripts\python.exe -m daily_log --no-browser
```

安装桌面依赖后，也可以使用窗口和系统托盘。安装版的数据位于 `%LOCALAPPDATA%\DailyLog`，升级和卸载都不会触碰这里：

```powershell
.\.venv\Scripts\python.exe -m daily_log --desktop
```

`python -m daily_log` 是客户端的正式源码入口。

Windows 安装包中的 `DailyLog.exe` 直接打开 pywebview 桌面客户端（带系统托盘），不是浏览器网页；安装版数据位于 `%LOCALAPPDATA%\DailyLog`，升级和卸载都不会删除数据。

绿色版直接运行目录中的 `DailyLog.exe` 时，数据位于绿色版目录下的 `data\`。绿色版压缩包还附带 `DailyLog-HumanTest.cmd`，用于人工测试已有数据：它默认指向已经存在的 `%LOCALAPPDATA%\DailyLog`，找不到其中的 `daily-log.db` 就会拒绝启动，不会创建新数据。也可以通过 `DAILY_LOG_EXISTING_DATA_DIR` 指向另一套已有客户端数据；这个入口只复用已有数据，不会自动新建测试 profile。

PortableApps 版有两种发布产物：`DailyLogPortable.zip` 可直接解压到任意目录，`DailyLogPortable.paf.exe` 可交给 PortableApps Platform 安装。它们都启动桌面客户端而不是浏览器网页；数据位于包内的 `Data\DailyLog`，升级时 `App` 程序目录会替换，但 `Data` 会保留。PortableApps 版的启动文件是包根目录的 `DailyLogPortable.exe`。当前已完成包结构、启动器和压缩包完整性校验；正式发布前仍需在真实 PortableApps Platform 中人工验收安装、升级和数据保留。

## 从旧仓库迁移

旧版 hledger、jrnl、todo.txt 和 ICS 数据不会被自动导入。需要迁移时，必须在目标用户目录第一次初始化之前显式执行：

```powershell
.\.venv\Scripts\python.exe -m daily_log --migrate-from "D:\旧版daily-log目录"
```

已初始化的用户目录不会再次接受旧数据导入，避免重复记录或意外覆盖。已有客户端数据请使用设置页的备份恢复功能迁移。

## 开发与检查

```bash
bash scripts/check
```

完整检查包括数据格式、SQLite 核心、备份恢复、Web 接口和运行时隔离测试。开发环境说明见 [SETUP.md](SETUP.md)，后续工作见 [TODO.md](TODO.md)。

## 当前状态

- 本地核心与 WebUI：可用
- 新用户空数据隔离：已完成
- 旧数据显式迁移：已完成
- 独立 Python 模块入口：已完成
- pywebview、托盘、安装包、普通绿色版和 PortableApps 版：已完成，本机 EXE/PortableApps 启动器冒烟通过，正式 Release 待安装、升级和数据保留验收
- P0 首次使用与发布阻塞项：已完成
- P1 桌面壳、Windows 构建、数据目录迁移和日历详情：已完成
- 正式 GitHub Release：待完成

项目目前优先保证数据可靠、离线可用和可恢复，再逐步增加桌面体验与个人定制功能。
