# Daily Log 协作约定

项目主要由维护者个人使用，同时维护为陌生用户下载后可以零配置使用核心功能的公开早期版本。没有规模化真实用户时，不把“收集用户反馈”当作可交付目标；以实际使用中发现的缺陷、数据安全和明确需求为依据。

## 项目边界和结构

- 仓库只包含通用客户端源码、网页资源、测试和构建配置，不提交私人记录、数据库、备份包、密钥、测试用户目录或旧仓库历史。
- 新功能必须服务通用客户端，或作为可隔离的个人可选能力实现，不得依赖私人仓库、GitHub Issue 或旧版工作流。
- 默认启动不得读取仓库数据、README、Git 状态或 GitHub Issue；新用户首次启动必须得到空数据库。
- `daily_log/`：SQLite 核心、模型、迁移、备份、投影、桌面壳和入口。
- `web/`：静态 WebUI；写入必须通过本地 HTTP 服务进入 SQLite。
- `scripts/web_server.py`：本地 HTTP 服务和 API；`scripts/web_data.py`：只读数据模型。
- `tests/`：Python 回归测试；`packaging/`：Windows 构建配置。

正式入口为：

```bash
python -m daily_log
python -m daily_log --no-browser
python -m daily_log --data-dir <隔离目录>
python -m daily_log --migrate-from <旧版数据目录>
```

PyInstaller 桌面发布入口是 `daily_log/desktop_entry.py`，正式桌面 EXE 不能只打开浏览器网页。

## SQLite 中心和写入规则

- `daily-log.db` 是唯一可写真源，也是相对于所有 Provider 的中心。
- SQLite 结构可以通过正式迁移演进；“保持不变”指它始终是真源和核心边界，不指结构永久冻结。
- WebUI、桌面壳、AI 和 Provider 必须通过 `DailyLogDatabase` 或核心服务写入，不能直接写 `portable/`。
- 每条记录使用稳定 UUID；更新、删除、完成和恢复按数据库 ID 操作。
- SQLite 记录和 outbox 必须在同一事务中提交；投影、备份、网络或 Provider 失败不得回滚已提交的本地记录。
- 所有可写文件必须位于 `AppPaths.state_dir`；测试使用临时目录、`--data-dir` 或隔离的 `DAILY_LOG_STATE_DIR`。

## 四类记录

- 账目科目使用 `expenses` 或 `expenses:一级:二级`；分类删除必须迁移历史记录或转为未分类；`budget_excluded` 只影响预算进度。
- 日记正文必须原样保存用户表述，不得润色、翻译、改写或删减；标签可为空。
- 待办的创建日期只用于排序；截止日期字段为 `due_date`，投影使用 `due:YYYY-MM-DD`；完成不弹确认且近期完成可恢复。
- 本地日程使用标准 ICS；外部 ICS 订阅只读，缓存失败不能阻止本地日程；点击日期打开当天详情，不得无条件打开新增表单。
- 同一段口述可以同时产生日记、账目、待办和日程；日记正文仍保留原始表达。

## AI 和 Inbox

- AI 是可选输入方式；零配置时手动录入必须完整可用。AI 直接调用用户在本机配置的兼容 API。
- 自然语言录入沿用统一 `plan`，可以同时包含四类记录，并通过一次数据库事务整体写入。
- 日记 `text` 必须是用户原话；LLM 只提取结构化字段，不得概括或转写。
- 普通结果允许 YOLO 自动入库；`clarifications`、格式错误、超时或其他失败不得猜测、不完整写入或丢弃，应进入可编辑/可重试状态。
- Inbox 成功转换后不要求长期保存原文；待处理或失败项目必须暂时保留。
- 远程 Inbox 使用 `Provider + 来源项目 ID` 幂等，不能用文本内容去重；不同来源 ID 的相同文字可以分别入库。
- 成功入库后远程项目可删除、归档、关闭或加标签；即使该动作失败，也要保留最小来源 ID 和已处理状态，避免重复入库。

