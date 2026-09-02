# Daily Log 协作约定

本文档是参与本仓库开发的 Agent 和维护者的工作边界。项目的目标是维护一个可以公开下载、独立运行、可靠保存个人数据的本地优先 SQLite 客户端。

## 项目边界

- 本仓库只包含通用客户端的源码、网页资源、测试和构建配置。
- 不得提交私人记录、数据库、备份包、API 密钥、远程存储凭据、代理凭据、测试用户目录或旧仓库历史。
- 新功能必须服务通用客户端，不得依赖维护者的私人仓库、GitHub Issue 或旧版 hledger/jrnl/todo.txt/khal 工作流。
- 默认启动不得读取仓库数据、README、Git 状态或 GitHub Issue；新用户首次启动必须得到空数据库。

## 代码结构

- `daily_log/`：SQLite 核心、数据模型、迁移、备份、投影、桌面壳和启动入口。
- `web/`：静态 WebUI。所有写入操作都通过本地 HTTP 服务进入 SQLite 核心。
- `scripts/web_server.py`：本地 HTTP 服务和 API。
- `scripts/web_data.py`：从 SQLite 构建 WebUI 只读数据模型。
- `tests/`：Python 回归测试。
- `packaging/`：PyInstaller、安装包、绿色版和 PortableApps 构建配置。
- `README.md`：普通用户文档；`SETUP.md`：开发与发布说明；`TODO.md`：未完成路线。

正式源码入口为：

```bash
python -m daily_log
python -m daily_log --no-browser
python -m daily_log --data-dir <隔离目录>
python -m daily_log --migrate-from <旧版数据目录>
```

PyInstaller 桌面发布入口是 `daily_log/desktop_entry.py`，不能把正式桌面 EXE 配置成只打开浏览器网页的入口。

## 数据真源和写入规则

- `daily-log.db` 是客户端唯一可写真源。
- WebUI、桌面壳和 AI 录入必须调用 `DailyLogDatabase`，不得绕过数据库直接写 `portable/` 投影文件。
- 每条记录使用稳定 UUID；更新、删除、完成和恢复都按数据库 ID 操作。
- SQLite 记录和 outbox 必须在同一事务中提交。
- HTTP 保存成功后立即返回；`ProjectionWorker` 在后台幂等地刷新用户数据目录中的 `portable/`。
- 投影、备份、网络或 Git 失败不得回滚已经提交的本地记录。
- 客户端所有可写文件必须位于 `AppPaths.state_dir`，不能写入源码目录或安装目录。
- 测试必须使用临时目录、`--data-dir` 或隔离的 `DAILY_LOG_STATE_DIR`，不得污染真实用户数据。

默认数据目录：Windows 为 `%LOCALAPPDATA%/DailyLog`；Linux/macOS 为 `$XDG_STATE_HOME/DailyLog`，未设置时为 `~/.local/state/DailyLog`。

## 四类记录约定

### 账目

- 科目使用 `expenses` 或 `expenses:一级:二级`。
- 一级分类、二级分类都不是必填项；留空表示未分类 `expenses`。
- 删除分类必须迁移历史记录，或明确将历史记录转为未分类。
- `budget_excluded` 只影响预算进度，不影响支出总额和分类统计。

### 日记

- 正文必须原样保存用户表述，不得润色、翻译、改写或删减。
- 标签可以为空；AI 可以事后为无标签记录建议标签。
- 整理和历史复核可以提出账目分类、日记标签和待办标签修改，但必须先展示预览，再经 `DailyLogDatabase` 批量写入。
- 历史复核不得修改日记原文、日期、金额或摘要；建议需要可追溯，失败批次需要可重试。
- 同一段口述可以同时产生日记、账目、待办和日程，日记正文仍保留原文。

### 待办

- 创建日期只用于内部排序。
- 截止日期是可选的，数据库字段为 `due_date`，便携投影使用 `due:YYYY-MM-DD`。
- 逾期待办置顶并高亮；完成不弹确认，近期完成记录必须可以恢复。

### 日程

