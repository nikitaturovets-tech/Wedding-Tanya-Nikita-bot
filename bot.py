"""
Телеграм-бот для свадьбы: фото гостей + интерактивный квиз.
Фото сохраняются в photos/, квиз управляется администратором.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
TOAST_DONE_FILE = Path("toast_done.json")
QUIZ_JSON = Path("quiz.json")
QUIZ_STATE_FILE = Path("quiz_state.json")
WALL_MODE_FILE = Path("wall_mode.json")
KNOWN_USERS_FILE = Path("known_users.json")

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


# ──────────────────────────── КВИЗ ──────────────────────────────────────────

# Состояние квиза — хранится в памяти
quiz_state: dict = {
    "active": False,
    "show_results": False,
    "current_question": 0,
    "participants": {},       # str(user_id) → {name, answers:[bool], score:int}
    "answered_current": [],   # str(user_id) кто уже ответил на текущий вопрос
}

# Все пользователи кто когда-либо писал боту: user_id → chat_id
known_users: dict = {}


def load_known_users() -> None:
    """Загружает known_users и participants из файла при старте бота."""
    if not KNOWN_USERS_FILE.exists():
        return
    try:
        data = json.loads(KNOWN_USERS_FILE.read_text(encoding="utf-8"))
        for uid_str, entry in data.items():
            uid = int(uid_str)
            known_users[uid] = entry["chat_id"]
            if uid_str not in quiz_state["participants"]:
                quiz_state["participants"][uid_str] = {
                    "name": entry["name"],
                    "answers": [],
                    "score": 0,
                }
        logger.info("Загружено %d пользователей из known_users.json", len(known_users))
    except Exception as e:
        logger.error("Ошибка загрузки known_users.json: %s", e)


def save_known_users() -> None:
    """Сохраняет known_users на диск."""
    data = {
        str(uid): {"chat_id": chat_id, "name": quiz_state["participants"].get(str(uid), {}).get("name", "")}
        for uid, chat_id in known_users.items()
    }
    KNOWN_USERS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def register_user(user_id: int, chat_id: int, name: str) -> None:
    """Регистрирует пользователя в known_users и participants квиза."""
    is_new = user_id not in known_users
    known_users[user_id] = chat_id
    uid = str(user_id)
    if uid not in quiz_state["participants"]:
        quiz_state["participants"][uid] = {
            "name": name,
            "answers": [],
            "score": 0,
        }
    # Сохраняем на диск только при появлении нового пользователя
    if is_new:
        save_known_users()


def load_quiz_questions() -> list:
    if not QUIZ_JSON.exists():
        return []
    try:
        return json.loads(QUIZ_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Ошибка чтения quiz.json: %s", e)
        return []


def save_quiz_state(questions: list) -> None:
    """Сохраняет публичное состояние квиза для веб-стены."""
    q_idx = quiz_state["current_question"]
    current_q = questions[q_idx] if q_idx < len(questions) else None
    participants = quiz_state["participants"]

    leaderboard = sorted(
        [{"name": v["name"], "score": v["score"], "total": q_idx}
         for v in participants.values()],
        key=lambda x: x["score"],
        reverse=True,
    )[:10]

    out = {
        "active": quiz_state["active"],
        "show_results": quiz_state["show_results"],
        "current_question": q_idx + 1,
        "total_questions": len(questions),
        "question_text": current_q["question"] if current_q else "",
        "options": current_q["options"] if current_q else [],
        "answered_count": len(quiz_state["answered_current"]),
        "total_participants": len(participants),
        "leaderboard": leaderboard,
    }
    QUIZ_STATE_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def init_quiz_state_file() -> None:
    if not QUIZ_STATE_FILE.exists():
        save_quiz_state(load_quiz_questions())


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
    register_user(user.id, update.effective_chat.id, user.first_name)
    await update.message.reply_text(
        f"👰🤵 Привет, {name}! Рады видеть тебя на свадьбе Тани и Никиты! 🎉\n\n"
        "Этот бот поможет тебе участвовать в празднике:\n\n"
        "📸 Хочешь поделиться фото?\n"
        "Просто отправь фотографию сюда — она тут же появится на большом экране в зале!\n\n"
        "🎯 Квиз про жениха и невесту!\n"
        "В определённый момент вечера здесь начнётся викторина — "
        "ответы приходят прямо в бот, результаты видны на экране!\n\n"
        "🥂 Хочешь сказать тост?\n"
        "Нажми «Встать в очередь» — и тебе дадут слово в нужный момент\n\n"
        "📶 Wi-Fi:\n"
        "Сеть: NESKUCHNIY\n"
        "Пароль: 87878787"
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

        # Уведомляем следующего гостя в личку
        try:
            await context.bot.send_message(
                chat_id=nxt["user_id"],
                text="🎤 Ты следующий говоришь тост!\n\nЖди сигнала от жениха 🥂"
            )
            text += "\n✉️ Гость уведомлён"
        except Exception as e:
            logger.warning("Не удалось отправить уведомление гостю %s: %s", nxt["name"], e)
            text += "\n⚠️ Не удалось отправить уведомление гостю"
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



# ──────────────────────── обработчик входящих фото ──────────────────────────

def _publish_photo(filename: str, name: str, time_str: str, session_id: str) -> None:
    """Добавляет фото в photos_meta.json — после этого оно появляется на стене."""
    meta = load_meta()
    meta.append({
        "filename":   filename,
        "name":       name,
        "time":       time_str,
        "session_id": session_id,
        "caption":    "",
    })
    save_meta(meta)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принимает фото от гостя, сохраняет на диск и сразу публикует на стене."""
    user = update.effective_user
    name = user.full_name if user else "Гость"
    register_user(user.id, update.effective_chat.id, user.first_name)

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

    _publish_photo(filename, name, datetime.now().strftime("%H:%M"), get_current_session_id())

    await update.message.reply_text(
        f"🎊 Готово, {name}! Твоё фото появится на экране в зале!"
    )



