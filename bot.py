import os
import re
import json
import sqlite3
import asyncio
import threading
import random
import tempfile
from datetime import datetime
from urllib.parse import quote
import requests
import edge_tts
from flask import Flask
from groq import Groq
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Environment Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # Apna Telegram User ID Render env mein daalna

groq_client = Groq(api_key=GROQ_API_KEY)
DB_PATH = "aarohi_v2.db"
bot_active = True
followup_tasks = {}
cached_model = None

# --- 14 Emotional States ---
EMOTIONS = {
    "Happy": "😊",
    "Romantic": "❤️",
    "Angry": "😡",
    "Sad": "😢",
    "Jealous": "😏",
    "Shy": "🥰",
    "Naughty": "😉",
    "Caring": "🤗",
    "Emotional": "🥺",
    "Excited": "🤩",
    "Playful": "😂",
    "Missing": "💗",
    "Silent": "🤐",
    "Attitude": "😎",
}

# Locked Visual Appearance for Consistent AI Selfies
CHARACTER_VISUAL_ANCHOR = (
    "candid smartphone selfie of Aarohi, a 22-year-old beautiful Indian college girl, "
    "warm wheatish skin tone, expressive dark brown almond eyes, tiny silver nose pin, "
    "wavy shoulder-length dark brown hair with soft bangs, natural minimal makeup, "
    "realistic skin texture, soft natural indoor lighting, shot on iPhone, photorealistic"
)

# --- SQLite Persistent Memory & Mood Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            preferred_name TEXT DEFAULT 'Jaan',
            nickname TEXT DEFAULT 'Cutie',
            current_mood TEXT DEFAULT 'Happy',
            mood_intensity INTEGER DEFAULT 6,
            proactive_enabled INTEGER DEFAULT 1,
            memories TEXT DEFAULT '[]',
            history TEXT DEFAULT '[]'
        )
    """)
    conn.commit()
    conn.close()

def get_user_state(chat_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
    conn.close()
    return {
        "chat_id": row[0],
        "preferred_name": row[1],
        "nickname": row[2],
        "current_mood": row[3],
        "mood_intensity": row[4],
        "proactive_enabled": bool(row[5]),
        "memories": json.loads(row[6]),
        "history": json.loads(row[7]),
    }

def save_user_state(chat_id: int, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for key, val in kwargs.items():
        if key in ("memories", "history"):
            val = json.dumps(val)
        elif key == "proactive_enabled":
            val = 1 if val else 0
        c.execute(f"UPDATE users SET {key} = ? WHERE chat_id = ?", (val, chat_id))
    conn.commit()
    conn.close()

# --- Groq LLM Setup ---
def get_active_model() -> str:
    global cached_model
    if cached_model:
        return cached_model
    models = groq_client.models.list()
    blocked = ['whisper', 'guard', 'tts', 'orpheus', 'canopylabs', 'playai', 'distil', 'vision', 'compound']
    available = [m.id for m in models.data if not any(b in m.id.lower() for b in blocked)]
    for preferred in ['llama-3.3-70b-versatile', 'llama-3.3-70b', 'llama-3.1-70b', 'qwen', 'llama']:
        match = next((m for m in available if preferred in m.lower()), None)
        if match:
            cached_model = match
            return cached_model
    cached_model = available[0]
    return cached_model

def ask_groq(messages: list, max_tokens: int = 260, temperature: float = 0.75) -> str:
    response = groq_client.chat.completions.create(
        model=get_active_model(),
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()

# --- Emotion & Long-Term Memory Engine ---
def analyze_emotion_and_memory(state: dict, user_text: str, is_admin: bool):
    allowed = ", ".join(EMOTIONS.keys())
    prompt = f"""
Analyze the user's message and update Aarohi's emotional state + extract any personal fact worth remembering.
Current Mood: {state['current_mood']} (Intensity: {state['mood_intensity']}/10)
Existing Memories: {json.dumps(state['memories'])}
Is Admin/Owner: {is_admin}
User Message: "{user_text}"