- 本地日程使用标准 ICS 投影。
- 外部 ICS 订阅只读，支持显示/隐藏、刷新和删除；订阅缓存失败不能阻止本地日程使用。
- 点击日期打开当天详情，不得无条件打开新增日程表单；新增日记、支出、待办和日程必须是独立操作。
- 第三方任务、日历、笔记、提醒和自动化服务同步尚未实现。实现前必须明确协议/API、授权成本、同步方向、删除语义、冲突解决、重复检测、离线重试、令牌保存、撤销授权和限流策略。本地 SQLite 始终是真源。

## AI、Provider、备份和密钥

- AI 是可选输入方式；零配置时手动录入必须完整可用。
- AI 直接调用用户在本机配置的兼容 API，不通过 GitHub Issue。
- 外部 AI、备份和未来同步连接器应抽象为内部 Provider，并统一遵守本地真源、超时、错误隔离和凭据边界。
- API Key、WebDAV/S3 凭据和代理凭据必须保存在源码仓库外。
- 正式备份后端是本机 ZIP、WebDAV 和 S3 兼容存储；Git 不属于客户端备份后端。
- 备份默认携带 API Key、远程存储密钥和代理凭据；“加密整个备份”是独立选项。发布产物和默认数据模板不得预置用户密钥。
- 恢复前必须生成当前状态的安全副本；恢复时要同时处理普通设置和默认携带的密钥。
- 任何远程服务失败都只能影响对应网络操作，不能阻止本地记录保存。

## 启动、迁移和发布数据目录

- 只有用户显式提供 `--migrate-from` 时才允许导入旧版文本数据，而且只能导入一次；不得把当前仓库目录当作默认迁移源。
- `--data-dir` 或 `DAILY_LOG_STATE_DIR` 优先于默认用户目录。
- 设置页迁移只接受空目标目录，迁移前生成 `restore-safety` 安全副本，不能覆盖已有目标或安装/系统目录。
- 安装版使用 `%LOCALAPPDATA%/DailyLog`；绿色版默认使用 EXE 旁边的 `data/`。
- PortableApps 版遵循 PortableApps Format 3.9：启动器位于包根目录，程序在 `App/DailyLog/`，用户数据在 `Data/DailyLog/`，默认模板在 `App/DefaultData/`。启动器必须设置 `DAILY_LOG_STATE_DIR=%PAL:DataDir%\DailyLog`，不得把数据写回 `App/`。
- 绿色版的 `DailyLog-HumanTest.cmd` 只能复用已经存在的数据目录；找不到 `daily-log.db` 时必须拒绝启动，不得创建测试目录。

## 开发和验证

Windows 普通命令优先使用 Git Bash；Windows 管理任务使用 PowerShell。文本搜索优先使用 `rg`，文件修改使用补丁方式，不写死本机绝对路径。

提交前至少执行：

```bash
bash scripts/check
git diff --check
```

涉及 JavaScript 时还应执行：

```bash
node --check web/app.js
```

涉及启动、数据目录或迁移时必须验证：

1. `python -m daily_log --help` 可用。
2. 全新隔离目录启动后账目、日记、待办、日程均为空。
3. 不带 `--migrate-from` 时不会导入仓库中的任何数据。
4. 显式迁移可以导入旧数据，已初始化目录会拒绝重复迁移。
5. 服务停止时会刷新并安全关闭后台 worker。

涉及 WebUI 时，在隔离目录进行真实浏览器操作，至少覆盖相关页面的查看、输入、筛选、保存、错误提示和刷新状态。涉及备份恢复时检查恢复前安全副本和路径边界。涉及发布时确认产物不含私人数据、密钥、绝对路径和测试数据库。

## GitHub CI 和 Release

- 普通提交和 Pull Request 运行测试与 Windows 构建。
- 只有推送 `vMAJOR.MINOR.PATCH` 标签时创建正式 Release。
- 标签版本必须与 `daily_log/version.py` 中的 `__version__` 一致。
- Release 至少包含 Windows 安装版、普通绿色版、PortableApps ZIP、PortableApps `.paf.exe` 和 `SHA256SUMS.txt`。
- 官方 PortableApps Launcher/Installer 只在构建时下载并校验，不提交二进制工具。
- 升级 PortableApps 包只能替换 `App/`，不得删除 `Data/`。

提交或发布前保留并检查用户已有改动；禁止使用 `git reset --hard`、`git checkout --` 等会覆盖工作区的操作，除非用户明确要求。
