#!/usr/bin/env python3
"""
ViralBox Uploader Bot - Modern Version (python-telegram-bot v21+)
This version removes the deprecated 'before_server_start' parameter
"""

import os
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from pymongo import MongoClient
import asyncio

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
PORT = int(os.getenv('PORT', 8000))
STORAGE_CHANNEL = os.getenv('STORAGE_CHANNEL', '-1003830165800')
WORKER_DOMAIN = os.getenv('WORKER_DOMAIN', 'https://server.viralboxfiles.workers.dev')
SHORTENER = os.getenv('SHORTENER', 'viralbox.in')

# Validate required environment variables
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN environment variable is required")
if not MONGO_URI:
    raise RuntimeError("❌ MONGO_URI environment variable is required")
if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL environment variable is required")

# Connect to MongoDB
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client['viralbox_db']
    # Test connection
    mongo_client.admin.command('ping')
    logger.info(f"✅ Connected to MongoDB: {db.name}")
except Exception as e:
    logger.error(f"❌ MongoDB connection failed: {e}")
    raise RuntimeError(f"❌ MongoDB connection failed: {e}")

# Print configuration
logger.info("🤖 Uploader Bot is running...")
logger.info(f"📂 Storage Channel: {STORAGE_CHANNEL}")
logger.info(f"🌐 Worker Domain: {WORKER_DOMAIN}")
logger.info(f"🔗 Shortener: {SHORTENER}")
logger.info(f"💾 Database: {db.name}")
logger.info(f"🌍 Webhook URL: {WEBHOOK_URL}")
logger.info(f"🏥 Health check: port {PORT} (/, /health, /healthz)")


# Command handlers
async def start_command(update, context):
    """Handle /start command"""
    await update.message.reply_text(
        "👋 Welcome to ViralBox Uploader Bot!\n\n"
        "Send me a file and I'll upload it for you."
    )


async def help_command(update, context):
    """Handle /help command"""
    await update.message.reply_text(
        "📖 Help:\n\n"
        "Just send me any file and I'll process it for you.\n"
        "Supported commands:\n"
        "/start - Start the bot\n"
        "/help - Show this help message"
    )


async def handle_file(update, context):
    """Handle file uploads"""
    try:
        # Get the file
        file = None
        if update.message.document:
            file = update.message.document
        elif update.message.video:
            file = update.message.video
        elif update.message.photo:
            file = update.message.photo[-1]  # Get largest photo
        
        if file:
            await update.message.reply_text("📤 Processing your file...")
            # Add your file processing logic here
            # Example: Save to database, upload to storage, etc.
            logger.info(f"Received file: {getattr(file, 'file_name', 'photo')}")
        else:
            await update.message.reply_text("Please send me a valid file.")
    except Exception as e:
        logger.error(f"Error handling file: {e}")
        await update.message.reply_text("❌ Error processing file. Please try again.")


async def health_check(update, context):
    """Health check endpoint"""
    await update.message.reply_text("✅ Bot is healthy!")


def main():
    """Main function to run the bot"""
    try:
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("health", health_check))
        application.add_handler(MessageHandler(
            filters.Document.ALL | filters.VIDEO | filters.PHOTO,
            handle_file
        ))
        
        # Run with webhook (modern approach - no before_server_start parameter)
        application.run_webhook(
            listen='0.0.0.0',
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            allowed_updates=['message', 'callback_query']
        )
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise


if __name__ == '__main__':
    main()
