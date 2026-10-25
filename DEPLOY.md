# 部署指南

## 当前 Windows 本机部署（固定 Cloudflare 隧道）

- 公网地址：`https://app.haotianzhushou.com/`。
- 命名隧道：`huisuo`，转发到 `http://127.0.0.1:4173`；前端通过 `/api` 代理本机 8000 端口。
- 隧道配置及凭据保存在当前用户的 `.cloudflared` 目录，不要提交凭据。
- 手动启动：在项目根目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start-local.ps1`。
- 脚本启动 API、机器人、已构建的前端预览和固定隧道；跳过已运行的实例，并检查本地及公网页面、数据接口。前端更新后需先在 `miniapp` 目录执行 `npm run build`。
- 当前电脑的用户启动目录已配置 `huisuo-services.cmd`，登录 Windows 后调用上述脚本。这不是系统服务，未登录时不会启动，也不负责进程崩溃后的自动重启。
- 启动和服务日志位于 `%LOCALAPPDATA%\Huisuo\logs`。MySQL84 已配置为自动启动的 Windows 服务。
- BotFather 的 Main Mini App 和机器人菜单网址应保持为上述固定地址。电脑关机、休眠或断网时，小程序仍不可用。

## 小程序导航扩展

顶部固定显示会所、外送、观影及“全部”入口；全部功能面板按内容、便民服务、个人与帮助分组，仅显示已接入的页面。
模块名称、分组、常用入口及直达参数统一登记在 `miniapp/src/utils/modules.js`；新增页面仍需在 `App.jsx` 注册路由。
外送直达地址为 `/?module=delivery`，会所入口为 `/?module=venue`；原首页、观影和充值链接保持兼容。
浏览筛选与滚动位置仅在当前页面会话内保留，不缓存余额或身份数据；面板支持关闭按钮、遮罩、Escape 和 Telegram 返回键。
个人中心位于 `/personal`，支持 `startapp=personal` 直达；从全部功能的个人与帮助分组进入，可查看账户余额、最近 30 条充值申请及客服规则。余额和记录复用后端签名验证的账户接口，普通浏览器不展示私人账户数据。
技师预约消息包含可用的资料描述和频道照片原帖链接。Telegram 客户端可能不保留链接中的预填文字，详情页提供复制预约信息按钮；剪贴板不可用时显示只读文本供手动复制。KTV 页底部保留统一联系咨询入口。
联系链接先调用 Telegram 的 `openTelegramLink`，1.5 秒后页面仍可见时再使用 `tg://resolve` 兜底；直接使用原生协议的方案在用户真机上无法打开，已撤回。会所及外送预约消息优先放来源链接，缺少原帖时使用现有媒体接口的公开地址；描述按 Telegram 4096 字符上限截取。客人仍需手动点击发送，未自动通知管理员；客户端是否实际预填须在真机确认。

观影区全部影片与集数的播放链接统一附加 `t=1`，请求 Telegram 从第 1 秒播放；原有其他链接参数保留。《定风波》第 1 集已通过用户真机确认可直接进入播放，其他客户端行为仍由 Telegram 决定。使用 Telegram 原生播放器，退出播放后仍会返回频道，非小程序内播放。

## 图片加载

技师照片、外送图片和观影封面在首次请求后缓存到服务端本地；Windows 默认目录为 `%LOCALAPPDATA%\Huisuo\image-cache`（未设置时使用系统临时目录）。缓存以 Telegram 文件 ID 的哈希隔离，重启 API 后仍可复用，写入时清理超过 7 天或总量超过 256 MB 的旧缓存。同一图片的并发首次请求合并下载。
会所列表头像和外送列表、缩略图使用 `?thumb=1` 的最长边 480px WebP，放大及资料图片链接继续使用原图。观影使用 Telegram 已有封面并复用本地缓存。浏览器图片缓存仍为 private、5 分钟，支持 ETag/304；不对照片展示校验做旁路，也不改变视频流和 Range 请求。未缓存图片首次请求仍需要 Telegram 回源，不能保证所有图片首次打开即秒出。

## KTV 视频启动加速

