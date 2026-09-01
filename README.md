# Daily Log

Daily Log 是一个本地优先的个人生活记录工具，把记账、日记、待办和日程放在同一个界面里。SQLite 是本地唯一真源；不配置 AI 或网络服务也可以完整使用。

当前稳定版本为 `v0.1.0`，Windows 安装包、绿色版和 PortableApps 版已经通过 CI 构建并发布。

- 公开仓库：<https://github.com/yuukoamamiya/daily-log>
- `v0.1.0` 下载：<https://github.com/yuukoamamiya/daily-log/releases/tag/v0.1.0>
- 全部 Releases：<https://github.com/yuukoamamiya/daily-log/releases>

## 下载并使用

普通 Windows 用户打开 [v0.1.0 Release](https://github.com/yuukoamamiya/daily-log/releases/tag/v0.1.0)，按需要选择：

- `DailyLog-Setup-*.exe`：安装版，适合日常使用。
- `DailyLog-portable.zip`：普通绿色版，解压后直接运行 `DailyLog.exe`。
- `DailyLogPortable.zip`：PortableApps 格式压缩包。
- `DailyLogPortable.paf.exe`：交给 PortableApps Platform 安装。
- `SHA256SUMS.txt`：所有发布文件的 SHA-256 校验和。

安装版和绿色版都启动带系统托盘的桌面客户端，不需要单独安装 Python。安装版数据保存在 `%LOCALAPPDATA%\DailyLog`；绿色版默认把数据保存在程序目录下的 `data\`。升级或卸载安装版不会删除用户数据。

PortableApps 版的数据保存在包内的 `Data\DailyLog`，更新程序时只替换 `App`，数据会保留。它同样启动桌面客户端，而不是只打开浏览器网页。

## 功能

- 记账：月度预算、预算外支出、一级/二级分类和未分类记录。
- 日记：正文原样保存，标签可选，也可以事后编辑或让 AI 补充标签。
- 待办：可选截止日期、逾期高亮、快速改期，以及近期完成记录的恢复。
- 日程：月历、本地 ICS 投影和外部 ICS 订阅。
- AI 录入：调用用户在本机配置的兼容 OpenAI API；不依赖 GitHub Issue。
- 备份恢复：本机 ZIP、WebDAV 和 S3 兼容存储。
- 导出：账目 CSV、日记 Markdown、todo.txt、ICS 和 Org。
- 日历交互：点击日期打开当天详情，可分别查看和编辑当天的四类记录；新增操作使用独立按钮。

## 数据位置与隐私

首次启动会创建空数据库，不会读取源码仓库、Git 状态、README 或其他用户文件。客户端也不要求 GitHub 账号，不会把个人记录上传到本项目的 GitHub 仓库。

默认数据目录如下：

- Windows：`%LOCALAPPDATA%\DailyLog`
- Linux：`$XDG_STATE_HOME/DailyLog`；未设置时为 `~/.local/state/DailyLog`

数据目录中的主要内容：

- `daily-log.db`：SQLite 数据库，唯一可写真源。
- `config.ini`：本机设置；API 密钥和远程备份凭据只保存在用户本机。
- `portable/`：后台从数据库生成的便携文本投影。
- `backups/`、`exports/`：本机备份和导出文件。

可以通过设置页的“高级设置 → 数据目录”迁移整套数据，也可以用 `--data-dir` 或 `DAILY_LOG_STATE_DIR` 指定目录。迁移只接受空目标目录，并会在迁移前留下安全副本。

## 从源码启动

需要 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m daily_log
```

默认会打开 <http://127.0.0.1:8765>。只启动后台服务：

```powershell
.\.venv\Scripts\python.exe -m daily_log --no-browser
```

使用隔离的数据目录：

```powershell
.\.venv\Scripts\python.exe -m daily_log --data-dir C:\path\to\daily-log-data
```

源码入口 `python -m daily_log`、桌面入口和发布版使用同一套 SQLite 核心。手动录入不需要 AI 配置。

## 从旧版本迁移

早期版本使用 hledger、jrnl、todo.txt 和 khal；这些工具不是当前客户端的运行依赖，也不会在默认启动时被调用。若仍保留旧版本数据，只有在新数据目录尚未初始化时，显式指定 `--migrate-from` 才会执行一次性迁移：

```powershell
.\.venv\Scripts\python.exe -m daily_log --migrate-from "C:\path\to\old-daily-log"
```

已经初始化的目录会拒绝再次迁移，避免重复记录或覆盖现有数据。日常备份和恢复请使用客户端自己的备份功能。

## 开发与发布

本地完整检查：

```bash
bash scripts/check
```

开发环境、测试和目录说明见 [SETUP.md](SETUP.md)，后续路线见 [TODO.md](TODO.md)。

每次提交和 Pull Request 会运行客户端测试与 Windows 构建。推送匹配 `vMAJOR.MINOR.PATCH` 的标签时，GitHub Actions 会校验源码版本、运行测试、构建并发布安装版、绿色版、PortableApps 包和 SHA-256 校验和。

## 许可证

项目许可证见 [LICENSE](LICENSE)，第三方依赖说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
