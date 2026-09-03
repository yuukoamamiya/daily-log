# Daily Log

[![CI](https://github.com/yuukoamamiya/daily-log/actions/workflows/client-ci.yml/badge.svg)](https://github.com/yuukoamamiya/daily-log/actions/workflows/client-ci.yml)
[![Latest release](https://img.shields.io/github/v/release/yuukoamamiya/daily-log)](https://github.com/yuukoamamiya/daily-log/releases)

Daily Log 是一个本地优先的个人生活记录客户端，把记账、日记、待办和日程放在同一个界面里。SQLite 是本地数据的唯一真源；不配置 AI、不连接网络，也可以完整使用。

项目主要由我个人使用，同时维护为陌生用户下载后可以零配置使用核心功能的公开早期版本。它没有经过大规模真实用户验证；后续工作以实际使用中发现的问题和明确需求为依据，不为了完成泛化路线图而扩张功能。

当前版本：`v0.1.1`

## 可以记录什么

- 记账：月度预算、预算外支出、一级/二级分类和未分类账目。
- 日记：正文尽可能原样保存，标签可选。
- 待办：可选截止日期、逾期高亮、快速改期和完成恢复。
- 日程：月历、本地 ICS 日程和只读外部 ICS 订阅。
- 搜索与整理：全局搜索、列表筛选，以及经过预览确认的批量分类和标签修改。
- AI 录入：用自然语言同时提取日记、账目、待办和日程，直接写入本地数据库。
- 历史复核：AI 可以提出分类和标签建议，确认后才应用，不改写日记原文、日期、金额或摘要。
- 备份与恢复：本机 ZIP、WebDAV 和 S3 兼容存储。
- 导出：CSV、Markdown、todo.txt、ICS 和 Org Mode。
- 界面：浅色、深色和跟随系统；Windows 客户端支持系统托盘。

## AI 自然语言录入和 Inbox

配置兼容的 AI API 后，可以输入一段自然语言，让 AI 生成统一的 `plan`。一段输入可以同时包含四类记录，然后由 SQLite 核心一次性写入。

日记正文必须保留用户的原始表达，不能被概括、翻译、润色或改写；LLM 只负责提取类型、日期、金额、标签等结构化字段。日期、时间或金额有歧义时不得自行猜测。

现有版本已经支持本地自然语言直接入库。专门的 Inbox 界面会展示自然语言转为结构化结果的过程：明确结果允许 YOLO 自动入库，有歧义或处理失败时进入可编辑、可重试状态。一次输入生成多个模块的记录时必须整体写入，不能部分成功。

## Provider 和手机桥接

Provider 有两条独立路线。

### 按模块同步

日记、账目、待办和日程可以分别选择 Provider，例如待办使用任务服务、日程使用日历服务。首版每种记录类型最多启用一个活动 Provider，其他 Provider 可以配置但停用；核心接口保留将来一对多同步的可能。

每个 Provider 必须声明实际支持的读取、创建、修改、删除、完成、标签同步和同步方向。Daily Log 的本地 UUID 是记录身份，外部 ID 只作为映射；Provider 负责外部 API 通信和格式转换，本地核心负责同步状态、冲突规则和 SQLite 写入。

### 手机桥接

手机桥接面向整个 Daily Log，不要求某个模块 Provider 支持所有数据：

- Dashboard：从 SQLite 生成四个模块的完整只读快照，后台异步发布到手机可访问的私有空间。
- Inbox：手机写入自然语言，电脑启动后拉取，再通过现有 AI `plan` 流程处理。

“读取 Inbox”和“发布 Dashboard”是独立能力，可以由同一个服务或不同服务提供。GitHub 私密仓库、GitHub Issue、Simplenote 等只是候选载体，具体方案仍需评估。手机和电脑可以异步工作，电脑不运行时手机输入继续留在远程 Inbox。

成功入库后，远程 Inbox 项目可以删除、归档、关闭或加标签，具体由 Provider 决定。必须使用 `Provider + 来源项目 ID` 保证幂等，避免同一输入重复生成记录。Dashboard 只要求手机默认看到最新快照，远程服务是否保留历史由其自身决定。

当前代码已经提供内部 `MobileBridgeProvider` 接口和内置 Mock Provider：Dashboard 快照由核心 SQLite 读模型生成，包含账目、日记、待办和日程四个模块；Inbox 拉取按 `Provider + 来源项目 ID` 幂等写入本地 Inbox。发布和拉取分别捕获失败，并由后台 worker 重试。真实远程载体尚未接入，Mock 仅用于离线开发和回归测试。

## Windows 下载

打开 [Releases](https://github.com/yuukoamamiya/daily-log/releases)，按使用方式选择：

- `DailyLog-Setup-*.exe`：安装版。
- `DailyLog-Windows-Portable.zip`：普通绿色版。
- `DailyLog-PortableApps.zip`：PortableApps 格式压缩包。
- `DailyLog-PortableApps.paf.exe`：交给 PortableApps Platform 安装。
- `SHA256SUMS.txt`：发布文件的 SHA-256 校验和。

发布版不需要另行安装 Python。安装版数据保存在 `%LOCALAPPDATA%\DailyLog`，绿色版默认保存在程序旁边的 `data\`；PortableApps 版数据保存在包内的 `Data\DailyLog`，更新时只替换 `App`。

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

## 数据、隐私和迁移

默认启动会创建空数据库，不会扫描源码仓库，也不会读取 README、Git 状态或 GitHub Issue。默认数据目录为 Windows 的 `%LOCALAPPDATA%\DailyLog`，Linux/macOS 的 `$XDG_STATE_HOME/DailyLog`，未设置时为 `~/.local/state/DailyLog`。

数据目录主要包含 `daily-log.db`、`config.ini`、`portable/`、`backups/`、`exports/` 和 `restore-safety/`。API Key、WebDAV/S3 凭据和代理凭据只保存在用户数据目录，不会写入源码仓库。备份默认会携带这些设置；加密整个备份是独立选项。

旧版文本数据只有在用户显式提供 `--migrate-from` 时才会导入，而且只能导入一次：

```bash
python -m daily_log --migrate-from "<旧版数据目录>"
```

目标数据目录已经初始化时，程序会拒绝重复迁移。日常备份和恢复请使用客户端自己的备份功能。

## 开发和发布

本地完整检查：

```bash
bash scripts/check
git diff --check
node --check web/app.js
```

开发环境和 Windows 构建说明见 [SETUP.md](SETUP.md)，未完成路线见 [TODO.md](TODO.md)。GitHub Actions 在普通提交和 Pull Request 中运行测试与 Windows 构建；只有推送 `vMAJOR.MINOR.PATCH` 标签时才创建正式 Release。

## 许可证

本项目使用 [MIT License](LICENSE)。第三方依赖和许可证说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
