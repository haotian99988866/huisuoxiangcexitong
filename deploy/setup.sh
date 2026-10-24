#!/usr/bin/env bash
# 在 Ubuntu 22.04（Oracle 免费服务器）上一键部署。
# 用法：sudo DOMAIN=xxx.duckdns.org BOT_TOKEN=机器人令牌 bash deploy/setup.sh
set -euo pipefail

: "${DOMAIN:?请设置 DOMAIN，例如 DOMAIN=xxx.duckdns.org}"
: "${BOT_TOKEN:?请设置 BOT_TOKEN（BotFather 给的机器人令牌）}"

APP_DIR=/opt/clubapp
APP_USER=clubapp
DB_NAME=venue_db
DB_USER=clubapp
ENV_FILE="$APP_DIR/bot/.env"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git curl ca-certificates gnupg debian-keyring debian-archive-keyring \
  apt-transport-https python3-venv python3-pip mysql-server libgl1 libglib2.0-0

# Node.js 20（构建前端）
if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

# Caddy（自动申请 HTTPS 证书）
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

# 代码：已在 /opt/clubapp 则直接使用，否则从当前目录复制
if [ ! -d "$APP_DIR/bot" ]; then
  mkdir -p "$APP_DIR"
  cp -r "$(cd "$(dirname "$0")/.." && pwd)/." "$APP_DIR/"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# 数据库：只允许本机访问；首次运行生成随机密码
if [ ! -f "$ENV_FILE" ]; then
  DB_PASSWORD=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  mysql -e "CREATE DATABASE IF NOT EXISTS $DB_NAME CHARACTER SET utf8mb4;
            CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
            ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASSWORD';
            GRANT ALL ON $DB_NAME.* TO '$DB_USER'@'localhost';"
  cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_NAME=$DB_NAME
API_HOST=127.0.0.1
API_PORT=8000
EOF
  chown "$APP_USER":"$APP_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# 后端依赖
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/bot/venv"
sudo -u "$APP_USER" "$APP_DIR/bot/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/bot/venv/bin/pip" install -r "$APP_DIR/bot/requirements.txt"

# 前端构建
sudo -u "$APP_USER" bash -c "cd $APP_DIR/miniapp && npm ci && npm run build"

# Caddy
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

# 常驻服务
cp "$APP_DIR/deploy/clubapp-api.service" /etc/systemd/system/
cp "$APP_DIR/deploy/clubapp-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable clubapp-api clubapp-bot

echo
echo "安装完成。下一步：导入数据库后启动服务（见 DEPLOY.md 第 5 步）："
echo "  sudo systemctl start clubapp-api clubapp-bot"
