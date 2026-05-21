#!/bin/bash
# Скрипт деплоя свадебной фотостены.
# Запускать из папки проекта: bash deploy.sh

set -e  # остановить при любой ошибке

echo "======================================"
echo "  Деплой свадебной фотостены 💒"
echo "======================================"

# ── 1. Обновляем код из Git ──
echo ""
echo "📥 Обновляем код..."
git pull

# ── 2. Устанавливаем зависимости Python ──
echo ""
echo "📦 Устанавливаем зависимости..."
pip3 install -r requirements.txt --quiet

# ── 3. Открываем порт 8080 через ufw ──
echo ""
echo "🔓 Открываем порт 8080..."
if command -v ufw &>/dev/null; then
  sudo ufw allow 8080/tcp
  echo "   Порт 8080 открыт."
else
  echo "   ufw не найден, пропускаем. Убедись, что порт 8080 открыт вручную."
fi

# ── 4. Создаём папку photos/ если нет ──
echo ""
echo "📁 Проверяем папку photos/..."
mkdir -p photos
echo "   Папка photos/ готова."

# ── 5. Останавливаем старые процессы если они запущены ──
echo ""
echo "🛑 Останавливаем старые процессы (если есть)..."

# Убиваем screen-сессии с нашими именами
screen -S wedding-bot -X quit 2>/dev/null && echo "   Остановлен wedding-bot." || true
screen -S wedding-server -X quit 2>/dev/null && echo "   Остановлен wedding-server." || true

# Небольшая пауза перед перезапуском
sleep 1

# ── 6. Запускаем бота и сервер через screen ──
echo ""
echo "🚀 Запускаем бота и сервер..."

# Запускаем бота в фоновом screen
screen -dmS wedding-bot bash -c "cd $(pwd) && python3 bot.py >> logs/bot.log 2>&1"
echo "   Бот запущен в screen-сессии 'wedding-bot'."

# Запускаем HTTP-сервер в фоновом screen
screen -dmS wedding-server bash -c "cd $(pwd) && python3 server.py >> logs/server.log 2>&1"
echo "   Сервер запущен в screen-сессии 'wedding-server'."

# Создаём папку для логов
mkdir -p logs

# ── 7. Выводим IP и ссылку ──
echo ""
echo "======================================"
echo "  ✅ Деплой завершён!"
echo "======================================"

# Получаем внешний IP сервера
SERVER_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
echo "🌐 Стена доступна по адресу:"
echo "   http://${SERVER_IP}:8080"
echo ""
echo "📋 Полезные команды:"
echo "   screen -r wedding-bot     — подключиться к боту"
echo "   screen -r wedding-server  — подключиться к серверу"
echo "   screen -ls                — список всех сессий"
echo "   cat logs/bot.log          — логи бота"
echo "   cat logs/server.log       — логи сервера"
echo ""
echo "🛑 Для остановки:"
echo "   screen -S wedding-bot -X quit"
echo "   screen -S wedding-server -X quit"
echo ""
