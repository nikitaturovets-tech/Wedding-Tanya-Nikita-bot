#!/bin/bash
# Скрипт деплоя свадебной фотостены (Debian 13).
# Запускать из папки проекта: bash deploy.sh

set -e

echo "======================================"
echo "  Деплой свадебной фотостены 💒"
echo "======================================"

# ── 1. Обновляем код из Git ──
echo ""
echo "📥 Обновляем код..."
git pull

# ── 2. Создаём / обновляем виртуальное окружение Python ──
echo ""
echo "🐍 Настраиваем Python venv..."
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "   venv создан."
fi
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "   Зависимости установлены."

# ── 3. Открываем порт 8080 через nftables ──
echo ""
echo "🔓 Открываем порт 8080..."
if command -v nft &>/dev/null; then
  # Добавляем правило только если его ещё нет
  if ! nft list ruleset | grep -q "8080"; then
    nft add rule ip filter INPUT tcp dport 8080 accept 2>/dev/null || \
    nft add rule inet filter input tcp dport 8080 accept 2>/dev/null || \
    echo "   Не удалось добавить правило nftables — проверь вручную."
    echo "   Порт 8080 открыт через nftables."
  else
    echo "   Порт 8080 уже открыт."
  fi
elif command -v ufw &>/dev/null; then
  sudo ufw allow 8080/tcp
  echo "   Порт 8080 открыт через ufw."
elif command -v iptables &>/dev/null; then
  iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
  echo "   Порт 8080 открыт через iptables."
else
  echo "   ⚠️  Файрвол не найден — убедись, что порт 8080 открыт вручную."
fi

# ── 4. Создаём нужные папки ──
echo ""
echo "📁 Создаём папки..."
mkdir -p photos logs
echo "   Папки photos/ и logs/ готовы."

# ── 5. Останавливаем старые процессы ──
echo ""
echo "🛑 Останавливаем старые процессы (если есть)..."
screen -S wedding-bot -X quit 2>/dev/null && echo "   Остановлен wedding-bot." || true
screen -S wedding-server -X quit 2>/dev/null && echo "   Остановлен wedding-server." || true
sleep 1

# ── 6. Запускаем бота и сервер через screen ──
echo ""
echo "🚀 Запускаем бота и сервер..."

VENV_PYTHON="$(pwd)/.venv/bin/python3"

screen -dmS wedding-bot bash -c "cd $(pwd) && ${VENV_PYTHON} bot.py >> logs/bot.log 2>&1"
echo "   Бот запущен в screen-сессии 'wedding-bot'."

screen -dmS wedding-server bash -c "cd $(pwd) && ${VENV_PYTHON} server.py >> logs/server.log 2>&1"
echo "   Сервер запущен в screen-сессии 'wedding-server'."

# ── 7. Выводим IP и ссылку ──
echo ""
echo "======================================"
echo "  ✅ Деплой завершён!"
echo "======================================"

SERVER_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
echo "🌐 Стена доступна по адресу:"
echo "   http://${SERVER_IP}:8080"
echo ""
echo "📋 Полезные команды:"
echo "   screen -r wedding-bot     — подключиться к боту"
echo "   screen -r wedding-server  — подключиться к серверу"
echo "   screen -ls                — список всех сессий"
echo "   tail -f logs/bot.log      — логи бота в реальном времени"
echo "   tail -f logs/server.log   — логи сервера в реальном времени"
echo ""
echo "🛑 Для остановки:"
echo "   screen -S wedding-bot -X quit"
echo "   screen -S wedding-server -X quit"
echo ""
