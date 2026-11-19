# 西港会所/外送 · AI 协作规则

西港会所/外送项目：React/Vite 前端 + Flask API + Telegram 频道机器人 + MySQL。
业务细节一律以 `README.md` 与 `DEPLOY.md` 为准，本文件只写协作规则与结构。

## 规则

- `docs/规范/`：房规 v1、多AI协作、请求规范（副本；主份在 `D:\project\_规范\`）

## 事实入口

- `README.md`：功能清单、目录结构、数据模型
- `DEPLOY.md`：部署与线上规则（**改前端或接口行为前先读**）
- `bot/config/channels.py`：频道与会所配置
- `bot/migrations/`：MySQL 表结构迁移

## 结构要点

- `miniapp/`：Vite 前端，`/api` 代理到本机后端（默认 `127.0.0.1:8000`）
- `bot/`：Flask API（生产用 Waitress）+ Telegram 机器人 + OCR/人工核对脚本
- `shared/`：前后端共用代码
- `deploy/`：部署脚本
- 数据存 MySQL，默认库名 `venue_db`

## 这台机器上的约定

- `.ps1` 必须存成 **UTF-8 with BOM**，否则 PowerShell 5.1 按 ANSI 解析会报语法错
- 启动 Node 服务用 `npm run preview` / `npm run dev`，不要手搓命令，因为部署监控按命令识别进程
- 前端构建后需重启 API 才生效（本项目的静态资源由后端托管）

## 版本控制

- **提交与推送由 AI 负责，不需要用户手动操作。**
- 远程仓库：`git@github.com:haotian99988866/huisuoxiangcexitong.git`（私有），分支 `main`
- **commit message 全中文，不用英文前缀。** 用中文类型词开头：
  `新增：` / `修复：` / `优化：` / `样式：` / `内容：` / `文档：` / `测试：` / `重构：` / `维护：`
- 推送前确认：`git status` 干净、没有把 `.env` / 数据库 / 照片原片带进仓库
- 受限环境下 push 会因「无法创建 ssh 管道」失败，需申请一次完全访问权限
- 不替用户执行 `push --force`、`reset --hard`、删分支
