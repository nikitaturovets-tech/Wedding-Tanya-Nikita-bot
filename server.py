"""
Простой HTTP-сервер для отдачи веб-стены и фото.
Порт 8080, без внешних зависимостей (только стандартная библиотека Python).

Поддерживает Server-Sent Events (SSE) на /events —
браузер получает мгновенное уведомление при появлении нового фото.
"""

import io
import json
import logging
import mimetypes
import os
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

PHOTOS_DIR       = Path("photos")
PRESET_DIR       = Path("photos_preset")
META_FILE        = Path("photos_meta.json")
SESSION_FILE     = Path("session.json")
QUIZ_STATE_FILE  = Path("quiz_state.json")
WALL_MODE_FILE   = Path("wall_mode.json")
WALL_FILE        = Path("wall.html")
PORT = 8080

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── SSE: список активных клиентов-подписчиков ──────────────────────────────
# Каждый элемент — объект wfile (сокет) открытого SSE-соединения.
_sse_clients: list = []
_sse_lock = threading.Lock()

# Время последнего изменения photos_meta.json — используется для детекции новых фото
_last_meta_mtime: float = 0.0


def _meta_mtime() -> float:
    """Возвращает время изменения photos_meta.json, или 0 если файла нет."""
    try:
        return META_FILE.stat().st_mtime
    except OSError:
        return 0.0


def _broadcast_refresh():
    """Отправляет SSE-событие 'refresh' всем подключённым клиентам."""
    dead = []
    with _sse_lock:
        for wfile in _sse_clients:
            try:
                wfile.write(b"data: refresh\n\n")
                wfile.flush()
            except OSError:
                dead.append(wfile)
        for wfile in dead:
            _sse_clients.remove(wfile)


def _watcher_thread():
    """
    Фоновый поток: следит за изменением photos_meta.json.
    При изменении рассылает SSE-событие всем браузерам.
    """
    global _last_meta_mtime
    _last_meta_mtime = _meta_mtime()

    while True:
        time.sleep(0.5)  # проверяем каждые 0.5 секунды
        mtime = _meta_mtime()
        if mtime != _last_meta_mtime:
            _last_meta_mtime = mtime
            _broadcast_refresh()
            logger.info("SSE: разослано событие refresh (%d клиентов)", len(_sse_clients))


