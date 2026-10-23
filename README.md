# 会所信息展示与 Telegram 频道机器人

本项目包含 React/Vite 前端、Flask API、Telegram 频道机器人和 MySQL 数据库，用于展示会所资料、服务项目及技师信息，并从指定频道接收带标签的图片来更新会所封面。


## 项目结构

```text
.
├── bot/
│   ├── api_server.py          # Flask API，生产运行使用 Waitress
│   ├── main.py                # Telegram 频道照片监听机器人
│   ├── technician_photo_ocr.py # 本地 OCR、自动匹配及人工确认
│   ├── review_technician_photos.py # 待核对照片 CLI
│   ├── db.py                  # MySQL 连接
│   ├── database.py            # 旧 PostgreSQL 初始化脚本，当前流程未使用
│   ├── requirements.txt       # Python 依赖
│   ├── migrations/            # MySQL 表结构迁移
│   └── config/
│       └── channels.py        # 频道、会所、主标签和搜索词配置
├── miniapp/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── VenueList.jsx
│   │   │   ├── VenueDetail.jsx
│   │   │   └── TechnicianList.jsx
│   │   ├── utils/twa.js       # 前端 API 请求
│   │   └── App.jsx            # 页面路由
│   ├── package.json
│   └── vite.config.js         # 开发环境 /api 代理
└── README.md
```

## 当前功能

- 会所列表、搜索标签、封面图片展示。
- 会所服务项目和技师工号展示。
- 每个服务项目可配置其适用技师；点击项目展开技师工号，点击工号查看已确认照片。
- “名玺水汇会所”频道公告按牌号解析当日技师，并从公告中提取三通、潮喷、SM、双仪等服务分类；技师照片按数字工号自动匹配。
- “凯迪/大浪淘沙会所”按价目表展示牌位、车模、日韩类别及已确认关联的技师工号，不依赖出勤公告。
- 技师详情会从照片 OCR 提取工号、年龄、身高、胸围、体重、籍贯和籍贯字段后的服务介绍；未识别出的资料不会补猜。
- 会所详情和技师列表 API。
- Telegram 机器人监听配置中的 10 个频道；带有对应主标签的图片会写入 `venue_photos`，并更新会所封面。
- 机器人在本地 OCR 频道照片中的纯数字工号；唯一、高置信度且属于当日出勤名单的照片会自动关联，其他照片待人工核对。
- 匹配时忽略工号的前导零（例如照片上的 `21` 可匹配出勤名单中的 `021`）；如名单中同时存在两种写法，则保留人工核对。
- 前端可点击今日工号查看已自动匹配或人工确认的照片；待核对照片不会通过公开 API 展示。
- 数据存储使用 MySQL，默认数据库名为 `venue_db`。

### 当前数据模型

- `venues`：会所资料；`technician_codes` 存放逗号分隔的技师工号。
- `venue_photos`：频道图片及消息记录。
- `technicians`：独立技师记录。
- `service_items`：会所服务项目及价格。
- `service_item_technicians`：服务项目与可服务技师工号的多对多关联。
- `technician_photos`：照片 OCR 结果、出勤日期、候选工号及审核状态；Telegram `file_id` 只由后端使用。
- `attendance_groups`：保存“名玺水汇会所”按牌号和服务分类解析出的每日技师列表。

前端“今日出勤”数量目前根据 `technician_codes` 计算；技师团队列表则读取 `technicians` 表，两者是不同数据来源。当前数据库没有会所介绍字段，因此详情 API 暂时返回空介绍。

## 本地开发（Windows）

### 准备环境变量

在 `bot/.env` 配置以下变量。不要将实际令牌或密码提交到 Git：

```env
TELEGRAM_BOT_TOKEN=替换为机器人令牌
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=应用专用数据库用户
DB_PASSWORD=数据库密码
DB_NAME=venue_db

# 可选：跨域来源，仅在前后端不同源部署时配置
CORS_ORIGINS=https://你的前端域名

# 可选：API 监听配置；本机反向代理部署通常保持默认值
API_HOST=127.0.0.1
API_PORT=8000
```

