好！直接在项目根目录创建 `README.md`，完整记录项目介绍、目录结构、启动步骤、常见问题👇

---

## 📄 创建文件
路径：`D:\project\huisuoxiangcexitong\README.md`

复制下面全部内容保存进去：

```markdown
# 会所管理系统

前后端分离的 Telegram Mini App，用于会所信息展示与管理。

## 📁 项目结构
```
huisuoxiangcexitong/
├── bot/                          # 后端服务
│   ├── .env                     # 数据库配置
│   ├── database.py              # 数据库连接与初始化
│   ├── main.py                  # FastAPI 主服务与接口
│   └── venv/                    # Python 虚拟环境
├── miniapp/                     # 前端应用
│   ├── src/
│   │   ├── pages/
│   │   │   ├── VenueList.jsx    # 会所列表页
│   │   │   └── VenueDetail.jsx  # 会所详情页
│   │   ├── utils/
│   │   │   └── twa.js           # 请求封装
│   │   ├── App.jsx              # 路由配置
│   │   ├── main.jsx             # React 入口
│   │   └── index.css            # 全局样式
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
└── README.md                     # 项目说明文档
```

## 🗄️ 数据库配置
文件：`bot/.env`
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=telegram_venue
DB_USER=postgres
DB_PASS=Aa112211
```
> 请将 `DB_PASS` 改为你的 PostgreSQL 实际密码

### 数据库准备
```bash
# 登录 PostgreSQL
psql -U postgres

# 创建数据库
CREATE DATABASE telegram_venue;

# 退出
\q
```

## 🚀 后端启动

### 1. 进入目录
```bash
cd D:\project\huisuoxiangcexitong\bot
```

### 2. 激活虚拟环境（Windows）
```bash
venv\Scripts\activate
```

### 3. 安装依赖
```bash
pip install fastapi uvicorn psycopg2-binary python-dotenv
```

### 4. 启动服务
```bash
python main.py
```

**成功输出：**
```
✅ 数据库就绪
✅ 后端：http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### 5. 接口测试
```bash
curl -X POST http://127.0.0.1:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"get_venues"}'
```

## 🚀 前端启动

### 1. 进入目录
```bash
cd D:\project\huisuoxiangcexitong\miniapp
```

### 2. 安装依赖（首次）
```bash
npm install
```

### 3. 启动开发服务
```bash
npm run dev
```

**成功输出：**
```
  VITE v5.x.x  ready in xxx ms

  ➜  Local:   http://localhost:5173/
```

### 4. 访问应用
浏览器打开：**http://localhost:5173**

## 🔧 常用命令汇总

| 操作 | 命令 |
|---|---|
| 后端启动 | `cd bot && python main.py` |
| 前端启动 | `cd miniapp && npm run dev` |
| 前端构建 | `cd miniapp && npm run build` |
| 停止服务 | `Ctrl + C` |

## ⚠️ 常见问题

### 后端启动失败 — 数据库连接失败
- 检查 PostgreSQL 服务是否启动 → `Win+R` → `services.msc` → 确认 PostgreSQL 已运行
- 核对 `.env` 中 `DB_PASS` 密码是否正确
- 确认数据库 `telegram_venue` 已创建

### 前端一直显示「加载中...」
- 确认后端已正常启动在 8000 端口
- 检查 `twa.js` 中请求地址为 `http://127.0.0.1:8000/webhook`
- F12 查看控制台报错，确认无跨域（CORS）错误

### 端口被占用
```bash
# 查看占用进程
netstat -ano | findstr :8000

# 结束进程（PID 为上面查到的数字）
taskkill /PID 进程号 /F
```

## 📝 开发说明
- 前端代码修改自动刷新，无需重启
- 后端代码修改需按 `Ctrl+C` 停止后重新 `python main.py`
- 新增页面在 `src/pages/` 创建，并在 `App.jsx` 配置路由

## ✅ 当前功能清单
- [x] 会所列表展示
- [x] 会所详情页
- [x] 前后端联调
- [ ] 技师管理模块
- [ ] 管理员后台
- [ ] Telegram Bot 接入
```

---

保存好后，以后启动项目直接看这个文档就行！接下来想做哪个功能继续告诉我👇
机器人token：8966653726:AAE6MVjOODdSc7lilOyQz2ICaXXny34GyqU

频道1：
- 频道ID：-1002324026180
- 会所名称：名玺水汇按摩
- 主标签：名玺水汇按摩
- 搜索关键词：名玺，名玺水汇，按摩，中医，中医理疗，肩颈按摩，采耳，修脚，水汇，万达，同济，金贝2，海悦

频道2：
- 频道ID：-1002323001411
- 会所名称：名玺水汇会所
- 主标签：名玺水汇会所
- 搜索关键词：名玺，名玺水汇，会所，莞式服务，半价，水汇，万达，同济，金贝2，海悦

频道3：
- 频道ID： -1002442225595
- 会所名称：名玺水汇KTV
- 主标签：名玺水汇KTV
- 搜索关键词：名玺，名玺水汇，会所，DJ，裸玩，水汇，万达，同济，金贝2，海悦

频道4：
- 频道ID： -1002219327758
- 会所名称：凯迪/大浪淘沙会所
- 主标签：凯迪/大浪淘沙会所
- 搜索关键词：凯迪，大浪，水汇，会所，莞式服务，海豚，蓝色海湾，南海

频道5：
- 频道ID： -1002242032486
- 会所名称：大西洋/五象会所
- 主标签：大西洋/五象会所
- 搜索关键词：五象，大西洋，会所，莞式服务，双狮


频道6：
- 频道ID： -1002242001555
- 会所名称：帝豪会所
- 主标签：帝豪会所
- 搜索关键词：帝豪，会所，莞式服务，红灯笼

频道7：
- 频道ID：-1002219602447
- 会所名称：西港170💲外送
- 主标签：西港170💲外送
- 搜索关键词：170，越南，外送，酒店，90分钟，包夜

频道8：
- 频道ID：-1002257887031
- 会所名称：西港180💲外送
- 主标签：西港180💲外送
- 搜索关键词：180，越南，外送，酒店，90分钟，包夜

频道9：
- 频道ID：-1002433638615
- 会所名称：西港160💲外送
- 主标签：西港160💲外送
- 搜索关键词：160，越南，外送，酒店，90分钟，包夜

频道10：
- 频道ID：-1003900469567
- 会所名称：小苹果/红苹果外送
- 主标签：小苹果/红苹果外送
- 搜索关键词：小苹果，红苹果，苹果，越南，外送，酒店，90分钟，包夜

 数据库信息
主机：127.0.0.1
端口：3306
用户名：root
密码：Aa112211
库名：venue_db


下面是精简版，只保留启动步骤👇

---

# 启动说明

打开 **3 个终端窗口**，分别执行：

### 窗口 1 — 机器人
```bash
cd bot
python main.py
```
提示：`🤖 机器人启动 — 监听会所频道模式`

### 窗口 2 — 后端接口
```bash
cd bot
python api_server.py
```
提示：`🚀 后端 API 启动：http://127.0.0.1:8000`

### 窗口 3 — 前端页面
```bash
cd miniapp
npm run dev
```
访问：**http://localhost:5173/**

> ⚠️ 三个窗口保持打开，全部运行才正常工作。