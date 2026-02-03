#!/usr/bin/env python3
"""
Telegram File Uploader Bot with URL Shortener — WEBHOOK MODE

Workflow:
1. User sets API key: /set_api <API_KEY>
2. User sends media
3. Bot uploads to storage channel
4. Bot generates worker link
5. Bot shortens link using user's API key
6. Bot sends shortened link to user (as reply to user's file)

Webhook:
- Telegram updates aate hain: https://<WEBHOOK_HOST>/<BOT_TOKEN>
- PTB ka built-in aiohttp webhook server use hota hai
- Health check (/, /health, /healthz) ek alag thread par HTTP server se
"""

import os
import string
import random
import asyncio
import requests
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
load_dotenv()

BOT_TOKEN            = os.getenv("UPLOADER_BOT_TOKEN")
MONGO_URI            = os.getenv("MONGODB_URI")
MONGO_DB_NAME        = os.getenv("MONGO_DB_NAME", "viralbox_db")
STORAGE_CHANNEL_ID   = int(os.getenv("STORAGE_CHANNEL_ID"))
WORKER_DOMAIN        = os.getenv("WORKER_DOMAIN")
VIRALBOX_DOMAIN      = os.getenv("VIRALBOX_DOMAIN", "viralbox.in")

# --- Webhook Config ---
# PORT         : Render / Railway assign karta hai (e.g. 10000)
#                Webhook server isi port par sunta hai
# WEBHOOK_HOST : Aapka public HTTPS domain
#                e.g. https://mybot.onrender.com   (trailing slash MAT rakhein)
# HEALTH_PORT  : Alag port health check ke liye (default 8000)
#                Render mein agar sirf ek PORT hai toh HEALTH_PORT = PORT rakhein
#                aur Render health check path ko /health set karein
WEBHOOK_PORT   = int(os.getenv("PORT", 10000))
WEBHOOK_HOST   = os.getenv("WEBHOOK_HOST", "")          # ⚠️  MUST set!
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")        # Recommended
HEALTH_PORT    = int(os.getenv("HEALTH_PORT", 8000))

# ---------------- MONGODB ----------------
try:
    mongo_client  = MongoClient(MONGO_URI)
    mongo_db      = mongo_client[MONGO_DB_NAME]
    mappings_col  = mongo_db["mappings"]
    links_col     = mongo_db["links"]
    user_apis_col = mongo_db["user_apis"]
    print(f"✅ Connected to MongoDB: {MONGO_DB_NAME}")
except PyMongoError as e:
    raise RuntimeError(f"❌ MongoDB connection failed: {e}")


# ---------------- HEALTH CHECK SERVER ----------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/health', '/healthz']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress logs


def start_health_server():
    server = HTTPServer(('0.0.0.0', HEALTH_PORT), HealthCheckHandler)
    print(f"🏥 Health check server running on port {HEALTH_PORT}")
    server.serve_forever()


# ---------------- UTIL ----------------
def generate_mapping_id(length=6):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def shorten_url(api_key: str, long_url: str) -> str:
    try:
        api_url  = f"https://{VIRALBOX_DOMAIN}/api?api={api_key}&url={long_url}"
        response = requests.get(api_url, timeout=10)
        data     = response.json()
        if data.get("status") == "success":
            return data.get("shortenedUrl", "")
        return ""
    except Exception as e:
        print(f"❌ Shortening failed: {e}")
        return ""


# ---------------- HANDLERS ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    user_api = user_apis_col.find_one({"userId": user.id})

    if user_api and "apiKey" in user_api:
        await update.message.reply_text("📂 Send A Media To Upload !")
    else:
        welcome_msg = (
            f"👋 Welcome {user.first_name} to {VIRALBOX_DOMAIN} Uploader Bot!\n\n"
            f"1️⃣ Create an Account on {VIRALBOX_DOMAIN}\n"
            f"2️⃣ Go To 👉 https://{VIRALBOX_DOMAIN}/member/tools/api\n"
            f"3️⃣ Copy your API Key\n"
            f"4️⃣ Send /set_api <API_KEY>\n"
            f"5️⃣ Send any media to upload !"
        )
        await update.message.reply_text(welcome_msg)


async def set_api_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /set_api <API_KEY>\n\n"
            f"Get your API key from: https://{VIRALBOX_DOMAIN}/member/tools/api"
        )
        return

    user_apis_col.update_one(
        {"userId": user_id},
        {"$set": {"userId": user_id, "apiKey": context.args[0]}},
        upsert=True
    )
    await update.message.reply_text(
        "✅ API Key saved successfully!\n\n"
        "📂 Now send any media to upload!"
    )


