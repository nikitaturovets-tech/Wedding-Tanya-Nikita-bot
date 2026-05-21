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

from telegram import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, Update
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
QUEUE_FILE = Path("toast_queue.json")
TOAST_DONE_FILE = Path("toast_done.json")  # кто уже выступил в эту сессию
TABLE_FILE = Path("table_photo.json")       # file_id фото карты столов

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


# ─────────────────────── очередь тостов ────────────────────────────────────

def load_queue() -> list:
    """Загружает очередь тостов из файла."""
    if not QUEUE_FILE.exists():
        return []
    try:
        with QUEUE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_queue(queue: list) -> None:
    """Сохраняет очередь тостов в файл."""
    with QUEUE_FILE.open("w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def load_toast_done() -> set:
    """Загружает множество user_id тех, кто уже выступил в эту сессию."""
    if not TOAST_DONE_FILE.exists():
        return set()
    try:
        with TOAST_DONE_FILE.open("r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_toast_done(done: set) -> None:
    """Сохраняет список уже выступивших."""
    with TOAST_DONE_FILE.open("w", encoding="utf-8") as f:
        json.dump(list(done), f, ensure_ascii=False, indent=2)


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


# ──────────────────────── команды очереди тостов ────────────────────────────

async def cmd_toast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Гость встаёт в очередь на тост командой /toast."""
    user = update.effective_user
    name = user.full_name if user else "Гость"
    user_id = user.id

    queue = load_queue()
    done = load_toast_done()

    # Уже выступал в эту сессию
    if user_id in done:
        await update.message.reply_text(
            "🥂 Ты уже выступал(а) с тостом в эту сессию.\n"
            "Спасибо за поздравление! 🎉"
        )
        return

    # Уже стоит в очереди
    for entry in queue:
        if entry["user_id"] == user_id:
            pos = queue.index(entry) + 1
            await update.message.reply_text(
                f"🥂 Ты уже в очереди на тост!\n"
                f"Твоя позиция: #{pos} — ожидай, скоро дадут слово 🎤"
            )
            return

    # Добавляем в конец очереди
    queue.append({
        "user_id": user_id,
        "name": name,
        "time": datetime.now().strftime("%H:%M"),
    })
    save_queue(queue)

    pos = len(queue)
    if pos == 1:
        await update.message.reply_text(
            f"🥂 Спасибо, {name}!\n\n"
            "Ты первый(ая) в очереди — совсем скоро дадут слово 🎤"
        )
    else:
        await update.message.reply_text(
            f"🥂 Спасибо, {name}! Ты в очереди на тост.\n\n"
            f"Твоя позиция: #{pos}\n"
            f"Ожидай — скоро дадут слово 🎤"
        )
    logger.info("Гость %s встал в очередь тостов, позиция %d", name, pos)


async def cmd_cancel_toast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Гость выходит из очереди командой /canceltoast."""
    user = update.effective_user
    name = user.full_name if user else "Гость"
    user_id = user.id

    queue = load_queue()
    new_queue = [e for e in queue if e["user_id"] != user_id]

    if len(new_queue) == len(queue):
        await update.message.reply_text("Ты не стоишь в очереди на тост.")
        return

    save_queue(new_queue)
    await update.message.reply_text(
        f"✅ {name}, ты убран из очереди на тост."
    )
    logger.info("Гость %s вышел из очереди тостов", name)


async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущую очередь тостов — только для администратора."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    queue = load_queue()

    if not queue:
        await update.message.reply_text("📋 Очередь тостов пуста.")
        return

    lines = ["📋 *Очередь тостов:*\n"]
    for i, entry in enumerate(queue, start=1):
        prefix = "🎤" if i == 1 else f"{i}."
        lines.append(f"{prefix} {entry['name']} (записался в {entry['time']})")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


async def cmd_next_toast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Убирает первого из очереди и показывает следующего — только для администратора."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    queue = load_queue()

    if not queue:
        await update.message.reply_text("📋 Очередь пуста — больше никого нет.")
        return

    done_entry = queue.pop(0)
    save_queue(queue)

    # Запоминаем выступившего — в эту сессию больше не встанет
    done = load_toast_done()
    done.add(done_entry["user_id"])
    save_toast_done(done)

    text = f"✅ *{done_entry['name']}* — выступил(а)!\n\n"

    if queue:
        nxt = queue[0]
        text += f"🎤 Следующий: *{nxt['name']}*"
        if len(queue) > 1:
            text += f"\n📋 Ещё в очереди: {len(queue) - 1} чел."
    else:
        text += "🎉 Очередь завершена! Больше тостов нет."

    await update.message.reply_text(text, parse_mode="Markdown")
    logger.info("Тост отмечен: %s, осталось в очереди: %d", done_entry['name'], len(queue))


async def cmd_clear_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Полностью очищает очередь тостов и историю выступивших — только для администратора."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    count = len(load_queue())
    save_queue([])
    save_toast_done(set())  # сбрасываем и историю — все могут встать снова

    await update.message.reply_text(
        f"🗑 Очередь тостов очищена. Удалено записей: {count}\n"
        "Все гости снова могут записаться на тост."
    )
    logger.info("Администратор очистил очередь тостов (%d записей)", count)


# ─────────────────────── карта столов ──────────────────────────────────────

def load_table_photo() -> str:
    """Возвращает file_id сохранённой карты столов, или пустую строку."""
    if not TABLE_FILE.exists():
        return ""
    try:
        with TABLE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f).get("file_id", "")
    except (json.JSONDecodeError, OSError):
        return ""


def save_table_photo(file_id: str) -> None:
    """Сохраняет file_id карты столов."""
    with TABLE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"file_id": file_id}, f)