KTV 包房列表和播放器交互保持不变。API 启动后通过单独的后台线程预取现有 KTV 视频，每轮完成后等待 60 秒再检查新文件，不占用 API 请求线程下载缓存。
视频缓存保存在 `%LOCALAPPDATA%\Huisuo\ktv-video-cache`（未设置时使用系统临时目录），以 Telegram 文件 ID 的哈希隔离，重启后可复用；总量上限 512 MB，单文件上限 32 MB。完整下载并校验声明大小及 MP4/WebM 容器标识后才原子发布，空间不足时淘汰旧文件，不能缓存的文件明确记录错误并保留原 Telegram 转发路径。
视频接口仍先查询数据库确认包房视频存在；命中本地缓存后支持 Range/206、拖动进度、HEAD 与 ETag/304，响应头 `X-Media-Cache` 为 `HIT`。缓存未就绪时为 `MISS`，继续使用原流式转发，不阻塞等待整段下载；转发结束或客户端断开时关闭上游连接。新增视频首次缓存完成前仍可能等待 Telegram 回源，客人实际播放速度仍受服务器上传和客人网络影响。

## 展示服务租赁基础（暂未开通真实租户）

第一版共用 `haotianzhaopian_bot`，提供租户品牌名、联系名、Telegram 联系用户名和会所 ID 白名单；租户仅有会所、观影与联系入口，不含外送、个人中心、钱包或充值。不做租户自助后台、自动收月费或扣款；平台运营者人工收费、核实授权后开通和续期。主入口原有功能保持不变。

### 本机管理员管理

在 `bot` 目录运行（Windows 使用 `.\venv\Scripts\python.exe`）：

```powershell
# 仅创建表，不开通租户；API 启动时也会初始化表
.\venv\Scripts\python.exe tenants.py init
.\venv\Scripts\python.exe tenants.py list

# 示例命令，不会自动执行。先创建草稿，不可对外访问
.\venv\Scripts\python.exe tenants.py create partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status draft

# 人工核款后，用完整配置更新并启用；到期时间一律为 UTC，不含时区
# 下例日期仅示范格式，实际填写未来日期
.\venv\Scripts\python.exe tenants.py update partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status active --expires "2030-01-01T00:00:00"

# 暂停时同样提供完整配置，省略到期时间即清空到期时间
.\venv\Scripts\python.exe tenants.py update partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status suspended
```

`create` 不覆盖已有租户；`update` 不会隐式创建租户。续期用 `update` 设置新的未来到期时间并保持 `active`。联系人不带 `@`，可选择当前启用的会所 ID 1–6、外送 ID 7–10。外送默认关闭：只有明确列在 `--venues` 或管理台勾选保存的外送项目才开放，现有租户权限不会自动扩展。无需修改表结构，仍以 `venue_ids` 记录各项目授权并纳入版本校验与审计。第一版不支持自定义头像或租户公告，公告显示品牌与信息服务说明。

### 专属入口与边界

- Telegram 入口：`https://t.me/haotianzhaopian_bot?startapp=tenant_partner-one`。
- 网页入口：`https://app.haotianzhushou.com/?tenant=partner-one`；直接进入观影为 `/movies?tenant=partner-one`。
- 导航、接口及媒体链接保留租户标识；预约、复制提示和 KTV 预订使用该租户联系人。没有有效租户配置时阻止访问，不退回昊天主入口。配置不返回账务、管理员或客户数据。
- 管理台表单分为会所权限、外送权限两个分组，外送按项目逐项勾选。未授权外送时隐藏导航并阻止直达；授权后仅返回选中项目，外送图片/视频在读取缓存或回源前按所属项目校验。仅外送租户首页进入外送模块。原有成年过滤、白晚班展示和钱包禁用规则保持不变。
- API 每个带租户标识的请求检查启用状态、UTC 到期时间及会所白名单；媒体命中本地缓存也先检查范围。租户响应禁止浏览器/CDN 缓存，避免过期配置或其他租户配置复用，但服务器本地公共媒体缓存仍共享。打开页面每 60 秒复核租户状态；故障、暂停或到期显示不可用提示。
- 这是**公开内容的品牌化展示订阅**，专属链接不是登录凭证或内容付费墙。主入口、公共频道和未携带租户标识的原公开 API 仍存在；用户可自行离开租户入口访问主平台。不能宣称租户白名单可阻止用户通过公共来源查看其他会所，也不能把去掉租户标识理解为取得管理员权限。租户入口不提供任何私人账户或钱包数据；主平台钱包仍使用原 Telegram 签名身份校验。
- 共用机器人时机器人名称和 Telegram 框架品牌不会独立；影视、照片和个人资料商业展示须有相应授权。正式收费扩展前应另行落实内容授权、托管稳定性、备份与服务条款。新表 `display_tenants` 应纳入数据库备份。

