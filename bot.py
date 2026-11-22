import os
import asyncio
import threading
import random
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """
You are Aarohi, a 20-year-old fun, witty, and friendly Indian college girl chatting on Telegram.
Rules for chatting:
1. Reply in casual, natural Hinglish (mix of Hindi and English in Roman script, like "haan yaar", "kya scene hai", "arre chill kar").
2. Keep replies short, natural, and conversational (1 to 2 sentences max—never write long paragraphs like an AI assistant).
3. Use light, natural emojis occasionally (😊, 😂, 🙄, ✨) but don't overdo it.
4. Be playful, friendly, and engaging. Never sound robotic or say "As an AI".
5. If someone is rude, give a witty, sassy comeback and move on.
"""

chat_history = {}
chat_reply_chance = {}  # Per-group reply frequency (default 0.30)
cached_model = None

MOOD_LEVELS = {
    "silent": 0.0,
    "chill": 0.15,
    "normal": 0.30,
    "hyper": 0.65,
}

def get_active_model() -> str:
    global cached_model
    if cached_model:
        return cached_model

    models = groq_client.models.list()
    blocked = ['whisper', 'guard', 'tts', 'orpheus', 'canopylabs', 'playai', 'distil', 'vision', 'compound']
    available = [
        m.id for m in models.data
        if not any(b in m.id.lower() for b in blocked)
    ]

    for preferred in ['llama-3.3-70b', 'llama', 'qwen', 'gemma', 'mixtral']:
        match = next((m for m in available if preferred in m.lower()), None)
        if match:
            cached_model = match
            print(f"Selected Groq Model: {cached_model}")
            return cached_model

    cached_model = available[0]
    return cached_model

def ask_groq(messages: list, max_tokens: int = 110, temperature: float = 0.85) -> str:
    model_id = get_active_model()
    response = groq_client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()

def get_ai_reply(chat_id: int, user_name: str, user_text: str) -> str:
    if not GROQ_API_KEY:
        return "API key missing hai yaar, Render settings check kar 😅"

    if chat_id not in chat_history:
        chat_history[chat_id] = []

    chat_history[chat_id].append({"role": "user", "content": f"{user_name}: {user_text}"})
    chat_history[chat_id] = chat_history[chat_id][-8:]

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[chat_id]
        reply = ask_groq(messages)
        chat_history[chat_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        print(f"GROQ API ERROR DETAILS: {repr(e)}")
        return "Ek sec ruk yaar, thoda glitch aa gaya 😅"

async def send_with_typing(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(random.uniform(1.2, 2.2))
    await update.message.reply_text(text)

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Hii! 👋 Main Aarohi hoon ✨\n\n"
        "Commands:\n"
        "• /mood <silent/chill/normal/hyper> - Group mein kitna bolna hai set karo\n"
        "• /roast - Kisi ke msg par reply karke roast maaro 🔥\n"
        "• /tod - Truth or Dare khelo 🎲"
    )
    await send_with_typing(update, context, msg)

async def set_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        current = chat_reply_chance.get(chat_id, 0.30)
        await update.message.reply_text(
            f"Abhi mera reply rate {int(current * 100)}% hai.\n"
            "Change karne ke liye likho: /mood silent, /mood chill, /mood normal, ya /mood hyper"
        )
        return

    mode = context.args[0].lower()
    if mode not in MOOD_LEVELS:
        await update.message.reply_text("Valid options: silent, chill, normal, hyper 🙄")
        return

    chat_reply_chance[chat_id] = MOOD_LEVELS[mode]
    await send_with_typing(update, context, f"Done! Ab mera mood '{mode}' ({int(MOOD_LEVELS[mode] * 100)}%) par set ho gaya hai 😎✨")

async def roast_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_name = None
    target_msg = ""

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_name = update.message.reply_to_message.from_user.first_name
        target_msg = update.message.reply_to_message.text or ""
    elif context.args:
        target_name = " ".join(context.args)
    else:
        target_name = update.message.from_user.first_name

    prompt = (
        f"Give a short, funny, witty Hinglish college-style roast (1-2 lines max, playful and non-abusive) "
        f"directed at {target_name}."
    )
    if target_msg:
        prompt += f" They just said: '{target_msg}'."

    try:
        roast = ask_groq([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], max_tokens=90, temperature=0.95)
        await send_with_typing(update, context, roast)
    except Exception as e:
        print(f"Roast Error: {repr(e)}")
        await update.message.reply_text("Aaj mood achha hai, bach gaya tu 😅")

async def truth_or_dare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = update.message.from_user.first_name or "Yaar"
    choice = random.choice(["TRUTH", "DARE"])
    prompt = (
        f"Give {player} a fun, safe, slightly spicy/funny college group {choice} question or challenge "
        f"in casual Hinglish (1-2 lines max)."
    )
    try:
        tod = ask_groq([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], max_tokens=90, temperature=0.95)
        await send_with_typing(update, context, f"🎲 **{choice} for {player}:**\n{tod}")
    except Exception as e:
        print(f"TOD Error: {repr(e)}")
        await update.message.reply_text("Truth: Apni last search history batao 😂")

# --- Group Welcome & Chat Handler ---
async def welcome_on_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await send_with_typing(update, context, "Hii everyone! 👋 Thanks group mein add karne ke liye, kya chal raha hai yahan?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    chat_type = update.message.chat.type
    text = update.message.text
    user_name = update.message.from_user.first_name or "Friend"
    bot_username = (context.bot.username or "").lower()

    # Private DM: Always reply with typing indicator
    if chat_type == "private":
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        reply = get_ai_reply(chat_id, user_name, text)
        await asyncio.sleep(random.uniform(1.0, 1.8))
        await update.message.reply_text(reply)
        return

    # Group Chat
    is_replied_to_bot = (
        update.message.reply_to_message is not None
        and update.message.reply_to_message.from_user is not None
        and update.message.reply_to_message.from_user.id == context.bot.id
    )
    is_mentioned = (bot_username and f"@{bot_username}" in text.lower()) or "aarohi" in text.lower()
    reply_prob = chat_reply_chance.get(chat_id, 0.30)
    random_chime_in = random.random() < reply_prob

    if is_replied_to_bot or is_mentioned or random_chime_in:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        reply = get_ai_reply(chat_id, user_name, text)
        await asyncio.sleep(random.uniform(1.0, 1.8))
        await update.message.reply_text(reply)

# --- Dummy Web Server for Render 24x7 ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Aarohi Telegram Bot V2 is Live 24x7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mood", set_mood))
    application.add_handler(CommandHandler("roast", roast_user))
    application.add_handler(CommandHandler("tod", truth_or_dare))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_on_add))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot V2 is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
