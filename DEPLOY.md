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