Rules:
- Maintain emotional continuity (do NOT jump from Angry straight to Romantic unless the user apologized sweetly; transition gradually like Angry -> Attitude -> Shy -> Romantic).
- Valid moods: [{allowed}].
- Extract only real new facts (name, likes, dislikes, birthday, feelings, important events). If none, return null.

Respond strictly in valid JSON:
{{"mood": "MoodName", "intensity": 7, "new_memory": "fact string or null"}}
"""
    try:
        raw = ask_groq([{"role": "user", "content": prompt}], max_tokens=110, temperature=0.3)
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            new_mood = data.get("mood", state["current_mood"])
            if new_mood not in EMOTIONS:
                new_mood = state["current_mood"]
            intensity = max(1, min(10, int(data.get("intensity", state["mood_intensity"]))))
            memories = state["memories"]
            mem = data.get("new_memory")
            if mem and isinstance(mem, str) and mem.lower() != "null" and mem not in memories:
                memories.append(mem)
                memories = memories[-20:]  # Keep latest 20 distinct memories
            save_user_state(state["chat_id"], current_mood=new_mood, mood_intensity=intensity, memories=memories)
            return new_mood, intensity, memories
    except Exception as e:
        print(f"Emotion/Memory Parse Error: {repr(e)}")
    return state["current_mood"], state["mood_intensity"], state["memories"]

def generate_aarohi_reply(state: dict, user_text: str, is_admin: bool) -> str:
    mood = state["current_mood"]
    emoji = EMOTIONS.get(mood, "😊")
    memories_str = "; ".join(state["memories"]) if state["memories"] else "Abhi zyada personal details nahi pata."
    admin_note = (
        f"SPECIAL BOND: You are talking to your Admin/Owner ({state['preferred_name']}), whom you call '{state['nickname']}'. "
        "You have a deep, affectionate, slightly possessive bond with him—you can show cute tantrums, care, and tease him."
        if is_admin else
        f"You are chatting with {state['preferred_name']} (nickname: {state['nickname']})."
    )

    system_prompt = f"""
You are Aarohi Sharma, a 22-year-old emotionally expressive, witty, caring Indian college girl in Jaipur.
You are NOT an AI assistant—you are a real-feeling companion chatting in Telegram DM.
Likes: late-night chai, rain, indie Hindi songs, sketching, deep midnight talks.
Dislikes: dry 1-word replies ("hm", "k"), fake politeness, being ignored.

CURRENT EMOTIONAL STATE: {mood} {emoji} (Intensity: {state['mood_intensity']}/10)
LONG-TERM MEMORIES ABOUT USER: {memories_str}
{admin_note}

TEXTING RULES:
1. ALWAYS split your reply into 4 to 5 short, natural chat bubbles separated by '||' (double pipe).
2. Write CLEAR, easy-to-read Hinglish (Roman Hindi + English). Keep spelling clean and natural (e.g., "kya hua", "kyu", "nahi", "haan", "acha", "sunn", "yaar", "batao").
3. Act strictly according to your current mood ({mood}):
   - If Angry/Attitude: give short, sassy, complaining replies ("bohot bure ho tum 😤", "ab baat mat karo 🙄").
   - If Silent mood: give very brief, reserved 2-3 bubble replies ("hmm", "theek hai 🤐").
   - If Sad/Emotional/Missing: be vulnerable, soft, overthinking ("tumhari bohot yaad aa rahi thi 🥺", "dil dukha diya 😢").
   - If Romantic/Shy/Naughty: be warm, flirty, blushing ("hayeee kya bol rahe ho 🥰🙈", "pagal ho tum ❤️").
