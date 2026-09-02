# Daily Log

[![CI](https://github.com/yuukoamamiya/daily-log/actions/workflows/client-ci.yml/badge.svg)](https://github.com/yuukoamamiya/daily-log/actions/workflows/client-ci.yml)
[![Latest release](https://img.shields.io/github/v/release/yuukoamamiya/daily-log)](https://github.com/yuukoamamiya/daily-log/releases)

Daily Log 是一个本地优先的个人生活记录客户端，把记账、日记、待办和日程放在同一个界面里。所有记录首先保存到本机 SQLite；不配置 AI、不连接网络，也可以完整使用。

项目地址：[github.com/yuukoamamiya/daily-log](https://github.com/yuukoamamiya/daily-log)

当前版本：`v0.1.0`

## 适合记录什么

- 记账：月度预算、预算外支出、一级/二级分类和未分类账目。
- 日记：正文原样保存，标签可选。
- 待办：可选截止日期、逾期高亮、快速改期和完成恢复。
- 日程：月历、本地 ICS 日程和只读外部 ICS 订阅。
- 搜索与整理：全局搜索、列表筛选、批量设置账目分类和记录标签，修改前提供预览。
- AI 录入与历史复核：AI 只作为可选 Provider；复核建议确认后才写入，不能改写日记正文、日期、金额或摘要。
- 备份与恢复：本机 ZIP、WebDAV 和 S3 兼容存储。
- 导出：CSV、Markdown、todo.txt、ICS 和 Org Mode。
- 界面：浅色、深色和跟随系统；Windows 客户端支持系统托盘。

## 下载 Windows 版本

打开 [Releases](https://github.com/yuukoamamiya/daily-log/releases)，按使用方式选择：

- `DailyLog-Setup-*.exe`：安装版，适合日常使用。
- `DailyLog-Windows-Portable.zip`：普通绿色版，解压后运行 `DailyLog.exe`。
- `DailyLog-PortableApps.zip`：PortableApps 格式压缩包。
- `DailyLog-PortableApps.paf.exe`：交给 PortableApps Platform 安装。
- `SHA256SUMS.txt`：发布文件的 SHA-256 校验和。

发布版不需要另行安装 Python。安装版和绿色版启动的是带系统托盘的桌面客户端，而不是只打开浏览器的网页入口。

安装版数据保存在 `%LOCALAPPDATA%\DailyLog`，升级或卸载不会删除用户数据。绿色版默认把数据保存在程序旁边的 `data\`；PortableApps 版把数据保存在包内的 `Data\DailyLog`，更新时只替换 `App`，数据会保留。

窗口关闭时，客户端会先完成必要的本地整理；如有待备份内容，会尝试执行一次限时备份。备份失败不会阻塞退出，下一次启动会继续提示待备份。

## 从源码运行

需要 Python 3.11 或更高版本。

Windows：

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

默认会启动本地服务并打开 `http://127.0.0.1:8765`。只启动服务、不自动打开浏览器：

```bash
python -m daily_log --no-browser
```

开发或测试时使用隔离数据目录：

```bash
python -m daily_log --data-dir <隔离目录>
```

手动录入不需要 AI 配置。AI、备份和日历订阅都可以在设置页按需启用。

## 数据和隐私

SQLite 是客户端唯一可写真源。默认启动会创建空数据库，不会扫描源码仓库，也不会读取 README、Git 状态或 GitHub Issue；个人记录不会上传到本项目的 GitHub 仓库。

默认数据目录：

- Windows：`%LOCALAPPDATA%\DailyLog`
- Linux/macOS：`$XDG_STATE_HOME/DailyLog`；未设置时为 `~/.local/state/DailyLog`

数据目录主要包含：

- `daily-log.db`：SQLite 数据库，唯一可写真源。
- `config.ini`：本机设置、AI 配置、备份配置和日历订阅。
- `portable/`：由数据库后台生成的文本投影。
- `backups/`：本机备份归档。
- `exports/`：导出文件。
- `restore-safety/`：恢复或迁移前生成的安全副本。

API Key、WebDAV/S3 凭据和代理凭据只保存在用户数据目录，不会写入源码仓库。备份默认会携带这些设置；“加密整个备份”是独立选项。发布包和默认数据模板不包含任何用户密钥。

## 从旧版本迁移

旧版文本数据只有在用户显式提供 `--migrate-from` 时才会导入，而且只能导入一次：

```bash
python -m daily_log --migrate-from "<旧版数据目录>"
```

目标数据目录已经初始化时，程序会拒绝重复迁移，以避免重复记录或覆盖现有数据。日常备份和恢复请使用客户端自己的备份功能。

## 开发和发布

本地完整检查：

```bash
bash scripts/check
git diff --check
node --check web/app.js
```

开发环境、目录结构和 Windows 构建说明见 [SETUP.md](SETUP.md)，未完成路线见 [TODO.md](TODO.md)。

GitHub Actions 会在普通提交和 Pull Request 中运行测试与 Windows 构建；只有推送形如 `vMAJOR.MINOR.PATCH` 的标签时才创建正式 Release。标签版本必须与 `daily_log/version.py` 一致，Release 包含安装版、绿色版、PortableApps 包和校验和。

第三方任务、日历、笔记、提醒和自动化服务的双向同步目前尚未实现；本地 SQLite 始终是未来连接器的真源。

## 许可证

本项目使用 [MIT License](LICENSE)。第三方依赖和许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
