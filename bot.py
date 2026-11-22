import os
import asyncio
import threading
import random
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

# --- AI Girl Persona System Prompt ---
SYSTEM_PROMPT = """
You are Aarohi, a 20-year-old fun, witty, and friendly Indian college girl chatting in a Telegram group.
Rules for chatting:
1. Reply in casual, natural Hinglish (mix of Hindi and English in Roman script, like "haan yaar", "kya scene hai", "arre chill kar").
2. Keep replies short, natural, and conversational (1 to 2 sentences max—never write long paragraphs like an AI assistant).
3. Use light, natural emojis occasionally (😊, 😂, 🙄, ✨) but don't overdo it.
4. Be playful, friendly, and engaging with group members. Never sound robotic or say "As an AI".
5. If someone is rude, give a witty, sassy comeback and move on.
"""

chat_history = {}

def get_ai_reply(chat_id: int, user_name: str, user_text: str) -> str:
    if chat_id not in chat_history:
        chat_history[chat_id] = []

    chat_history[chat_id].append({"role": "user", "content": f"{user_name}: {user_text}"})
    chat_history[chat_id] = chat_history[chat_id][-8:]

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[chat_id],
            temperature=0.8,
            max_tokens=100
        )
        reply = response.choices[0].message.content.strip()
        chat_history[chat_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        print(f"Groq Error: {e}")
        return "Network thoda slow chal raha hai yaar, ek sec baad msg kar 😅"

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey! Mujhe kisi bhi group mein add karo, mast baatein karenge ✨")

async def welcome_on_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text("Hii everyone! 👋 Thanks group mein add karne ke liye, kya chal raha hai yahan?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_type = update.message.chat.type
    text = update.message.text
    user_name = update.message.from_user.first_name or "Friend"
    bot_username = context.bot.username.lower()

    if chat_type == "private":
        reply = get_ai_reply(update.message.chat_id, user_name, text)
        await update.message.reply_text(reply)
        return

    is_replied_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user.id == context.bot.id
    )
    is_mentioned = f"@{bot_username}" in text.lower() or "aarohi" in text.lower()
    random_chime_in = random.random() < 0.25

    if is_replied_to_bot or is_mentioned or random_chime_in:
        reply = get_ai_reply(update.message.chat_id, user_name, text)
        await update.message.reply_text(reply)

# --- Dummy Web Server for Render 24x7 Free Hosting ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Aarohi Telegram Bot is Live 24x7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def main():
    # Fix for Python 3.14 asyncio event loop in MainThread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_on_add))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