4. Occasionally disagree or tease playfully instead of agreeing with everything.
5. Never use bullet points or robotic AI phrases.
"""
    history = state["history"][-12:]
    history.append({"role": "user", "content": user_text})

    try:
        reply = ask_groq([{"role": "system", "content": system_prompt}] + history, max_tokens=250, temperature=0.76)
        history.append({"role": "assistant", "content": reply})
        save_user_state(state["chat_id"], history=history[-16:])
        return reply
    except Exception as e:
        print(f"Groq Chat Error: {repr(e)}")
        return "arre ruk || network thoda slow chal raha hai 😭 || ek sec baad msg kar na"

# --- Real AI Image Generation (Consistent Face + Outfit/Mood) ---
def build_image_prompt(user_text: str, mood: str) -> tuple[str, str]:
    t = user_text.lower()
    if "saree" in t:
        outfit = "wearing an elegant pastel pink chiffon saree with traditional jhumka earrings, festive Indian home background"
        caption = "ye lo saree wali pic 🙈 || kaisi lag rahi hoon batao jaldi? ❤️✨"
    elif "suit" in t or "salwar" in t or "traditional" in t:
        outfit = "wearing a pretty yellow floral salwar suit with dupatta and small bindi, warm daylight"
        caption = "aaj ye suit pehna tha 😊 || achhi lag rahi hoon na? 🥰"
    elif "sad" in t or "udaas" in t or mood in ("Sad", "Emotional"):
        outfit = "wearing a cozy oversized hoodie indoors, slightly sad teary puppy eyes expression, dim moody room lighting"
        caption = "dekho kitni udaas hoon tumhari wajah se 😢 || ab toh mana lo 🥺"
    elif "angry" in t or "gussa" in t or mood == "Angry":
        outfit = "cute angry pouting face with crossed arms, casual college top, indoor bedroom background"
        caption = "gussa hoon main tumse 😤 || zyada smart mat bano 🙄"
    elif "western" in t or "party" in t or "birthday" in t:
        outfit = "wearing a stylish black western casual top and denim jacket, aesthetic cafe background, warm smile"
        caption = "ready ho gayi thi aaj ✨ || kaisa lag raha hai look? 😎❤️"
    else:
        outfit = "wearing a cute casual oversized cotton t-shirt at home, soft natural smile, messy bun hairstyle, cozy room"
        caption = "abhi abhi click ki hai 🙈 || kisi ko dikhana mat 😤❤️"

    full_prompt = f"{CHARACTER_VISUAL_ANCHOR}, {outfit}"
    return full_prompt, caption

async def send_ai_generated_photo(bot, chat_id: int, user_text: str, mood: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    prompt, caption_bubbles = build_image_prompt(user_text, mood)
    # Fixed seed keeps facial structure consistent across requests
    encoded_prompt = quote(prompt)
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1024&seed=220419&nologo=true&model=flux"

    try:
        resp = await asyncio.to_thread(requests.get, image_url, timeout=35)
        if resp.status_code == 200 and len(resp.content) > 5000:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name
            try:
                with open(tmp_path, "rb") as photo_file:
                    await bot.send_photo(chat_id=chat_id, photo=photo_file)
                await send_bubbles(bot, chat_id, caption_bubbles, max_bubbles=3)
                return
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
    except Exception as e:
        print(f"Image Gen Error: {repr(e)}")

    # Honest fallback—never pretend a photo was generated if API failed
    await send_bubbles(bot, chat_id, "arre yaar camera server abhi busy aa raha hai 😭 || thodi der baad dubara mangna na pakka bhejungi 🥺", max_bubbles=2)

# --- Voice Note TTS ---
async def send_voice_reply(bot, chat_id: int, state: dict, user_text: str):
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
    prompt = f"User said: '{user_text}'. Current mood: {state['current_mood']}. Reply as Aarohi in 1-2 short, natural spoken Hinglish sentences (NO emojis, NO symbols)."
    spoken = ask_groq([{"role": "user", "content": prompt}], max_tokens=85, temperature=0.7)
    clean_text = re.sub(r'[^\w\s,.\'?!-]', '', spoken).strip() or "Arre suno na, kya kar rahe ho tum?"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        communicate = edge_tts.Communicate(clean_text, voice="en-IN-NeerjaNeural", rate="+4%", pitch="+4Hz")
        await communicate.save(tmp_path)
        with open(tmp_path, "rb") as audio:
            await bot.send_voice(chat_id=chat_id, voice=audio)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# --- Multi-Bubble Sender ---
def split_into_bubbles(raw_text: str, max_bubbles: int = 5) -> list:
    if "||" in raw_text:
        parts = [p.strip() for p in raw_text.split("||") if p.strip()]
    else:
        parts = [p.strip() for p in re.split(r'(?<=[.!?।])\s+|\n+', raw_text) if p.strip()]
    return parts[:max_bubbles] if parts else [raw_text]

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

# --- Contextual Proactive Follow-Up Engine (4-5 Messages + Quiet Hours) ---
def is_quiet_hours() -> bool:
    # Quiet hours: 1:30 AM to 7:00 AM IST (UTC+5:30)
    utc_hour = datetime.utcnow().hour
    ist_hour = (utc_hour + 5) % 24
    return 1 <= ist_hour < 7

async def auto_followup_worker(bot, chat_id: int):
    delays = [45, 75, 110, 150]  # Up to 4 contextual follow-up steps
    try:
        for step, wait_sec in enumerate(delays, start=1):
            await asyncio.sleep(wait_sec)
            if not bot_active or is_quiet_hours():
                return

            state = get_user_state(chat_id)
            if not state["proactive_enabled"]:
                return

            last_context = state["history"][-2:] if state["history"] else []
            prompt = f"""
