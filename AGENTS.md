# Daily Log 开发约定

## 当前目标

先把通用客户端做到可以公开发布和供他人使用，再增加 GitHub Issue、邮件 inbox、桌面便签等维护者个人功能。新增功能必须优先服务通用客户端，不能重新把客户端绑定到当前私人仓库。

## 架构边界

项目只有一套通用客户端：`daily_log/` 本地核心、`web/` 界面和 `python -m daily_log` 启动入口。

客户端不得在默认启动流程中读取仓库数据、README、Git 状态或 GitHub Issue。新用户首次启动必须创建空数据库。旧版文本数据只有在用户显式提供 `--migrate-from` 时才能导入，而且只能导入一次。

## 真源与写入流程

- SQLite `daily-log.db` 是客户端唯一可写真源。
- WebUI、桌面壳和 AI 录入都必须调用 `DailyLogDatabase`，不得绕过数据库直接写投影文件。
- 每条记录使用稳定 UUID。
- 数据与 outbox 必须在同一 SQLite 事务提交；HTTP 保存成功后立即返回。
- `ProjectionWorker` 在后台把 outbox 幂等投影到用户数据目录的 `portable/`。
- 投影、备份、网络或 Git 失败不得回滚已经提交的本地记录。
- 客户端所有可写文件必须位于 `AppPaths.state_dir`，不能写入源码或安装目录。

默认用户目录：Windows 为 `%LOCALAPPDATA%/DailyLog`，Linux 为 `$XDG_STATE_HOME/DailyLog` 或 `~/.local/state/DailyLog`。测试必须使用隔离的临时目录或 `--data-dir`，禁止污染真实用户数据。

## 四类数据规则

### 记账

- 科目使用 `expenses` 或 `expenses:一级:二级`。
- 一级分类和二级分类都不是录入必填项；留空表示未分类 `expenses`。
- 分类删除必须迁移历史记录，或由用户明确选择转为未分类。
- `budget_excluded` 只影响预算进度，不影响支出总额与分类统计。

### 日记

- 正文必须原样保存用户表述，不得润色、改写、翻译或删减。
- 标签可为空；AI 可以事后为无标签记录补充标签。
- 同一段口述可以同时产生日记、账目、待办和日程，日记正文仍保留原文。

### 待办

- 创建日期只用于内部排序。
- 用户可设置可选截止日期，数据库字段为 `due_date`，便携文本投影为 `due:YYYY-MM-DD`。
- 已逾期待办置顶并高亮；完成操作不弹确认，近期完成必须可以恢复。

### 日程

- 本地日程使用标准 ICS 投影。
- 外部订阅只读，支持任意 ICS 地址、显示/隐藏、刷新和删除。
- 订阅缓存失败不能阻止本地日程使用。

## AI、备份与密钥

- AI 是可选输入方式，手动录入在零配置下必须完整可用。
- AI 录入直接调用用户在本机 `config.ini` 配置的兼容 API，不通过 GitHub Issue。
- AI 结果允许直接写库，写错后由普通编辑功能纠正。
- API 密钥、WebDAV 和 S3 凭据保存在源码仓库外。
- “备份密钥”和“加密整个备份”是两个独立选项；用户可以选择明文备份密钥。
- 正式备份后端为本机 ZIP、WebDAV 和 S3；Git 不属于客户端备份后端。
- 恢复前必须生成当前状态的安全副本。

## 启动与迁移

通用源码入口：

```bash
python -m daily_log
python -m daily_log --no-browser
python -m daily_log --data-dir <隔离目录>
python -m daily_log --migrate-from <旧仓库目录>
```

默认启动不得传入仓库根目录作为迁移源。

### 桌面发布与人工测试

- PyInstaller 的正式入口是 `daily_log/desktop_entry.py`，必须启动 pywebview 桌面窗口和托盘；不能把发布 EXE 配成只打开浏览器网页的 Web 入口。
- 安装版使用 `%LOCALAPPDATA%/DailyLog`；绿色版默认使用 EXE 旁边的 `data/`。安装包和绿色版不得包含仓库私人 `data/`、数据库或密钥。
- PortableApps 版使用 PortableApps Format 3.9：包根目录启动器为 `DailyLogPortable.exe`，程序位于 `App/DailyLog/`，数据位于 `Data/DailyLog/`，默认数据模板目录为 `App/DefaultData/`。启动器必须通过 `[Environment]` 设置 `DAILY_LOG_STATE_DIR=%PAL:DataDir%\DailyLog`，不得把数据写回 `App/`。
- PortableApps 产物由 `packaging/portableapps.py` 生成 ZIP 和 `.paf.exe`；官方 Launcher/Installer 只在构建时下载并校验，不提交二进制工具。升级只能替换 `App/`，不得删除 `Data/`。
- 本机已校验 PortableApps 包结构、启动器冒烟和 ZIP 完整性；正式发布前还必须在真实 PortableApps Platform 中人工验收 `.paf.exe` 安装、升级和数据保留。
- 绿色版的 `DailyLog-HumanTest.cmd` 是复用已有数据的特殊人工测试入口：默认读取已经存在的 `%LOCALAPPDATA%/DailyLog/daily-log.db`，也可由 `DAILY_LOG_EXISTING_DATA_DIR` 指定；目标不存在时必须拒绝启动，不得创建测试目录。
- 设置页的数据目录迁移只接受用户选择的空目录，迁移前生成 `restore-safety` 安全副本，完成后通过默认 profile 指向文件在重启时生效；显式 `--data-dir` 或 `DAILY_LOG_STATE_DIR` 优先，迁移不能覆盖已有目标或安装/系统目录。

### 日历交互约定

- 点击日期打开当天详情面板，不得无条件打开新增日程表单。
- 当天详情按日记、支出、日程、待办分区；点击已有记录进入对应的查看/编辑流程，外部订阅日程只读。
- “新增日记 / 新增支出 / 新增待办 / 新增日程”必须是独立操作，并把详情面板选中的日期带入表单。

## 开发要求

- Windows 普通命令优先使用 Git Bash；Windows 管理任务使用 PowerShell。不要把 Bash 语法交给 PowerShell。
- 文本搜索优先使用 `rg`。
- 文件修改使用补丁方式，保留用户已有改动。
- 不写死本机绝对路径。
- 不提交 `.venv`、`config.ini`、密钥、数据库、备份包或测试用户目录。
- 数据模型和迁移必须向后兼容，并为旧字段增加回归测试。
- 对外接口错误要返回可理解的中文消息，不能泄露密钥或完整远端响应。

## 验证标准

提交前至少执行：

```bash
bash scripts/check
```

涉及启动、数据目录或迁移时，还必须验证：

1. `python -m daily_log --help` 可用。
2. 全新隔离用户目录启动后四类数据均为空。
3. 不带 `--migrate-from` 时不会导入仓库 `data/`。
4. 显式迁移可以导入旧数据，已初始化目录会拒绝重复迁移。
5. 服务停止时会刷新并安全关闭后台 worker。

涉及 UI 时应在隔离用户目录进行真实浏览器操作测试。涉及备份恢复时必须检查恢复前安全副本和路径边界。

## 文档职责

- `README.md`：面向普通用户的项目介绍和启动方式。
- `SETUP.md`：面向开发者的环境、测试和目录说明。
- `TODO.md`：尚未完成的发布路线，不记录已经完成的流水账。
