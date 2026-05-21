"""
Простой HTTP-сервер для отдачи веб-стены и фото.
Порт 8080, без внешних зависимостей (только стандартная библиотека Python).
"""

import json
import logging
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

PHOTOS_DIR = Path("photos")
META_FILE = Path("photos_meta.json")
SESSION_FILE = Path("session.json")
WALL_FILE = Path("wall.html")
PORT = 8080

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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
        elif path.startswith("/photo/"):
            filename = path[len("/photo/"):]
            self.serve_photo(filename)
        else:
            self.send_error(404, "Страница не найдена")

    def serve_wall(self):
        """Отдаёт главную страницу wall.html."""
        if not WALL_FILE.exists():
            self.send_error(404, "Файл wall.html не найден")
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
            self.send_error(500, "Внутренняя ошибка сервера")

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
                # Фильтруем только фото текущей сессии
                data = [p for p in all_meta if p.get("session_id") == session_id]
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Ошибка чтения photos_meta.json: %s", e)
                data = []

        # Последние 12, новые первыми
        data = list(reversed(data[-12:]))

        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def serve_photo(self, filename: str):
        """Отдаёт файл фото из папки photos/."""
        # Защита от path traversal: разрешаем только имя файла без слешей
        if "/" in filename or "\\" in filename or ".." in filename:
            self.send_error(400, "Некорректное имя файла")
            return

        filepath = PHOTOS_DIR / filename
        if not filepath.exists():
            self.send_error(404, "Фото не найдено")
            return

        try:
            content = filepath.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(filepath))
            if not mime_type:
                mime_type = "image/jpeg"

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            # Кэшируем фото на 1 час (они не меняются)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except OSError as e:
            logger.error("Ошибка чтения фото %s: %s", filename, e)
            self.send_error(500, "Внутренняя ошибка сервера")


def main():
    """Запускает HTTP-сервер."""
    # Убеждаемся, что папка photos существует
    PHOTOS_DIR.mkdir(exist_ok=True)

    server = HTTPServer(("0.0.0.0", PORT), WallHandler)
    logger.info("Сервер запущен на http://0.0.0.0:%d", PORT)
    logger.info("Открой в браузере: http://localhost:%d", PORT)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Сервер остановлен")
        server.server_close()


if __name__ == "__main__":
    main()