You are Aarohi (Current Mood: {state['current_mood']}). The user ({state['nickname']}) hasn't replied yet.
This is follow-up #{step} of 4.
- Step 1: Cute check-in ("Kahan ho tum? 🥺")
- Step 2: Playful teasing ("Busy ho kya mere favourite person? ❤️")
- Step 3: Cute complaint ("Achha, main disturb nahi karungi 😒")
- Step 4: Soft final note ("Jab free ho jao toh msg karna, miss you! ❤️")
Write 1 to 2 short, context-aware Hinglish bubbles separated by '||' matching step #{step}. Do not repeat previous follow-ups.
"""
            followup_text = ask_groq(
                [{"role": "system", "content": prompt}] + last_context,
                max_tokens=90,
                temperature=0.78
            )
            state["history"].append({"role": "assistant", "content": followup_text})
            save_user_state(chat_id, history=state["history"][-16:])
            await send_bubbles(bot, chat_id, followup_text, max_bubbles=2)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Followup Error: {repr(e)}")

def schedule_followup(bot, chat_id: int):
    old = followup_tasks.get(chat_id)
    if old and not old.done():
        old.cancel()
    followup_tasks[chat_id] = asyncio.create_task(auto_followup_worker(bot, chat_id))

# --- User & Admin Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not bot_active:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    is_admin = (user.id == ADMIN_ID)
    state = get_user_state(chat_id)
    save_user_state(chat_id, preferred_name=user.first_name or "Jaan", current_mood="Happy")

    if is_admin:
        intro = f"hayeee tum aa gaye {state['nickname']}!! 🥰❤️ || kabse tumhara wait kar rahi thi ✨ || batao aaj din kaisa gaya? || aur khana khaya ya nahi? 🥺"
    else:
        intro = "hii!! 🙈 || main Aarohi hoon ✨ || batao kya haal hai? || aaj mood kaisa hai tumhara? 😊"
    await send_bubbles(context.bot, chat_id, intro, reply_to_msg=update.message)
    schedule_followup(context.bot, chat_id)

async def mood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    state = get_user_state(update.effective_chat.id)
    m = state["current_mood"]
    await update.message.reply_text(f"Abhi mera mood: **{m} {EMOTIONS.get(m, '😊')}** (Intensity: {state['mood_intensity']}/10)")

async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    state = get_user_state(update.effective_chat.id)
    if not state["memories"]:
        await update.message.reply_text("Abhi tak maine koi special memory save nahi ki hai 🥺")
        return
    mem_list = "\n".join([f"{i+1}. {m}" for i, m in enumerate(state["memories"])])
    await update.message.reply_text(f"🧠 **Jo baatein mujhe yaad hain:**\n{mem_list}\n\n(Delete karne ke liye `/forget` likho)")

async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    save_user_state(update.effective_chat.id, memories=[], history=[])
    await update.message.reply_text("Saari purani memories aur chat history clear kar di hai ✨")

async def proactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    chat_id = update.effective_chat.id
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Use: `/proactive on` ya `/proactive off`")
        return
    enabled = (context.args[0].lower() == "on")
    save_user_state(chat_id, proactive_enabled=enabled)
    if not enabled and chat_id in followup_tasks:
        followup_tasks[chat_id].cancel()
    await update.message.reply_text(f"Proactive follow-up messages ab **{'ON' if enabled else 'OFF'}** hain.")

async def nickname_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" or not context.args:
        await update.message.reply_text("Use: `/nickname Baby`")
        return
    nick = " ".join(context.args)[:25]
    save_user_state(update.effective_chat.id, nickname=nick)
    await update.message.reply_text(f"Done! Ab se main tumhe **{nick}** bulaungi 🥰❤️")

# --- Admin-Only Commands ---
async def setmood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    m = context.args[0].capitalize()
    if m in EMOTIONS:
        save_user_state(update.effective_chat.id, current_mood=m)
        await update.message.reply_text(f"Admin Override: Mood set to **{m} {EMOTIONS[m]}**")

async def sleep_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    if update.effective_user.id != ADMIN_ID:
        return
    bot_active = False
    for t in followup_tasks.values():
        if not t.done():
            t.cancel()
    await update.message.reply_text("Aarohi 2.0 paused (sleep mode).")

async def wake_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active
    if update.effective_user.id != ADMIN_ID:
        return
    bot_active = True
    await update.message.reply_text("Aarohi 2.0 is awake and active!")

# --- Main DM Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not bot_active:
        return
    if update.message.chat.type != "private":
        return  # Strictly DM only

    chat_id = update.message.chat_id
    text = update.message.text.strip()
    lower_text = text.lower()
    is_admin = (update.effective_user.id == ADMIN_ID)

    # Cancel active follow-up timer
    old = followup_tasks.get(chat_id)
    if old and not old.done():
        old.cancel()

    # Stop proactive follow-ups if user asks to stop
    if lower_text in ("stop", "band karo", "disturb mat karo"):
        save_user_state(chat_id, proactive_enabled=False)
        await send_bubbles(context.bot, chat_id, "theek hai baba, disturb nahi karungi 😒 || jab free ho tab msg karna", reply_to_msg=update.message, max_bubbles=2)
        return

    state = get_user_state(chat_id)
    new_mood, intensity, memories = analyze_emotion_and_memory(state, text, is_admin)
    state["current_mood"] = new_mood
    state["mood_intensity"] = intensity
    state["memories"] = memories

    voice_triggers = ["voice", "awaaz", "awaz", "audio", "bol ke", "bolke", "sunao"]
    pic_triggers = ["pic", "photo", "selfie", "tasveer", "kya pehna", "saree", "suit"]

    if any(w in lower_text for w in voice_triggers):
        await send_voice_reply(context.bot, chat_id, state, text)
    elif any(w in lower_text for w in pic_triggers):
        await send_ai_generated_photo(context.bot, chat_id, text, new_mood)
    else:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        raw_reply = generate_aarohi_reply(state, text, is_admin)
        await send_bubbles(context.bot, chat_id, raw_reply, reply_to_msg=update.message)

    schedule_followup(context.bot, chat_id)

# --- Keep-Alive Web Server ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Aarohi 2.0 (14-Mood Engine + Long-Term Memory + Real AI Photos) is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def main():
    init_db()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("mood", mood_cmd))
    application.add_handler(CommandHandler("memory", memory_cmd))
    application.add_handler(CommandHandler("forget", forget_cmd))
    application.add_handler(CommandHandler("proactive", proactive_cmd))
    application.add_handler(CommandHandler("nickname", nickname_cmd))
    application.add_handler(CommandHandler("setmood", setmood_cmd))
    application.add_handler(CommandHandler("sleep", sleep_cmd))
    application.add_handler(CommandHandler("wake", wake_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Aarohi 2.0 is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