async def cmd_set_table(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Инструкция для админа как загрузить карту столов."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text(
        "🪑 Чтобы установить карту столов:\n\n"
        "Отправь фото и в подписи к нему напиши /settable\n\n"
        "После этого гости смогут получить её по команде /table."
    )


async def cmd_table(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Гость получает карту рассадки."""
    file_id = load_table_photo()
    if not file_id:
        await update.message.reply_text(
            "🪑 Карта рассадки ещё не загружена.\n"
            "Спроси организатора!"
        )
        return
    await update.message.reply_photo(
        photo=file_id,
        caption="🪑 Карта рассадки гостей"
    )


# ──────────────────────── расписание дня ────────────────────────────────────

SCHEDULE = """📅 *Программа вечера*

🕠 *17:30 – 18:30* — Сбор гостей
　　🥩 Кортадор
　　🥂 Шампанское & Апероль
　　🎵 Диджей

🥂 *18:30* — Приветственный тост и начало банкета
　　Тосты родителей

🎉 *19:00* — Активность

🍽 *19:30* — Горячее

🎤 *20:30* — Конкурс / квиз
　　Тосты от гостей

💃 *21:00* — Медленный танец

💐 *21:30* — Бросаем букет & подарки

🎂 *22:00* — Торт

🌙 *23:00 – 23:30* — Расходимся"""


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расписание свадебного вечера."""
    await update.message.reply_text(SCHEDULE, parse_mode="Markdown")


# ──────────────────────── обработчик входящих фото ──────────────────────────

# Словарь ожидающих подписи: user_id → {filename, name, time, session_id}
# Фото сохранено на диск, но НЕ добавлено в meta — ждём подпись или /skip
pending_caption: dict = {}


def _publish_photo(filename: str, name: str, time_str: str,
                   session_id: str, caption: str) -> None:
    """Добавляет фото в photos_meta.json — после этого оно появляется на стене."""
    meta = load_meta()
    meta.append({
        "filename":   filename,
        "name":       name,
        "time":       time_str,
        "session_id": session_id,
        "caption":    caption,
    })
    save_meta(meta)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Принимает фото от гостя, сохраняет на диск и ждёт подписи.
    На стену фото НЕ добавляется до тех пор, пока гость не напишет
    подпись или не отправит /skip.
    """
    user = update.effective_user
    name = user.full_name if user else "Гость"

    # Если админ отправил фото с подписью /settable — сохраняем как карту столов
    if is_admin(user.id):
        caption = (update.message.caption or "").strip()
        if caption == "/settable":
            file_id = update.message.photo[-1].file_id
            save_table_photo(file_id)
            await update.message.reply_text("✅ Карта столов сохранена! Гости могут получить её по /table.")
            logger.info("Админ обновил карту столов")
            return

    # Если гость прислал новое фото пока мы ждали подпись к предыдущему —
    # публикуем предыдущее без подписи, чтобы не потерять
    if user.id in pending_caption:
        prev = pending_caption.pop(user.id)
        _publish_photo(
            prev["filename"], prev["name"], prev["time"],
            prev["session_id"], caption=""
        )
        logger.info("Предыдущее фото %s опубликовано без подписи (гость прислал новое)", prev["filename"])

    # Берём фото в максимальном качестве
    photo = update.message.photo[-1]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{user.id}.jpg"
    filepath = PHOTOS_DIR / filename

    try:
        file = await context.bot.get_file(photo.file_id)
        await file.download_to_drive(str(filepath))
        logger.info("Сохранено фото на диск: %s от %s", filename, name)
    except Exception as e:
        logger.error("Ошибка при скачивании фото от %s: %s", name, e)
        await update.message.reply_text(
            "😔 Что-то пошло не так при сохранении фото. Попробуй ещё раз!"
        )
        return

    # Запоминаем данные фото — НЕ публикуем пока нет подписи
    pending_caption[user.id] = {
        "filename":   filename,
        "name":       name,
        "time":       datetime.now().strftime("%H:%M"),
        "session_id": get_current_session_id(),
    }

    await update.message.reply_text(
        f"✨ Фото получено, {name}!\n\n"
        "✏️ Напиши подпись к фото — она появится на экране в зале.\n"
        "Или отправь /skip чтобы пропустить."
    )


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Гость пропускает добавление подписи — фото публикуется без подписи."""
    user = update.effective_user
    name = user.full_name if user else "Гость"

    if user.id in pending_caption:
        pending = pending_caption.pop(user.id)
        _publish_photo(
            pending["filename"], pending["name"], pending["time"],
            pending["session_id"], caption=""
        )
        logger.info("Фото %s опубликовано без подписи (/skip)", pending["filename"])
        await update.message.reply_text(
            f"🎊 Готово, {name}! Твоё фото появится на экране в зале!"
        )
    else:
        await update.message.reply_text(
            "📸 Сначала пришли фото, а потом добавляй подпись."
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текст: сохраняет подпись и публикует фото, или просит прислать фото."""
    user = update.effective_user
    name = user.full_name if user else "Гость"
    text = update.message.text.strip()

    if user.id in pending_caption:
        pending = pending_caption.pop(user.id)
        caption = text[:100]  # не более 100 символов

        _publish_photo(
            pending["filename"], pending["name"], pending["time"],
            pending["session_id"], caption=caption
        )
        logger.info("Фото %s опубликовано с подписью от %s: %r", pending["filename"], name, caption)
        await update.message.reply_text(
            f"🎊 Готово, {name}! Фото с подписью появится на экране в зале!"
        )
        return

    await update.message.reply_text(
        "📸 Пришли мне фото! Просто прикрепи снимок к сообщению и отправь."
    )


# ──────────────────────────── запуск бота ───────────────────────────────────

async def setup_commands(app: Application) -> None:
    """
    Устанавливает меню команд в Telegram.
    Гостям показывается короткое меню с основными действиями.
    Администратору — расширенное с командами управления.
    """
    # Команды для всех гостей
    guest_commands = [
        BotCommand("start",       "👋 Начало — как пользоваться ботом"),
        BotCommand("schedule",    "📅 Программа вечера"),
        BotCommand("table",       "🪑 Карта рассадки гостей"),
        BotCommand("toast",       "🥂 Встать в очередь на тост"),
        BotCommand("canceltoast", "❌ Выйти из очереди на тост"),
        BotCommand("skip",        "⏩ Пропустить добавление подписи к фото"),
    ]

    # Команды для администратора (включают всё гостевое + управление)
    admin_commands = guest_commands + [
        BotCommand("settable",     "🗺 Загрузить карту столов"),
        BotCommand("queue",        "📋 Список очереди тостов"),
        BotCommand("nexttoast",    "➡️ Следующий тост"),
        BotCommand("clearqueue",   "🗑 Очистить очередь тостов"),
        BotCommand("status",       "📊 Статус текущей сессии фото"),
        BotCommand("newsession",   "🆕 Начать новую сессию фото"),
        BotCommand("clearsession", "🗑 Удалить фото сессии"),
    ]

    # Устанавливаем гостевое меню для всех личных чатов
    await app.bot.set_my_commands(
        guest_commands,
        scope=BotCommandScopeAllPrivateChats(),
    )

    # Перекрываем меню для администратора его личным расширенным набором
    if ADMIN_ID:
        await app.bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )

    logger.info("Меню команд установлено.")


def main() -> None:
    """Запускает телеграм-бота."""
    if not BOT_TOKEN:
        raise ValueError("Переменная BOT_TOKEN не задана в .env файле!")

    # Создаём папку для фото, если её нет
    PHOTOS_DIR.mkdir(exist_ok=True)

    # Инициализируем сессию при первом запуске
    load_session()

    app = Application.builder().token(BOT_TOKEN).post_init(setup_commands).build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("newsession", cmd_new_session))
    app.add_handler(CommandHandler("clearsession", cmd_clear_session))

    # Расписание
    app.add_handler(CommandHandler("schedule", cmd_schedule))

    # Карта столов
    app.add_handler(CommandHandler("table", cmd_table))
    app.add_handler(CommandHandler("settable", cmd_set_table))

    # Пропуск подписи
    app.add_handler(CommandHandler("skip", cmd_skip))

    # Очередь тостов — гостевые команды
    app.add_handler(CommandHandler("toast", cmd_toast))
    app.add_handler(CommandHandler("canceltoast", cmd_cancel_toast))

    # Очередь тостов — команды администратора
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("nexttoast", cmd_next_toast))
    app.add_handler(CommandHandler("clearqueue", cmd_clear_queue))

    # Обработчик фото
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Обработчик для всего остального (текст, стикеры и т.д.)
    # Текстовые сообщения — подписи к фото или подсказка отправить фото
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Всё остальное (стикеры, голосовые и т.д.)
    app.add_handler(MessageHandler(filters.ALL & ~filters.PHOTO & ~filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен. Ожидаю фото от гостей...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