## Provider、同步和手机桥接

先实现内部可替换接口和内置实现，不立即开放任意第三方插件动态加载。

### 按模块同步

- 日记、账目、待办和日程可以绑定不同 Provider；首版每种记录类型最多一个活动 Provider，架构保留一对多扩展空间。
- Provider 声明实际能力：读取、创建、修改、删除、完成、标签同步和同步方向；不支持的操作不能伪装成功。
- 本地 UUID 是记录身份，外部 ID 只作映射。Provider 负责远端通信和格式转换；核心负责映射、同步状态、冲突规则和 SQLite 写入。
- 实现双向同步前必须明确 API、授权、同步方向、删除语义、重复检测、离线重试、冲突、令牌保存、撤销授权和限流。

### 手机桥接

- 手机桥接与按模块同步是两类独立接口。
- Dashboard 从 SQLite 生成四模块完整只读快照，后台异步发布；Inbox 接收自然语言，电脑启动后拉取并使用现有 `plan` 流程。
- “读取 Inbox”和“发布 Dashboard”是独立能力，可由同一或不同服务提供。
- Dashboard 是可选的完整数据远程副本，必须由用户明确启用并使用具备访问控制的私有空间；远程历史是否保留由服务决定。
- 本地写入成功后再异步发布或拉取；远程失败不能阻塞本地保存。手机和电脑可以异步工作。

第三方任务、日历、笔记、提醒和自动化服务同步尚未实现。本地 SQLite 始终是真源。

## 备份、密钥、启动和发布

- AI、备份存储、同步和手机桥接都应抽象为 Provider；密钥保存在源码仓库外。
- 正式备份后端是本机 ZIP、WebDAV 和 S3 兼容存储；Git 不属于客户端备份后端。
- 备份默认携带 API Key、远程存储密钥和代理凭据；加密整个备份是独立选项。
- 恢复前必须生成 `restore-safety` 安全副本；恢复要同时处理普通设置和默认携带的密钥。
- 只有显式 `--migrate-from` 才允许一次性导入旧数据；`--data-dir` 或 `DAILY_LOG_STATE_DIR` 优先于默认目录。
- 安装版使用 `%LOCALAPPDATA%/DailyLog`；绿色版使用 EXE 旁边的 `data/`；PortableApps 使用 `App/DailyLog/` 和 `Data/DailyLog/`，启动器必须设置 `DAILY_LOG_STATE_DIR=%PAL:DataDir%\\DailyLog`。
- 绿色版 `DailyLog-HumanTest.cmd` 找不到已有 `daily-log.db` 时必须拒绝启动，不得创建测试目录。

## 开发和验证

Windows 普通命令优先使用 Git Bash；管理任务才使用 PowerShell。搜索优先使用 `rg`，文件修改使用补丁方式，不写死本机绝对路径。

提交前至少执行：

```bash
bash scripts/check
git diff --check
```

涉及 JavaScript 时执行 `node --check web/app.js`。涉及启动、目录或迁移时验证帮助、全新隔离目录为空、无迁移时不读取仓库、显式迁移可用且不可重复、服务停止会刷新并关闭 worker。涉及 WebUI 或 Inbox 时，在隔离目录进行真实浏览器验收；涉及发布时检查产物无私人数据、密钥、绝对路径和测试数据库。

## GitHub CI 和 Release

- 普通提交和 Pull Request 运行测试与 Windows 构建；只有推送 `vMAJOR.MINOR.PATCH` 标签时创建正式 Release。
- 标签版本必须与 `daily_log/version.py` 一致。
- Release 至少包含 Windows 安装版、普通绿色版、PortableApps ZIP、PortableApps `.paf.exe` 和 `SHA256SUMS.txt`。
- PortableApps 工具只在构建时下载并校验，不提交二进制；升级包只能替换 `App/`，不得删除 `Data/`。
- 提交或发布前保留并检查用户已有改动；禁止使用会覆盖工作区的命令，除非用户明确要求。
