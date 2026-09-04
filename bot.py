import os
import logging
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ================== SOZLAMALAR ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")                       # .env faylidan o'qiladi
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))      # .env faylidan o'qiladi
USER_THRESHOLD = 10                                        # nechta user start bosgach alert yuborilsin
DB_PATH = "bot_users.db"
PORT = int(os.getenv("PORT", "10000"))                    # Render shu portni kutadi

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. .env faylida BOT_TOKEN=... qo'shing.")
if not ADMIN_CHAT_ID:
    raise RuntimeError("ADMIN_CHAT_ID topilmadi. .env faylida ADMIN_CHAT_ID=... qo'shing.")

MAINTENANCE_MESSAGE = (
    "Bot is currently not working due to a technical issue. "
    "It will be fixed soon, stay tuned 🙏"
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ================== RENDER UCHUN FAKE WEB SERVER ==================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot ishlayapti")

    def log_message(self, format, *args):
        pass  # HTTP loglarni o'chirish, faqat bot loglari ko'rinsin


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    logger.info(f"Health-check server {PORT}-portda ishga tushdi")
    server.serve_forever()


# ================== DATABASE ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            started_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_user_if_new(user) -> bool:
    """Userni bazaga qo'shadi. Yangi user bo'lsa True qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user.id,))
    exists = cur.fetchone() is not None

    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, username, first_name, started_at) VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.first_name, datetime.now().isoformat()),
        )
        conn.commit()

    conn.close()
    return not exists


def get_user_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, first_name, started_at FROM users ORDER BY started_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


# ================== HANDLERLAR ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = add_user_if_new(user)
    count = get_user_count()

    await update.message.reply_text(MAINTENANCE_MESSAGE)

    if is_new:
        logger.info(f"Yangi user: {user.id} (@{user.username}) | Jami: {count}")

        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🆕 Yangi user: @{user.username or '—'} ({user.first_name})\nJami userlar: {count}",
            )
        except Exception as e:
            logger.error(f"Adminga xabar yuborishda xato: {e}")

        if count == USER_THRESHOLD:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🔥 {USER_THRESHOLD} ta user /start bosdi! Serverni yoqish vaqti keldi.",
                )
            except Exception as e:
                logger.error(f"Threshold alert xato: {e}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat admin uchun: /stats — jami userlar sonini ko'rsatadi."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    count = get_user_count()
    await update.message.reply_text(f"Jami userlar: {count}")


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat admin uchun: /users — oxirgi userlar ro'yxati."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    rows = get_all_users()[:30]
    if not rows:
        await update.message.reply_text("Hali user yo'q.")
        return
    text = "\n".join(
        f"{uid} | @{uname or '—'} | {fname} | {ts}"
        for uid, uname, fname, ts in rows
    )
    await update.message.reply_text(text[:4000])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Matn kelganda ishlaydigan joy (TTS uchun) — hozircha maintenance."""
    await update.message.reply_text(MAINTENANCE_MESSAGE)
    # AI ishga tushganda shu yerga synthesize_speech() chaqiruvi qo'yiladi
    # text = update.message.text
    # audio_path = await synthesize_speech(text)
    # await update.message.reply_voice(voice=open(audio_path, "rb"))


# ================== TTS (API/model ulanganda ishga tushadi) ==================
async def synthesize_speech(text: str) -> str:
    """
    Bu funksiya matnni ovozga (audio faylga) o'giradi va audio fayl yo'lini qaytaradi.
    Hozircha bo'sh — o'z TTS modelingiz yoki API tayyor bo'lganda shu yerga ulanadi.

    Masalan (o'z serveringiz):
        import requests
        response = requests.post(
            "https://SIZNING_SERVER/tts",
            json={"text": text},
        )
        audio_bytes = response.content
        out_path = "output.ogg"
        with open(out_path, "wb") as f:
            f.write(audio_bytes)
        return out_path
    """
    raise NotImplementedError("TTS API hali ulanmagan")


# ================== ISHGA TUSHIRISH ==================
def main():
    init_db()

    # Render "web service" portni kutadi — shu uchun alohida threadda fake server
    threading.Thread(target=run_health_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ishga tushdi (monitoring rejimida)...")
    app.run_polling()


if __name__ == "__main__":
    main()