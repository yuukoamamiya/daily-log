# Daily Log WebUI

`web/` 是通用客户端的本地界面，由同一台机器上的 Python 服务提供。它不是独立云端网页，也不直接访问 GitHub、SQLite 文件或第三方 API。

## 启动

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m daily_log
```

默认地址是 <http://127.0.0.1:8765>。

## 数据流

1. 表单或 AI 请求调用本地 HTTP API。
2. 服务在 SQLite 事务中保存记录和 outbox。
3. 页面立即刷新数据库状态。
4. 后台 worker 在空闲时生成便携文本投影并执行配置的备份。

界面不要求 AI。账目分类和日记标签均允许留空，之后可以人工编辑或批量整理。

## 文件

- `index.html`：应用外壳和录入抽屉。
- `styles.css`：工作台视觉系统和响应式布局。
- `app.js`：视图渲染、月历、表单、乐观更新和接口调用。

桌面封装将继续复用这套 WebUI，不另写一套页面。
