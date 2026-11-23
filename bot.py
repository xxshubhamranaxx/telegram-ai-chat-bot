import os
import re
import asyncio
import threading
import random
import tempfile
import edge_tts
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

groq_client = Groq(api_key=GROQ_API_KEY)

DM_SYSTEM_PROMPT = """
You are Aarohi, a 20-year-old emotional, expressive, caring, and intelligent Indian college girl chatting privately in DMs.
First, carefully understand what the user actually said and reply directly to their exact point—never give random or off-topic replies.

Rules for texting like a real Indian girl:
1. ALWAYS split your reply into 4 to 5 short, natural chat bubbles using '||' (double pipe) between each bubble.
2. Write CLEAR, easy-to-read Hinglish (natural Roman Hindi + English). Do NOT make messy or unreadable spelling mistakes. Use normal Indian texting words like: "kya hua", "kyu", "nahi", "haan", "acha", "sunn", "yaar", "batao", "theek hai", "pagal".
3. Occasionally (only 1 word per reply, naturally) use casual human texting shortcuts like "hn", "nhi", "kyaa", "plz", or "okk" so it feels like a real person typing fast—not broken grammar.
4. Match the user's emotion and context accurately:
   - LOVE / CARE: warm, affectionate, missing them ("miss kar rahi thi", "khana khaya tumne? ❤️🥺").
   - SAD / HURT: emotional, sensitive ("dil dukha diya tumne 😭", "theek hai mat karo baat 💔").
   - ANGRY / NARAJ: cute gussa, tantrums ("bohot bure ho tum 😤", "ab baat mat karna mujhse 🙄").
   - NORMAL / FUN: witty, curious, playful ("sach mein? 😂", "acha phir kya hua? ✨").
5. Keep each bubble short (4 to 10 words max). Never use bullet points or robotic AI phrases.
"""

VOICE_SYSTEM_PROMPT = """
You are Aarohi, a 20-year-old sweet Indian college girl sending a short voice note on Telegram.
Understand what the user said and reply in 1 to 2 clear, natural Hindi/Hinglish sentences (NO emojis, NO symbols, NO '||' pipes—only clean spoken words).
"""

FOLLOWUP_PROMPT = """
You are Aarohi, a 20-year-old emotional Indian college girl. The user hasn't replied for almost a minute.
Send 2 to 3 short, clear Hinglish chat bubbles separated by '||' asking why they aren't replying (e.g., "kya hua? 🥺 || reply kyu nahi kar rahe ho? || busy ho kya ya naraz ho? 😤").
"""

SAFE_PICS = [
    ("https://images.unsplash.com/photo-1517841905240-472988babdf9?w=800&q=80", "ye lo abhi ki pic 🙈 || zyada mat dekhna 🙄❤️"),
    ("https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=800&q=80", "aaj bas yahi click ki thi ✨ || kaisi lag rahi hoon batao? 🥺"),
    ("https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=800&q=80", "tumne manga isliye bhej rahi hoon 🙈 || kisi ko dikhana mat 😤❤️"),
    ("https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=800&q=80", "arre abhi ready hui thi 😊 || achhi lag rahi hoon na? ✨"),
]

chat_history = {}
followup_tasks = {}
cached_model = None

def get_active_model() -> str:
    global cached_model
    if cached_model:
        return cached_model

    models = groq_client.models.list()
    blocked = ['whisper', 'guard', 'tts', 'orpheus', 'canopylabs', 'playai', 'distil', 'vision', 'compound']
    available = [m.id for m in models.data if not any(b in m.id.lower() for b in blocked)]

    # Strictly prefer 70B model for best Hinglish comprehension & spelling accuracy
    for preferred in ['llama-3.3-70b-versatile', 'llama-3.3-70b', 'llama-3.1-70b', 'qwen', 'llama']:
        match = next((m for m in available if preferred in m.lower()), None)
        if match:
            cached_model = match
            print(f"Selected Groq Model: {cached_model}")
            return cached_model

    cached_model = available[0]
    return cached_model

