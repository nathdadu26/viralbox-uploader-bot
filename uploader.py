#!/usr/bin/env python3
"""
Telegram File Uploader Bot with URL Shortener
Optimized for Koyeb Free Tier (Cold Start Friendly)

Workflow:
1. User sets API key: /set_api <API_KEY>
2. User sends media
3. Bot uploads to storage channel
4. Bot generates worker link
5. Bot shortens link using user's API key
6. Bot sends shortened link to user (as reply to user's file)
"""

import os
import asyncio
import string
import random
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
load_dotenv()

BOT_TOKEN = os.getenv("UPLOADER_BOT_TOKEN")
MONGO_URI = os.getenv("MONGODB_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "viralbox_db")
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID"))
WORKER_DOMAIN = os.getenv("WORKER_DOMAIN")
VIRALBOX_DOMAIN = os.getenv("VIRALBOX_DOMAIN", "viralbox.in")
PORT = int(os.getenv("PORT", 8000))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Your Koyeb app URL

# ---------------- MONGODB (Lazy Connection) ----------------
# Lazy init - connection sirf tab hogi jab pehli baar use ho
_mongo_client = None
_mongo_db = None

def get_db():
    """Lazy MongoDB connection - cold start me fast boot ke liye"""
    global _mongo_client, _mongo_db
    if _mongo_client is None:
        try:
            _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _mongo_db = _mongo_client[MONGO_DB_NAME]
            print(f"✅ Connected to MongoDB: {MONGO_DB_NAME}")
        except PyMongoError as e:
            raise RuntimeError(f"❌ MongoDB connection failed: {e}")
    return _mongo_db

def get_col(name):
    return get_db()[name]


# ---------------- HEALTH CHECK SERVER ----------------
# Yeh lightweight HTTP server Koyeb ko batata hai ki app alive hai
# Isse Koyeb container ko "healthy" samajhta hai aur webhook kaam karta hai

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Health check logs suppress karo (noisy hote hain)

def run_health_server():
    """Background thread me health check server chalao"""
    # Koyeb ka PORT environment variable use karta hai health check ke liye
    # python-telegram-bot apna port alag use karta hai
    health_port = int(os.getenv("HEALTH_PORT", 8080))
    server = HTTPServer(("0.0.0.0", health_port), HealthHandler)
    print(f"🏥 Health check server: port {health_port}")
    server.serve_forever()


# ---------------- UTIL ----------------
def generate_mapping_id(length=6):
    """Generate random alphanumeric mapping ID"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def shorten_url(api_key: str, long_url: str) -> str:
    """Shorten URL using viralbox.in API"""
    try:
        api_url = f"https://{VIRALBOX_DOMAIN}/api?api={api_key}&url={long_url}"
        response = requests.get(api_url, timeout=10)
        data = response.json()
        
        if data.get("status") == "success":
            return data.get("shortenedUrl", "")
        return ""
    except Exception as e:
        print(f"❌ Shortening failed: {e}")
        return ""


# ---------------- START HANDLER ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_id = user.id
    
    user_api = get_col("user_apis").find_one({"userId": user_id})
    
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


# ---------------- SET API HANDLER ----------------
async def set_api_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_api <API_KEY> command"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: /set_api <API_KEY>\n\n"
            f"Get your API key from: https://{VIRALBOX_DOMAIN}/member/tools/api"
        )
        return
    
    api_key = context.args[0]
    
    get_col("user_apis").update_one(
        {"userId": user_id},
        {"$set": {"userId": user_id, "apiKey": api_key}},
        upsert=True
    )
    
    await update.message.reply_text(
        "✅ API Key saved successfully!\n\n"
        "📂 Now send any media to upload!"
    )


# ---------------- UPLOAD HANDLER ----------------
async def upload_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media upload"""
    user_id = update.effective_user.id
    msg = update.message
    
    user_api = get_col("user_apis").find_one({"userId": user_id})
    
    if not user_api or "apiKey" not in user_api:
        await msg.reply_text(
            "⚠️ Please set your API key first!\n\n"
            f"👉 Get it from: https://{VIRALBOX_DOMAIN}/member/tools/api\n"
            f"👉 Then send: /set_api <API_KEY>"
        )
        return
    
    api_key = user_api["apiKey"]
    
    try:
        sent_msg = await msg.copy(chat_id=STORAGE_CHANNEL_ID)
        stored_msg_id = sent_msg.message_id
        
        mapping_id = generate_mapping_id()
        
        get_col("mappings").insert_one({
            "mapping": mapping_id,
            "message_id": stored_msg_id
        })
        
        worker_link = f"{WORKER_DOMAIN}/{mapping_id}"
        short_url = shorten_url(api_key, worker_link)
        
        if not short_url:
            await msg.reply_text(
                "❌ URL shortening failed!\n"
                "Please check your API key.",
                reply_to_message_id=msg.message_id
            )
            return
        
        get_col("links").insert_one({
            "longURL": worker_link,
            "shortURL": short_url
        })
        
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
def main():
    """Initialize and run the bot"""
    if not all([BOT_TOKEN, MONGO_URI, STORAGE_CHANNEL_ID, WORKER_DOMAIN, WEBHOOK_URL]):
        raise RuntimeError("❌ Missing required environment variables!")

    # Health check server background me start karo
    # Yeh Koyeb ko signal deta hai ki container ready hai
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("set_api", set_api_handler))
    app.add_handler(MessageHandler(
        filters.Document.ALL | 
        filters.PHOTO | 
        filters.VIDEO | 
        filters.AUDIO | 
        filters.VOICE |
        filters.VIDEO_NOTE,
        upload_media
    ))
    
    print("🤖 Uploader Bot is running...")
    print(f"📂 Storage Channel: {STORAGE_CHANNEL_ID}")
    print(f"🌐 Worker Domain: {WORKER_DOMAIN}")
    print(f"🔗 Shortener: {VIRALBOX_DOMAIN}")
    print(f"💾 Database: {MONGO_DB_NAME}")
    print(f"🌍 Webhook URL: {WEBHOOK_URL}")
    
    # Webhook mode - Cold start ke liye best approach
    # Telegram request aate hi container wake up hota hai
    # aur python-telegram-bot apna aserver khud handle karta hai
    app.run_webhook(
        listen='0.0.0.0',
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )


# ---------------- ENTRY POINT ----------------
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