确保 MySQL 服务正在运行，并已创建数据库及所需表。机器人和 API 共享 `bot/.env` 中的 MySQL 配置。

应用技师照片表迁移（在仓库根目录执行）：

```powershell
Get-Content -Raw .\bot\migrations\002_technician_photos.sql | mysql -u root -p venue_db
Get-Content -Raw .\bot\migrations\003_technician_photo_ocr_error.sql | mysql -u root -p venue_db
Get-Content -Raw .\bot\migrations\004_service_item_technicians.sql | mysql -u root -p venue_db
Get-Content -Raw .\bot\migrations\005_service_item_technician_duty_date.sql | mysql -u root -p venue_db
Get-Content -Raw .\bot\migrations\006_attendance_groups.sql | mysql -u root -p venue_db
```

Telegram 出勤公告的固定分区会自动同步到该日期对应的服务项目：小项、中项、高项、中医按摩、修脚师分区中的工号会关联到同类别下的项目，且只在公告日期显示。公告中的分区名单视为该类别下所有项目均可服务；重复收到同日期公告时会更新当日对应关系。

若需手动调整某天的名单，先查出该会所服务项目 ID，再为项目插入可服务的工号：

```sql
SELECT id, name FROM service_items WHERE venue_id = 1 ORDER BY sort_order, id;

INSERT INTO service_item_technicians (service_item_id, duty_date, technician_code)
VALUES (13, '2026-10-23', '098'), (13, '2026-10-23', '021');
```

替换示例中的服务项目 ID、日期和工号为实际名单。网页点击项目名称即可展开当日工号，点击工号可查看已确认照片。

### 安装依赖

在仓库根目录打开 PowerShell：

```powershell
cd .\bot
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

另开一个终端安装前端依赖：

```powershell
cd .\miniapp
npm install
```

### 启动服务

分别在三个终端窗口运行以下命令，并保持终端开启。

**窗口 1：Telegram 机器人**

```powershell
cd .\bot
.\venv\Scripts\Activate.ps1
python .\main.py
```

**窗口 2：后端 API**

```powershell
cd .\bot
.\venv\Scripts\Activate.ps1
python .\api_server.py
```

API 默认由 Waitress 监听 `127.0.0.1:8000`。

**窗口 3：前端**

```powershell
cd .\miniapp
npm run dev
```

在浏览器打开 `http://localhost:5173/`。Vite 会将 `/api/*` 请求代理到 `http://127.0.0.1:8000/*`。

## API

所有请求均为 `POST /webhook`，请求和响应使用 JSON。

| `action` | 请求字段 | 返回内容 |
|---|---|---|
| `get_venues` | 无 | `{ "venues": [...] }`，包含服务项目及技师工号 |
| `get_venue_detail` | `id` | 会所详情 |
| `get_technicians` | `venue_id` | `{ "list": [...] }` |

本地调用示例：

```powershell
$body = @{ action = "get_venues" } | ConvertTo-Json
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/webhook" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## 构建前端

```powershell
cd .\miniapp
npm run build
```

构建产物位于 `miniapp/dist/`，可由 Nginx、Caddy 或其他静态文件服务器托管。

## 服务器部署要点

1. 在服务器安装 Python、Node.js 和 MySQL；创建数据库及表，并配置 `bot/.env`。为应用创建权限受限的数据库账号，不要在服务器上使用 MySQL `root` 账号运行应用。
2. 安装后端依赖：`python -m pip install -r requirements.txt`。
3. 使用进程管理器运行 `python api_server.py` 和 `python main.py`，并配置服务异常重启及日志管理。
4. 由 Nginx/Caddy 提供 HTTPS 和静态前端文件，并将 `/api/` 反向代理到 `http://127.0.0.1:8000/`。前端需要配置 SPA 路由回退到 `index.html`。
5. 默认 API 仅监听本机回环地址，适用于同机反向代理。若必须跨主机访问，才将 `API_HOST` 配为 `0.0.0.0`，并通过防火墙限制来源。
6. 前后端同域部署时通常不需要放宽 CORS；分域部署时，将精确的前端 HTTPS 来源填入 `CORS_ORIGINS`。
7. `VITE_API_BASE_URL` 是前端构建时变量；同域部署使用默认 `/api`，分域部署时在构建前设置为完整 API 前缀，例如 `https://api.example.com`。