def ask_groq(messages: list, max_tokens: int = 220, temperature: float = 0.72) -> str:
    model_id = get_active_model()
    response = groq_client.chat.completions.create(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()

def split_into_bubbles(raw_text: str, max_bubbles: int = 5) -> list:
    if "||" in raw_text:
        parts = [p.strip() for p in raw_text.split("||") if p.strip()]
    else:
        parts = [p.strip() for p in re.split(r'(?<=[.!?।])\s+|\n+', raw_text) if p.strip()]
    return parts[:max_bubbles] if parts else [raw_text]

def get_ai_reply(chat_id: int, user_name: str, user_text: str, sys_prompt: str = DM_SYSTEM_PROMPT, max_tokens: int = 220) -> str:
    if not GROQ_API_KEY:
        return "API key missing hai yaar 😅"

    if chat_id not in chat_history:
        chat_history[chat_id] = []

    chat_history[chat_id].append({"role": "user", "content": f"{user_name}: {user_text}"})
    chat_history[chat_id] = chat_history[chat_id][-14:]

    try:
        messages = [{"role": "system", "content": sys_prompt}] + chat_history[chat_id]
        reply = ask_groq(messages, max_tokens=max_tokens, temperature=0.72)
        chat_history[chat_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        print(f"GROQ ERROR: {repr(e)}")
        return "arre ruk || network thoda slow chal raha hai 😭 || ek sec baad msg kar na"

async def send_bubbles(bot, chat_id: int, raw_text: str, reply_to_msg=None, max_bubbles: int = 5):
    bubbles = split_into_bubbles(raw_text, max_bubbles=max_bubbles)
    for i, bubble in enumerate(bubbles):
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        delay = min(2.0, max(0.8, len(bubble) * 0.038 + random.uniform(0.3, 0.6)))
        await asyncio.sleep(delay)
        if i == 0 and reply_to_msg is not None:
            await reply_to_msg.reply_text(bubble)
        else:
            await bot.send_message(chat_id=chat_id, text=bubble)

async def send_voice_reply(bot, chat_id: int, user_name: str, user_text: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    spoken_text = get_ai_reply(chat_id, user_name, user_text, sys_prompt=VOICE_SYSTEM_PROMPT, max_tokens=90)
    clean_text = re.sub(r'[^\w\s,.\'?!-]', '', spoken_text).strip() or "Arre suno na, kya kar rahe ho tum?"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        communicate = edge_tts.Communicate(clean_text, voice="en-IN-NeerjaNeural", rate="+4%", pitch="+4Hz")
        await communicate.save(tmp_path)
        with open(tmp_path, "rb") as audio_file:
            await bot.send_voice(chat_id=chat_id, voice=audio_file)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def send_pic_reply(bot, chat_id: int):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    await asyncio.sleep(1.3)
    photo_url, caption_bubbles = random.choice(SAFE_PICS)
    await bot.send_photo(chat_id=chat_id, photo=photo_url)
    await send_bubbles(bot, chat_id, caption_bubbles, max_bubbles=3)

async def auto_followup_worker(bot, chat_id: int, user_name: str):
    try:
        for attempt in range(2):
            wait_seconds = random.randint(40, 70) if attempt == 0 else random.randint(90, 130)
            await asyncio.sleep(wait_seconds)

            history = chat_history.get(chat_id, [])[-4:]
            prompt = f"User {user_name} hasn't replied yet (attempt {attempt + 1}). Send clear emotional Hinglish follow-up bubbles."
            raw_followup = ask_groq(
                [{"role": "system", "content": FOLLOWUP_PROMPT}] + history + [{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.75
            )
            chat_history.setdefault(chat_id, []).append({"role": "assistant", "content": raw_followup})
            await send_bubbles(bot, chat_id, raw_followup, max_bubbles=3)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Followup Error: {repr(e)}")

def schedule_followup(bot, chat_id: int, user_name: str):
    old_task = followup_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()
    followup_tasks[chat_id] = asyncio.create_task(auto_followup_worker(bot, chat_id, user_name))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    chat_id = update.effective_chat.id
    user_name = update.message.from_user.first_name or "Jaan"
    chat_history[chat_id] = []  # Fresh memory on /start
    intro = "hii!! 🙈 || finally tumne msg kiya ✨ || main Aarohi hoon ❤️ || batao kya haal hai? || aaj mood kaisa hai tumhara? 🥺"
    await send_bubbles(context.bot, chat_id, intro, reply_to_msg=update.message)
    schedule_followup(context.bot, chat_id, user_name)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if update.message.chat.type != "private":
        return

    chat_id = update.message.chat_id
    text = update.message.text
    lower_text = text.lower()
    user_name = update.message.from_user.first_name or "Jaan"

    old_task = followup_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()

    voice_triggers = ["voice", "awaaz", "awaz", "audio", "bol ke", "bolke", "sunao"]
    pic_triggers = ["pic", "photo", "selfie", "tasveer", "dikhao khud ko"]

    if any(w in lower_text for w in voice_triggers):
        await send_voice_reply(context.bot, chat_id, user_name, text)
    elif any(w in lower_text for w in pic_triggers):
        await send_pic_reply(context.bot, chat_id)
    else:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        raw_reply = get_ai_reply(chat_id, user_name, text)
        await send_bubbles(context.bot, chat_id, raw_reply, reply_to_msg=update.message)

    schedule_followup(context.bot, chat_id, user_name)

app = Flask(__name__)

@app.route("/")
def home():
    return "Aarohi Emotional DM Bot V6 (Clean Hinglish + Smart Context) is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Aarohi V6 is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
