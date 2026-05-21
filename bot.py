"""
Телеграм-бот для сбора свадебных фото от гостей.
Сохраняет фото в папку photos/ и метаданные в photos_meta.json.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Загружаем переменные окружения из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Папка для хранения фото
PHOTOS_DIR = Path("photos")
META_FILE = Path("photos_meta.json")
SESSION_FILE = Path("session.json")

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ───────────────────────── вспомогательные функции ──────────────────────────

def load_meta() -> list:
    """Загружает список метаданных фото из файла."""
    if not META_FILE.exists():
        return []
    try:
        with META_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_meta(meta: list) -> None:
    """Сохраняет список метаданных фото в файл."""
    with META_FILE.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_session() -> dict:
    """Загружает текущую сессию из файла."""
    if not SESSION_FILE.exists():
        # Создаём начальную сессию при первом запуске
        session = {"current": datetime.now().strftime("%Y%m%d_%H%M%S")}
        save_session(session)
        return session
    try:
        with SESSION_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        session = {"current": datetime.now().strftime("%Y%m%d_%H%M%S")}
        save_session(session)
        return session


def save_session(session: dict) -> None:
    """Сохраняет данные сессии в файл."""
    with SESSION_FILE.open("w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def get_current_session_id() -> str:
    """Возвращает ID текущей активной сессии."""
    return load_session()["current"]


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ────────────────────────── обработчики команд ──────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start — приветственное сообщение."""
    user = update.effective_user
    name = user.full_name if user else "Гость"
    await update.message.reply_text(
        f"👰🤵 Привет, {name}!\n\n"
        "Добро пожаловать на свадьбу Тани и Никиты! 🎉\n\n"
        "Отправь сюда своё фото с праздника, и оно появится "
        "на большом экране в зале. 📸\n\n"
        "Просто прикрепи фото к сообщению и отправь!"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /status — только для администратора."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    session_id = get_current_session_id()
    meta = load_meta()

    # Считаем фото только текущей сессии
    session_photos = [p for p in meta if p.get("session_id") == session_id]

    # Преобразуем session_id в читаемый формат (20250101_180000 → 01.01.2025 18:00)
    try:
        dt = datetime.strptime(session_id, "%Y%m%d_%H%M%S")
        started = dt.strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        started = session_id

    await update.message.reply_text(
        f"📊 Статус текущей сессии:\n\n"
        f"🆔 ID сессии: {session_id}\n"
        f"🕐 Начата: {started}\n"
        f"📸 Фото в сессии: {len(session_photos)}\n"
        f"📁 Всего фото на диске: {len(meta)}"
    )


async def cmd_new_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /newsession — начать новую сессию (только для админа)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    new_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_session({"current": new_id})

    await update.message.reply_text(
        f"✅ Новая сессия начата.\n"
        f"🆔 ID: {new_id}\n\n"
        "Предыдущие фото скрыты со стены, но остаются на диске."
    )
    logger.info("Администратор начал новую сессию: %s", new_id)


async def cmd_clear_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /clearsession — удалить фото текущей сессии (только для админа)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    session_id = get_current_session_id()
    meta = load_meta()

    deleted_count = 0
    remaining = []

    for entry in meta:
        if entry.get("session_id") == session_id:
            # Удаляем файл с диска
            photo_path = PHOTOS_DIR / entry["filename"]
            try:
                if photo_path.exists():
                    photo_path.unlink()
                    deleted_count += 1
            except OSError as e:
                logger.error("Не удалось удалить файл %s: %s", photo_path, e)
        else:
            remaining.append(entry)

    save_meta(remaining)

    await update.message.reply_text(
        f"🗑 Фото текущей сессии удалены.\n"
        f"📸 Удалено файлов: {deleted_count}"
    )
    logger.info("Администратор очистил сессию %s, удалено %d фото", session_id, deleted_count)


# ──────────────────────── обработчик входящих фото ──────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает фото от гостя, сохраняет на диск и записывает метаданные."""
    user = update.effective_user
    name = user.full_name if user else "Гость"

    # Берём фото в максимальном качестве (последний элемент списка)
    photo = update.message.photo[-1]

    # Формируем уникальное имя файла: timestamp_user_id.jpg
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{user.id}.jpg"
    filepath = PHOTOS_DIR / filename

    try:
        # Скачиваем файл через Telegram API
        file = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(str(filepath))
        logger.info("Сохранено фото: %s от %s", filename, name)
    except Exception as e:
        logger.error("Ошибка при скачивании фото от %s: %s", name, e)
        await update.message.reply_text(
            "😔 Что-то пошло не так при сохранении фото. Попробуй ещё раз!"
        )
        return

    # Записываем метаданные
    meta = load_meta()
    meta.append({
        "filename": filename,
        "name": name,
        "time": datetime.now().strftime("%H:%M"),
        "session_id": get_current_session_id(),
    })
    save_meta(meta)

    await update.message.reply_text(
        f"✨ Спасибо, {name}! Твоё фото появится на экране в зале! 🎊"
    )


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает, если гость прислал не фото."""
    await update.message.reply_text(
        "📸 Пришли мне фото! Просто прикрепи снимок к сообщению и отправь."
    )


# ──────────────────────────── запуск бота ───────────────────────────────────

def main() -> None:
    """Запускает телеграм-бота."""
    if not BOT_TOKEN:
        raise ValueError("Переменная BOT_TOKEN не задана в .env файле!")

    # Создаём папку для фото, если её нет
    PHOTOS_DIR.mkdir(exist_ok=True)

    # Инициализируем сессию при первом запуске
    load_session()

    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("newsession", cmd_new_session))
    app.add_handler(CommandHandler("clearsession", cmd_clear_session))

    # Обработчик фото
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Обработчик для всего остального (текст, стикеры и т.д.)
    app.add_handler(MessageHandler(filters.ALL & ~filters.PHOTO & ~filters.COMMAND, handle_non_photo))

    logger.info("Бот запущен. Ожидаю фото от гостей...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