## 手机运营管理台（第一版）

联系人跳转默认使用 Telegram 官方 `openTelegramLink`。当前按真机对比测试需求，iPhone 会所页通用联系区域提供两个独立按钮：「直接联系」仅调用内部跳转，「通过网页联系」使用官方 `openLink(url, { try_browser: 'safari' })`。iPhone 当前客服联系链接不自动延时中转，也不叠加协议兜底，避免两种测试互相干扰。网页方式可能先经过 Safari 再进入聊天，旧客户端是否采用指定浏览器由 Telegram 决定。预约 `text` 参数原样保留，但自动带入聊天与否需真机确认；独立网页按钮只打开聊天、不附加预约资料。安卓、电脑及观影频道跳转保持原样。浏览器模拟只能验证调用路径，不能证明 iPhone 原生聊天已打开。

平台主入口的「个人中心」在账户通过 Telegram 签名验证且 ID 位于 `ADMIN_USER_IDS` 时显示「运营管理台」。也可由管理员直接打开 `https://t.me/haotianzhaopian_bot?startapp=admin`，页面路由为 `/admin`；普通浏览器没有 Telegram 签名身份会明确拒绝，不能仅凭网址或客户端自报 ID 获取数据。现有机器人 `/admin`、`/wallet` 和本机租户命令继续保留。

- 范围：租户总数、启用数、7 天内到期数、已到期数、草稿/暂停数、待核充值数量，以及租户创建、编辑、续期、暂停、专属入口复制。第一版不提供充值审核、广播编辑、服务器进程探测或租户自助后台。待核充值仅显示数量，不执行资金操作。
- 到期时间在表单按操作手机的本地时区输入、展示，提交时转成明确的 UTC `...Z` 时间，数据库仍使用 UTC。启用必须设置未来到期时间；新建默认草稿。不会自动按月扣费或自动加一个月，人工收款后设置新的到期时间并选择启用。
- 所有保存均先展示品牌、联系人、会所、状态与到期时间确认；入口标识创建后不能在网页改名。保存不允许把不存在的租户隐式创建或覆盖已有标识。遇到网络异常请先刷新核对状态与记录，前端不会自动重试写入。
- 管理 API 每次校验 Telegram 签名、授权时间和服务端管理员白名单；即使管理员使用 `tenant_...` 签名会话或带租户请求标识也拒绝管理操作。管理员响应 `private, no-store`，无独立管理密码、无管理员 ID 客户端信任。
- 更新使用配置版本摘要和数据库行锁防止覆盖并发修改，冲突返回 409 并提示刷新。`tenant_admin_audit` 与租户配置在同一事务中写入，记录操作者、来源、前后配置及时间，审计失败则整体回滚。本机 CLI 同步记录来源 `cli`；此前已存在的租户不补造历史记录。表应纳入数据库备份，不随 48 小时业务数据清理删除。
- 页面显示最近 30 条操作元信息，完整前后配置保存在数据库。租户列表上限 200，超过明确提示升级分页，不静默隐藏。概览按刷新时的数据统计，暂停和到期仍由原租户访问检查执行。

## 充值模块当前状态

小程序 `/recharge` 和机器人主菜单已增加充值中心入口，支持 `startapp=recharge` 直达。
Premium 和话费均未开放商品下单，不会自动交付。余额充值地址须管理员自行配置。
余额模块支持 Telegram 签名身份校验、TRC20 充值申请、管理员人工核款入账及充值记录。未配置有效收款地址时禁止提交申请。
管理员私聊机器人发送 `/wallet` 查看待审核申请及命令；`/wallet address 地址` 配置收款地址；
`/wallet approve 申请编号 实际到账金额` 生成二次确认按钮，独立核对链上转账后点击才入账；
`/wallet reject 申请编号` 拒绝申请。用户提交 TXID 后不会自动入账，也不会自动通知管理员，管理员需主动查看 `/wallet`。
同一 TXID 只能提交一次（被拒绝后也不能重复提交，误报请联系管理员）。入账使用数据库事务、行锁和唯一流水防止重复记账。
1 USDT 实际到账计 1 U；余额仅供后续话费消费，不用于 Premium。无自助提现、无自动链上付款、无通用扣款功能，话费扣款和失败退款待商品规则确认后实现。数据库备份须包含所有 `wallet_` 表。
后续开放话费前需要确认运营商、面额、售价、收款币种及网络、处理时限和退款规则，再实现订单、凭证及管理员核款和履约流程。
Premium 需要接入实际可用的自动交付渠道，并确认 Telegram Stars 支付与退款方案，不复用话费的虚拟货币人工收款流程。

