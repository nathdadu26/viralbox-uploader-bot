#!/usr/bin/env python3
"""
Telegram File Uploader Bot with URL Shortener

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

# ---------------- MONGODB ----------------
try:
    mongo_client = MongoClient(MONGO_URI)
    mongo_db = mongo_client[MONGO_DB_NAME]
    mappings_col = mongo_db["mappings"]
    links_col = mongo_db["links"]
    user_apis_col = mongo_db["user_apis"]
    print(f"✅ Connected to MongoDB: {MONGO_DB_NAME}")
except PyMongoError as e:
    raise RuntimeError(f"❌ MongoDB connection failed: {e}")


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
    
    # Check if user has API key set
    user_api = user_apis_col.find_one({"userId": user_id})
    
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
    
    # Save or update API key
    user_apis_col.update_one(
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
    
    # Check if user has API key
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
        # Step 1: Copy file to storage channel
        sent_msg = await msg.copy(chat_id=STORAGE_CHANNEL_ID)
        stored_msg_id = sent_msg.message_id
        
        # Step 2: Generate mapping ID
        mapping_id = generate_mapping_id()
        
        # Step 3: Save mapping to MongoDB
        mappings_col.insert_one({
            "mapping": mapping_id,
            "message_id": stored_msg_id
        })
        
        # Step 4: Generate worker link
        worker_link = f"{WORKER_DOMAIN}/{mapping_id}"
        
        # Step 5: Shorten URL using user's API key
        short_url = shorten_url(api_key, worker_link)
        
        if not short_url:
            await msg.reply_text(
                "❌ URL shortening failed!\n"
                "Please check your API key.",
                reply_to_message_id=msg.message_id
            )
            return
        
        # Step 6: Save links to database
        links_col.insert_one({
            "longURL": worker_link,
            "shortURL": short_url
        })
        
        # Step 7: Send only shortened link (as reply to user's file)
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
    
    # Build application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
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
    print(f"🏥 Health check: port {PORT} (/, /health, /healthz)")
    
    # Run webhook
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
