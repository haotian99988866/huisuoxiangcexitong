# 免费上线部署指南（Oracle 永久免费服务器）

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