## 可选：Oracle 永久免费服务器部署

结构：一台服务器同时运行 MySQL、API、Telegram 机器人和前端，Caddy 提供免费 HTTPS。

## 1. 创建 Oracle 免费服务器

1. 注册 <https://www.oracle.com/cloud/free/>（需要银行卡验证，不扣费）。
2. 控制台 → Compute → Instances → Create instance：
   - Image：**Ubuntu 22.04**
   - Shape：**Ampere（ARM）VM.Standard.A1.Flex**，选 2 OCPU / 12GB 内存（标有 Always Free）
   - 下载 SSH 私钥并保存好
3. 在实例所属子网的 Security List 里添加入站规则：TCP **80** 和 **443**（来源 0.0.0.0/0）。
4. 记下实例的公网 IP。

## 2. 申请免费域名

1. 打开 <https://www.duckdns.org>，用 GitHub 账号登录。
2. 创建一个子域名（如 `myclub`），IP 填服务器公网 IP。得到 `myclub.duckdns.org`。

## 3. 登录服务器并拉取代码

```bash
ssh -i 私钥文件 ubuntu@服务器IP
sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
sudo apt-get install -y iptables-persistent   # 提示保存规则时选是
sudo git clone https://github.com/haotian99988866/huisuoxiangcexitong.git /opt/clubapp
```

> Oracle 的 Ubuntu 镜像自带防火墙规则，第二行用于放行 80/443，两处（安全列表和系统防火墙）都要开。

## 4. 一键安装

```bash
cd /opt/clubapp
sudo DOMAIN=myclub.duckdns.org BOT_TOKEN='机器人令牌' bash deploy/setup.sh
```

脚本会安装 MySQL、Node、Caddy，创建数据库和随机密码（写入 `/opt/clubapp/bot/.env`，权限 600），安装依赖、构建前端并注册两个常驻服务。

## 5. 导入数据并启动

在**自己电脑**上导出数据库（密码会提示输入），再上传：

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe' -u root -p --single-transaction --default-character-set=utf8mb4 --no-tablespaces venue_db -r venue_db.sql
scp -i 私钥文件 venue_db.sql ubuntu@服务器IP:/tmp/
```

在服务器上导入并启动：

```bash
sudo mysql venue_db < /tmp/venue_db.sql
sudo rm /tmp/venue_db.sql
sudo systemctl start clubapp-api clubapp-bot
sudo systemctl status clubapp-api clubapp-bot --no-pager
```

## 6. 检查与切换

- 浏览器打开 `https://myclub.duckdns.org`，应能看到页面。
- 验证接口：`curl -s -X POST https://myclub.duckdns.org/api/webhook -H 'Content-Type: application/json' -d '{"action":"get_venues"}' | head -c 200`
- **关闭自己电脑上的机器人**（同一个机器人令牌不能两处同时运行）。
- 在 BotFather 里：`/mybots` → 选机器人 → Bot Settings → Menu Button（或 Configure Mini App）→ 网址改为 `https://myclub.duckdns.org`。

## 日常运维

```bash
sudo journalctl -u clubapp-bot -f      # 查看机器人日志
sudo journalctl -u clubapp-api -f      # 查看 API 日志
cd /opt/clubapp && sudo -u clubapp git pull
sudo -u clubapp bash -c 'cd miniapp && npm ci && npm run build'
sudo systemctl restart clubapp-api clubapp-bot
```

每日备份数据库（可放入 crontab）：

```bash
sudo mysqldump --single-transaction venue_db | gzip > ~/venue_db_$(date +%F).sql.gz
```

## 注意

- 机器人只能收到启动之后的频道消息，迁移期间尽量缩短两边都不运行的时间。
- `.env` 里的令牌和密码不要提交到 Git。
- Oracle 免费实例长期闲置可能被回收，保持服务运行并定期登录即可。