# ── Многопоточный HTTP-сервер ───────────────────────────────────────────────
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer с поддержкой многопоточности — нужно для SSE."""
    daemon_threads = True  # потоки завершаются вместе с процессом


def load_session() -> dict:
    """Читает текущую сессию из файла."""
    if not SESSION_FILE.exists():
        return {"current": ""}
    try:
        with SESSION_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"current": ""}


class WallHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов для свадебной стены."""

    def log_message(self, format, *args):
        """Переопределяем логирование, чтобы использовать наш логгер."""
        logger.info("%s - %s", self.address_string(), format % args)

    def send_cors_headers(self):
        """Добавляет CORS-заголовки для доступа с любого источника."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """Обрабатывает preflight-запросы CORS."""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """Маршрутизация GET-запросов."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.serve_wall()
        elif path == "/meta":
            self.serve_meta()
        elif path == "/quiz":
            self.serve_quiz()
        elif path == "/mode":
            self.serve_mode()
        elif path == "/preset-list":
            self.serve_preset_list()
        elif path == "/events":
            self.serve_events()
        elif path == "/download":
            self.serve_download()
        elif path == "/starry-bg":
            self.serve_starry_bg()
        elif path.startswith("/photo/"):
            filename = path[len("/photo/"):]
            self.serve_photo(filename)
        elif path.startswith("/preset/"):
            filename = path[len("/preset/"):]
            self.serve_preset_photo(filename)
        else:
            self.send_error(404)

    def serve_wall(self):
        """Отдаёт главную страницу wall.html."""
        if not WALL_FILE.exists():
            self.send_error(404)
            return
        try:
            content = WALL_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.error("Ошибка чтения wall.html: %s", e)
            self.send_error(500)

    def serve_meta(self):
        """
        Отдаёт метаданные фото текущей сессии в формате JSON.
        Возвращает последние 12 фото в обратном порядке (новые первыми).
        """
        session_id = load_session().get("current", "")

        if not META_FILE.exists():
            data = []
        else:
            try:
                with META_FILE.open("r", encoding="utf-8") as f:
                    all_meta = json.load(f)
                data = [p for p in all_meta if p.get("session_id") == session_id]
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Ошибка чтения photos_meta.json: %s", e)
                data = []

        data = list(reversed(data[-12:]))
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_mode(self):
        """
        Отдаёт wall_mode.json — текущий режим стены: slideshow или wall.
        Если файл не существует — возвращает режим по умолчанию (wall).
        """
        if not WALL_MODE_FILE.exists():
            data = {"mode": "wall"}
        else:
            try:
                with WALL_MODE_FILE.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = {"mode": "wall"}

        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_preset_list(self):
        """
        Возвращает список файлов из photos_preset/ в формате JSON.
        Используется стеной для загрузки слайдшоу.
        """
        PRESET_DIR.mkdir(exist_ok=True)
        exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        files = sorted(
            f.name for f in PRESET_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in exts
        )
        body = json.dumps(files, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_preset_photo(self, filename: str):
        """Отдаёт файл из папки photos_preset/."""
        if "/" in filename or "\\" in filename or ".." in filename:
            self.send_error(400)
            return
        filepath = PRESET_DIR / filename
        if not filepath.exists():
            self.send_error(404)
            return
        try:
            content = filepath.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(filepath))
            if not mime_type:
                mime_type = "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.error("Ошибка чтения preset фото %s: %s", filename, e)
            self.send_error(500)

    def serve_quiz(self):
        """
        Отдаёт quiz_state.json — текущее состояние квиза для веб-стены.
        Если файл не существует — возвращает заглушку с active: false.
        """
        if not QUIZ_STATE_FILE.exists():
            data = {"active": False, "show_results": False}
        else:
            try:
                with QUIZ_STATE_FILE.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Ошибка чтения quiz_state.json: %s", e)
                data = {"active": False, "show_results": False}

        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_events(self):
        """
        SSE-эндпоинт: держит соединение открытым и отправляет событие
        'refresh' когда появляется новое фото.
        Браузер подключается один раз и мгновенно получает обновления.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_cors_headers()
        self.end_headers()

        # Регистрируем клиента
        with _sse_lock:
            _sse_clients.append(self.wfile)

        try:
            # Отправляем heartbeat каждые 20 секунд чтобы соединение не закрылось
            while True:
                time.sleep(20)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with _sse_lock:
                if self.wfile in _sse_clients:
                    _sse_clients.remove(self.wfile)

    def serve_download(self):
        """
        Упаковывает все фото из папки photos/ в ZIP-архив
        и отдаёт его браузеру для скачивания.
        """
        photos = sorted(PHOTOS_DIR.glob("*")) if PHOTOS_DIR.exists() else []
        photos = [p for p in photos if p.is_file()]

        if not photos:
            body = "<html><body><h2>Фото пока нет 📷</h2></body></html>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
                for photo in photos:
                    zf.write(photo, arcname=photo.name)
            zip_bytes = buf.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="wedding_tanya_nikita_photos.zip"'
            )
            self.end_headers()
            self.wfile.write(zip_bytes)
            logger.info("ZIP скачан: %d фото, %.1f МБ", len(photos), len(zip_bytes) / 1024 / 1024)
        except OSError as e:
            logger.error("Ошибка создания ZIP: %s", e)
            self.send_error(500)

    def serve_starry_bg(self):
        """Отдаёт фото звёздного неба для fullscreen-режима."""
        filepath = Path("starry_bg.jpg")
        if not filepath.exists():
            self.send_error(404)
            return
        try:
            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.error("Ошибка чтения starry_bg.jpg: %s", e)
            self.send_error(500)

    def serve_photo(self, filename: str):
        """Отдаёт файл фото из папки photos/."""
        if "/" in filename or "\\" in filename or ".." in filename:
            self.send_error(400)
            return

        filepath = PHOTOS_DIR / filename
        if not filepath.exists():
            self.send_error(404)
            return

        try:
            content = filepath.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(filepath))
            if not mime_type:
                mime_type = "image/jpeg"

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.error("Ошибка чтения фото %s: %s", filename, e)
            self.send_error(500)


def main():
    """Запускает HTTP-сервер с поддержкой SSE."""
    PHOTOS_DIR.mkdir(exist_ok=True)
    PRESET_DIR.mkdir(exist_ok=True)

    # Запускаем фоновый поток-наблюдатель за новыми фото
    watcher = threading.Thread(target=_watcher_thread, daemon=True)
    watcher.start()
    logger.info("Наблюдатель за фото запущен.")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), WallHandler)
    logger.info("Сервер запущен на http://0.0.0.0:%d", PORT)
    logger.info("Открой в браузере: http://localhost:%d", PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер остановлен")
        server.server_close()


if __name__ == "__main__":
    main()