async def upload_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    msg      = update.message
    user_api = user_apis_col.find_one({"userId": user_id})

    if not user_api or "apiKey" not in user_api:
        await msg.reply_text(
            "⚠️ Please set your API key first!\n\n"
            f"👉 Get it from: https://{VIRALBOX_DOMAIN}/member/tools/api\n"
            f"👉 Then send: /set_api <API_KEY>"
        )
        return

    api_key = user_api["apiKey"]

    try:
        # 1️⃣  Storage channel mein copy
        sent_msg      = await msg.copy(chat_id=STORAGE_CHANNEL_ID)
        stored_msg_id = sent_msg.message_id

        # 2️⃣  Random mapping ID
        mapping_id = generate_mapping_id()

        # 3️⃣  MongoDB mein save
        mappings_col.insert_one({
            "mapping":    mapping_id,
            "message_id": stored_msg_id
        })

        # 4️⃣  Worker link
        worker_link = f"{WORKER_DOMAIN}/{mapping_id}"

        # 5️⃣  Shorten
        short_url = shorten_url(api_key, worker_link)

        if not short_url:
            await msg.reply_text(
                "❌ URL shortening failed!\nPlease check your API key.",
                reply_to_message_id=msg.message_id
            )
            return

        # 6️⃣  Links save
        links_col.insert_one({
            "longURL":  worker_link,
            "shortURL": short_url
        })

        # 7️⃣  User ko reply
        await msg.reply_text(
            short_url,
            reply_to_message_id=msg.message_id
        )
        print(f"✅ Upload complete: {mapping_id} -> {short_url}")

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        await msg.reply_text(
            "❌ Upload failed! Please try again later.",
            reply_to_message_id=msg.message_id
        )


# ---------------- MAIN ----------------
async def main():
    # --- Validation ---
    missing = [v for v, val in {
        "UPLOADER_BOT_TOKEN": BOT_TOKEN,
        "MONGODB_URI":        MONGO_URI,
        "STORAGE_CHANNEL_ID": STORAGE_CHANNEL_ID,
        "WORKER_DOMAIN":      WORKER_DOMAIN,
        "WEBHOOK_HOST":       WEBHOOK_HOST,
    }.items() if not val]

    if missing:
        raise RuntimeError(f"❌ Missing env vars: {', '.join(missing)}")

    # --- Health check thread (alag port par) ---
    if HEALTH_PORT != WEBHOOK_PORT:
        Thread(target=start_health_server, daemon=True).start()

    # --- Application ---
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   start_handler))
    app.add_handler(CommandHandler("set_api", set_api_handler))
    app.add_handler(MessageHandler(
        filters.Document.ALL |
        filters.PHOTO       |
        filters.VIDEO       |
        filters.AUDIO       |
        filters.VOICE       |
        filters.VIDEO_NOTE,
        upload_media
    ))

    # --- Webhook URL ---
    webhook_url = f"{WEBHOOK_HOST}/{BOT_TOKEN}"

    # --- Logs ---
    print("=" * 52)
    print("  🤖  Uploader Bot — WEBHOOK MODE")
    print("=" * 52)
    print(f"  📂  Storage Channel  : {STORAGE_CHANNEL_ID}")
    print(f"  🌐  Worker Domain    : {WORKER_DOMAIN}")
    print(f"  🔗  Shortener        : {VIRALBOX_DOMAIN}")
    print(f"  💾  Database         : {MONGO_DB_NAME}")
    print(f"  🌍  Webhook Host     : {WEBHOOK_HOST}")
    print(f"  🔌  Webhook Port     : {WEBHOOK_PORT}")
    print(f"  🔗  Webhook URL      : {webhook_url}")
    print(f"  🏥  Health Port      : {HEALTH_PORT}")
    if WEBHOOK_SECRET:
        print(f"  🔐  Webhook Secret   : *** set ***")
    print("=" * 52)

    # --- run_webhook ---
    # PTB automatically Telegram par webhook register karta hai
    # aur ek aiohttp server start karta hai.
    await app.run_webhook(
        listen       = "0.0.0.0",
        port         = WEBHOOK_PORT,
        url_path     = f"/{BOT_TOKEN}",        # local route
        webhook_url  = webhook_url,            # public URL → Telegram
        secret_token = WEBHOOK_SECRET or None, # optional security
    )


# ---------------- ENTRY POINT ----------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