> 当前 API 未实现登录或访问令牌校验。不要在未增加访问控制和限流的情况下，将 API 直接暴露到公网。

## 数据库和机器人注意事项

- 机器人仅处理配置频道中的图片消息；图片说明需要包含该频道对应的主标签。
- “名玺水汇会所”出勤公告使用“9牌969”等格式，页面按牌号分组显示技师，并从“三通、潮喷、SM、双仪”等公告分区提取服务分类。部署此功能前需应用 `006_attendance_groups.sql` 迁移。
- 技师照片 OCR 在运行机器人本机执行，不会将照片发送给 OCR 第三方服务。照片按出勤公告中的日期归档；若照片先于公告，公告保存后会自动尝试补匹配。
- 只有 OCR 唯一识别出高置信度工号且工号在对应出勤名单中的照片会自动公开；其他照片需人工核对。待核对照片默认不显示在小程序。
- 查看待核对项目：`cd .\bot; .\venv\Scripts\python.exe .\review_technician_photos.py list`。人工确认：`.\venv\Scripts\python.exe .\review_technician_photos.py approve <照片ID> <工号>`。只能确认该日期出勤名单中的工号。
- 首次运行 OCR 时需要联网下载识别模型；模型保存在 Python 环境中，不会上传照片。
- 请先确认机器人账号已加入需要监听的频道，并具备读取消息及发送回复的权限。
- `bot/database.py` 是旧 PostgreSQL 脚本；当前 `bot/db.py` 和 `bot/api_server.py` 使用 MySQL。
- 数据库备份、迁移和恢复应在服务器上线前另行配置。

## 配置与秘密管理

- 本地开发的 Telegram 令牌和数据库密码保存在 `bot/.env`；该文件已被 `.gitignore` 排除，不会被 Git 跟踪。请勿把真实凭据放进 README、源代码、截图或提交记录。
- 服务器部署时优先使用云厂商 Secret Manager；没有该服务时，通过服务器环境变量或权限受限的秘密文件注入，不要把本机 `.env` 上传到公开仓库。
- `bot/.env` 已包含当前项目所需的环境变量。新环境请按“本地开发”章节的模板配置自己的值。
- 旧凭据曾出现在 README 和聊天中，建议在 Telegram BotFather 轮换机器人令牌，并更改数据库密码；轮换后只更新 `bot/.env` 和服务器秘密配置，不要把新值贴到 README。

频道 ID 是机器人运行配置，不是认证凭据；具体频道及标签以 `bot/config/channels.py` 为准。当前频道配置如下：

| 频道 ID | 会所 | 主标签 |
|---|---|---|
| `-1002324026180` | 名玺水汇按摩 | `名玺水汇按摩` |
| `-1002323001411` | 名玺水汇会所 | `名玺水汇会所` |
| `-1002442225595` | 名玺水汇KTV | `名玺水汇KTV` |
| `-1002219327758` | 凯迪/大浪淘沙会所 | `凯迪/大浪淘沙会所` |
| `-1002242032486` | 大西洋/五象会所 | `大西洋/五象会所` |
| `-1002242001555` | 帝豪会所 | `帝豪会所` |
| `-1002219602447` | 西港170💲外送 | `西港170💲外送` |
| `-1002257887031` | 西港180💲外送 | `西港180💲外送` |
| `-1002433638615` | 西港160💲外送 | `西港160💲外送` |
| `-1003900469567` | 小苹果/红苹果外送 | `小苹果/红苹果外送` |