async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает входящий текст — просит прислать фото."""
    user = update.effective_user
    register_user(user.id, update.effective_chat.id, user.first_name)
    await update.message.reply_text(
        "📸 Пришли мне фото! Просто прикрепи снимок к сообщению и отправь."
    )


# ──────────────────────────── РЕЖИМ СТЕНЫ ───────────────────────────────────

def set_wall_mode(mode: str) -> None:
    """Сохраняет текущий режим стены: 'slideshow' или 'wall'."""
    WALL_MODE_FILE.write_text(
        json.dumps({"mode": mode}, ensure_ascii=False),
        encoding="utf-8",
    )


async def cmd_slideshow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключить стену в режим слайдшоу (предзагруженные фото)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    set_wall_mode("slideshow")
    await update.message.reply_text(
        "🖼 Стена переключена в режим *Слайдшоу*.\n"
        "Показываются фото из папки `photos_preset/`.",
        parse_mode="Markdown",
    )
    logger.info("Режим стены: slideshow")


async def cmd_wallmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключить стену в режим фото от гостей."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    set_wall_mode("wall")
    await update.message.reply_text(
        "📸 Стена переключена в режим *Фото гостей*.",
        parse_mode="Markdown",
    )
    logger.info("Режим стены: wall")


async def cmd_presentation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключить стену в режим презентации — ручное переключение кликером."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    set_wall_mode("presentation")
    await update.message.reply_text(
        "🎞 Стена переключена в режим *Презентация*.\n"
        "Управление кликером: → / PageDown — вперёд, ← / PageUp — назад.",
        parse_mode="Markdown",
    )
    logger.info("Режим стены: presentation")


async def cmd_starsky(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключить стену в режим Звёздное небо — полноэкранный фон."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    set_wall_mode("starsky")
    await update.message.reply_text(
        "🌌 Стена переключена в режим *Звёздное небо*.",
        parse_mode="Markdown",
    )
    logger.info("Режим стены: starsky")


# ──────────────────────────── КОМАНДЫ КВИЗА ─────────────────────────────────

async def _broadcast_question(context, questions: list, q_idx: int) -> int:
    """Разослать вопрос всем известным пользователям. Вернуть кол-во доставок."""
    q = questions[q_idx]
    keyboard = [
        [InlineKeyboardButton(f"{i+1}. {opt}", callback_data=f"quiz:{q_idx}:{i}")]
        for i, opt in enumerate(q["options"])
    ]
    markup = InlineKeyboardMarkup(keyboard)
    text = f"❓ *Вопрос {q_idx + 1} из {len(questions)}*\n\n{q['question']}"
    sent = 0
    for chat_id in known_users.values():
        try:
            await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
            sent += 1
        except Exception as e:
            logger.warning("Не удалось отправить вопрос в чат %s: %s", chat_id, e)
    return sent


async def _broadcast_text(context, text: str) -> None:
    """Разослать текстовое сообщение всем известным пользователям."""
    for chat_id in known_users.values():
        try:
            await context.bot.send_message(chat_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Не удалось отправить сообщение в чат %s: %s", chat_id, e)


async def cmd_startquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    questions = load_quiz_questions()
    if not questions:
        await update.message.reply_text(
            "❌ Файл quiz.json не найден или пустой. Создайте файл с вопросами."
        )
        return

    quiz_state["active"] = True
    quiz_state["show_results"] = False
    quiz_state["current_question"] = 0
    quiz_state["answered_current"] = []
    for uid in quiz_state["participants"]:
        quiz_state["participants"][uid]["answers"] = []
        quiz_state["participants"][uid]["score"] = 0

    save_quiz_state(questions)
    n = await _broadcast_question(context, questions, 0)
    await update.message.reply_text(
        f"🎯 Квиз начат! Вопрос 1 разослан {n} гостям.\n"
        f"Всего вопросов: {len(questions)}\n\n"
        "Когда гости ответят — /nextquestion для следующего вопроса."
    )
    logger.info("Квиз начат, вопрос 1 разослан %d гостям", n)


async def cmd_nextquestion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    if not quiz_state["active"]:
        await update.message.reply_text("❌ Квиз не активен. Запустите /startquiz")
        return

    questions = load_quiz_questions()
    q_idx = quiz_state["current_question"]

    # Показываем правильный ответ на текущий вопрос
    prev_q = questions[q_idx]
    correct_text = prev_q["options"][prev_q["correct"]]
    answered = len(quiz_state["answered_current"])
    total = len(quiz_state["participants"])
    await _broadcast_text(
        context,
        f"✅ Правильный ответ на вопрос {q_idx + 1}:\n*{correct_text}*\n\n"
        f"Ответили: {answered} из {total}",
    )

    next_idx = q_idx + 1
    if next_idx >= len(questions):
        await update.message.reply_text(
            f"🏁 Все {len(questions)} вопросов заданы!\n"
            "Напишите /results чтобы показать финальный лидерборд."
        )
        return

    quiz_state["current_question"] = next_idx
    quiz_state["answered_current"] = []
    save_quiz_state(questions)

    n = await _broadcast_question(context, questions, next_idx)
    await update.message.reply_text(
        f"➡️ Вопрос {next_idx + 1} из {len(questions)} разослан {n} гостям."
    )


async def cmd_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    questions = load_quiz_questions()
    total_q = len(questions)
    participants = quiz_state["participants"]

    sorted_p = sorted(participants.items(), key=lambda x: x[1]["score"], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *ИТОГИ КВИЗА*\n"]
    for i, (uid, data) in enumerate(sorted_p):
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} {data['name']} — {data['score']} из {total_q}")
    result_text = "\n".join(lines) + "\n\nСпасибо всем! 🎉"

    # Разослать всем участникам итог + личный результат
    for uid, data in participants.items():
        chat_id = known_users.get(int(uid))
        if chat_id:
            personal = f"\n\nТвой результат: *{data['score']} из {total_q}* 🎯"
            try:
                await context.bot.send_message(
                    chat_id, result_text + personal, parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning("Не удалось отправить результаты в чат %s: %s", chat_id, e)

    quiz_state["active"] = False
    quiz_state["show_results"] = True

    # Финальный лидерборд для стены — все участники
    q_idx = quiz_state["current_question"]
    leaderboard_wall = [
        {"name": v["name"], "score": v["score"], "total": total_q}
        for _, v in sorted_p
    ]
    QUIZ_STATE_FILE.write_text(
        json.dumps({
            "active": False,
            "show_results": True,
            "current_question": q_idx + 1,
            "total_questions": total_q,
            "question_text": "",
            "options": [],
            "answered_count": 0,
            "total_participants": len(participants),
            "leaderboard": leaderboard_wall,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    await update.message.reply_text("✅ Результаты отправлены всем участникам. Стена показывает лидерборд.")


async def cmd_stopquiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    quiz_state["active"] = False
    quiz_state["show_results"] = False
    quiz_state["current_question"] = 0
    quiz_state["answered_current"] = []
    save_quiz_state(load_quiz_questions())
    await update.message.reply_text("🛑 Квиз остановлен. Стена вернётся к показу фото.")


async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопки ответа на вопрос квиза."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    register_user(user.id, query.message.chat_id, user.first_name)

    if not quiz_state["active"]:
        await context.bot.send_message(query.message.chat_id, "Квиз уже завершён.")
        return

    _, q_idx_str, ans_idx_str = query.data.split(":")
    q_idx = int(q_idx_str)
    ans_idx = int(ans_idx_str)

    if q_idx != quiz_state["current_question"]:
        await context.bot.send_message(
            query.message.chat_id, "⏩ Этот вопрос уже не активен."
        )
        return

    uid = str(user.id)
    if uid in quiz_state["answered_current"]:
        await context.bot.send_message(
            query.message.chat_id, "Ты уже ответил на этот вопрос!"
        )
        return

    questions = load_quiz_questions()
    if q_idx >= len(questions):
        return

    correct_idx = questions[q_idx]["correct"]
    is_correct = ans_idx == correct_idx

    quiz_state["answered_current"].append(uid)
    if uid not in quiz_state["participants"]:
        quiz_state["participants"][uid] = {"name": user.first_name, "answers": [], "score": 0}
    quiz_state["participants"][uid]["answers"].append(is_correct)
    if is_correct:
        quiz_state["participants"][uid]["score"] += 1

    save_quiz_state(questions)

    correct_text = questions[q_idx]["options"][correct_idx]
    if is_correct:
        response = "✅ Верно!"
    else:
        response = f"❌ Неверно. Правильный ответ: *{correct_text}*"

    await context.bot.send_message(query.message.chat_id, response, parse_mode="Markdown")


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
        BotCommand("toast",       "🥂 Встать в очередь на тост"),
        BotCommand("canceltoast", "❌ Выйти из очереди на тост"),
    ]

    # Команды для администратора (включают всё гостевое + управление)
    admin_commands = guest_commands + [
        BotCommand("queue",         "📋 Список очереди тостов"),
        BotCommand("nexttoast",     "➡️ Следующий тост"),
        BotCommand("clearqueue",    "🗑 Очистить очередь тостов"),
        BotCommand("slideshow",     "🖼 Стена: режим слайдшоу"),
        BotCommand("presentation",  "🎞 Стена: режим презентация"),
        BotCommand("starsky",       "🌌 Стена: режим звёздное небо"),
        BotCommand("wallmode",      "📸 Стена: режим фото гостей"),
        BotCommand("status",        "📊 Статус текущей сессии фото"),
        BotCommand("newsession",    "🆕 Начать новую сессию фото"),
        BotCommand("clearsession",  "🗑 Удалить фото сессии"),
        BotCommand("startquiz",     "🎯 Начать квиз"),
        BotCommand("nextquestion",  "➡️ Следующий вопрос квиза"),
        BotCommand("results",       "🏆 Показать итоги квиза"),
        BotCommand("stopquiz",      "🛑 Остановить квиз"),
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

    # Создаём quiz_state.json если не существует
    init_quiz_state_file()

    # Загружаем известных пользователей с диска (переживают рестарт бота)
    load_known_users()

    app = Application.builder().token(BOT_TOKEN).post_init(setup_commands).build()

    # ── Общие команды ──────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("newsession", cmd_new_session))
    app.add_handler(CommandHandler("clearsession", cmd_clear_session))

    # ── Очередь тостов ────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("toast", cmd_toast))
    app.add_handler(CommandHandler("canceltoast", cmd_cancel_toast))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("nexttoast", cmd_next_toast))
    app.add_handler(CommandHandler("clearqueue", cmd_clear_queue))

    # ── Режим стены (только для администратора) ──────────────────────────────
    app.add_handler(CommandHandler("slideshow", cmd_slideshow))
    app.add_handler(CommandHandler("presentation", cmd_presentation))
    app.add_handler(CommandHandler("starsky", cmd_starsky))
    app.add_handler(CommandHandler("wallmode", cmd_wallmode))

    # ── Квиз (только для администратора) ─────────────────────────────────────
    app.add_handler(CommandHandler("startquiz", cmd_startquiz))
    app.add_handler(CommandHandler("nextquestion", cmd_nextquestion))
    app.add_handler(CommandHandler("results", cmd_results))
    app.add_handler(CommandHandler("stopquiz", cmd_stopquiz))

    # Ответы гостей на вопросы квиза (InlineKeyboard callback)
    app.add_handler(CallbackQueryHandler(handle_quiz_answer, pattern=r"^quiz:"))

    # ── Медиа и текст ─────────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Стикеры, голосовые и т.д. — тот же ответ «пришли фото»
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.PHOTO & ~filters.TEXT & ~filters.COMMAND,
        handle_text,
    ))

    logger.info("Бот запущен. Ожидаю фото и команды администратора...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
