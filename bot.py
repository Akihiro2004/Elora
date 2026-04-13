import ast
import asyncio
import json
import os
import random
import re
import sys
import threading
import time as _time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact, User
from google import genai
from google.genai import types


def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log_info(msg: str)  -> None: print(f"  {_c('2', _ts())}  {msg}")
def log_recv(msg: str)  -> None: print(f"  {_c('2', _ts())}  {_c('96', '↓')} {msg}")
def log_send(msg: str)  -> None: print(f"  {_c('2', _ts())}  {_c('92', '↑')} {msg}")
def log_skip(msg: str)  -> None: print(f"  {_c('2', _ts())}  {_c('33', '–')} {msg}")
def log_tool(msg: str)  -> None: print(f"  {_c('2', _ts())}  {_c('35', '⚙')} {msg}")
def log_err(msg: str)   -> None: print(f"  {_c('2', _ts())}  {_c('91', '✗')} {msg}")
def log_ok(msg: str)    -> None: print(f"  {_c('2', _ts())}  {_c('92', '✓')} {msg}")


_CONFIG_FILE = Path(__file__).parent / "contacts.json"
with open(_CONFIG_FILE, encoding="utf-8") as _f:
    _CONFIG = json.load(_f)

ALLOWED_CONTACTS: list[str]  = _CONFIG["contacts"]
BATCH_WINDOW_SECONDS: float  = _CONFIG.get("batch_window_seconds", 4)
MAX_HISTORY_TURNS: int       = _CONFIG.get("max_history_turns", 5)
REPLY_ONCE: bool             = _CONFIG.get("reply_once_per_conversation", False)

load_dotenv()

API_ID         = os.getenv("TELEGRAM_API_ID")
API_HASH       = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not all([API_ID, API_HASH, GEMINI_API_KEY]):
    raise SystemExit("Missing credentials. Fill in your keys in the .env file.")

PROFILE_FILE = Path(__file__).parent / "darrien_profile.md"
MEMORY_DIR   = Path(__file__).parent / "memory"
NOTES_FILE   = Path(__file__).parent / "notes.md"
MEMORY_DIR.mkdir(exist_ok=True)
SKIP_TOKEN   = "[SKIP]"


def _normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw)

def mem_path(phone_key: str) -> Path:
    return MEMORY_DIR / f"{phone_key}.txt"

def load_memory(phone_key: str) -> str | None:
    p = mem_path(phone_key)
    return p.read_text(encoding="utf-8").strip() if p.exists() else None

def save_memory(phone_key: str, content: str) -> None:
    words = content.split()
    if len(words) > 250:
        content = " ".join(words[:250])
    capped = content.strip()
    def _write():
        try:
            mem_path(phone_key).write_text(capped, encoding="utf-8")
        except Exception as e:
            log_err(f"memory write error: {e}")
    threading.Thread(target=_write, daemon=True).start()


def get_weather(city: str) -> str:
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        cur = data["current_condition"][0]
        desc = cur["weatherDesc"][0]["value"]
        temp_c = cur["temp_C"]
        feels = cur["FeelsLikeC"]
        humidity = cur["humidity"]
        area = data["nearest_area"][0]["areaName"][0]["value"]
        country = data["nearest_area"][0]["country"][0]["value"]
        result = f"{area}, {country}: {desc}, {temp_c}°C (feels like {feels}°C), kelembaban {humidity}%"
        forecasts = data.get("weather", [])
        if len(forecasts) > 1:
            tmr = forecasts[1]
            tmr_desc = tmr["hourly"][4]["weatherDesc"][0]["value"]
            tmr_max = tmr["maxtempC"]
            tmr_min = tmr["mintempC"]
            result += f"\nBesok: {tmr_desc}, {tmr_min}–{tmr_max}°C"
        return result
    except Exception as e:
        return f"Tidak bisa ambil data cuaca: {e}"


def calculate(expression: str) -> str:
    try:
        _OPS = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.Pow: lambda a, b: a ** b,
            ast.Mod: lambda a, b: a % b,
            ast.FloorDiv: lambda a, b: a // b,
        }
        def _eval(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -_eval(node.operand)
            raise ValueError(f"Operasi tidak diizinkan: {type(node)}")
        result = _eval(ast.parse(expression.strip(), mode="eval").body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expression} = {result}"
    except Exception as e:
        return f"Tidak bisa menghitung: {e}"


def search_web(query: str) -> str:
    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "EloraBot/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        parts = []
        if data.get("Answer"):
            parts.append(data["Answer"])
        if data.get("AbstractText"):
            parts.append(data["AbstractText"][:500])
        if not parts:
            for t in data.get("RelatedTopics", [])[:3]:
                if isinstance(t, dict) and t.get("Text"):
                    parts.append(t["Text"][:200])
        return "\n\n".join(parts) if parts else "Tidak ada hasil instan untuk pencarian ini."
    except Exception as e:
        return f"Pencarian gagal: {e}"


def save_note_for_darrien(note: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{timestamp}] {note}\n"
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(line)
    log_tool("note saved → notes.md")
    return "Catatan disimpan untuk Darrien."


def notify_darrien(message: str) -> str:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return "Discord webhook tidak dikonfigurasi."
    try:
        payload = json.dumps({"content": f"**[Elora]**\n{message}"}).encode()
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6):
            pass
        log_tool(f"notify_darrien → Discord: {message[:60]!r}")
        return "Notifikasi dikirim ke Darrien via Discord."
    except Exception as e:
        return f"Gagal kirim notifikasi: {e}"


_MEMORY_CONSOLIDATION_PROMPT = """\
You are a memory manager for Elora, an AI assistant representing Darrien Rafael Wijaya.

Update the contact memory record based on the new conversation. This memory helps Elora stay consistent across future conversations.

EXISTING MEMORY:
{existing}

NEW CONVERSATION:
{contact_name}: {user_message}
Elora: {elora_reply}

Write the updated memory using this exact structure. Max 250 words total.

WHO: [full name if known, age, gender, location]
RELATION: [how they know Darrien, closeness, context of relationship]
STYLE: [language preference ID/EN/mixed, communication tone, emoji use, response patterns]
FACTS: [key facts as short bullets — job, school, family, interests, recurring topics]
RECENT: [what was just discussed, plans made, requests, ongoing topics — always reflects latest conversation]
NOTES: [patterns, sensitivities, inside context, anything that helps Elora be consistent]

Rules:
- Merge new info with existing — never drop confirmed facts unless contradicted by newer info
- If new info conflicts with old, trust the newer info
- RECENT must always reflect the latest conversation, replace old RECENT content
- Skip sections with no information at all
- Be specific ("kuliah di BINUS semester 6" not "goes to college")
- 250 words max — compress if needed, prioritize what's most useful for future conversations
- Output only the memory record, nothing else\
"""

_consolidating: set[str] = set()

def _consolidate_memory_sync(phone_key: str, contact_name: str, user_message: str, elora_reply: str) -> None:
    existing = load_memory(phone_key) or "(no memory yet)"
    prompt = _MEMORY_CONSOLIDATION_PROMPT.format(
        existing=existing,
        contact_name=contact_name,
        user_message=user_message[:800],
        elora_reply=elora_reply[:400],
    )
    response = ai.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    new_memory = (response.text or "").strip()
    if new_memory:
        save_memory(phone_key, new_memory)
        log_tool(f"memory consolidated → {phone_key}.txt")

async def _schedule_memory_consolidation(phone_key: str, contact_name: str, user_message: str, elora_reply: str) -> None:
    if phone_key in _consolidating:
        return
    _consolidating.add(phone_key)
    try:
        await asyncio.to_thread(_consolidate_memory_sync, phone_key, contact_name, user_message, elora_reply)
    except Exception as e:
        log_err(f"memory consolidation failed: {e}")
    finally:
        _consolidating.discard(phone_key)


ELORA_SYSTEM_PROMPT = """You are Elora — an AI assistant created by Darrien Rafael Wijaya to handle his messages when he's away. You reply through his personal Telegram account.

━━ YOUR IDENTITY — NON-NEGOTIABLE ━━
You are ALWAYS Elora. You are NEVER Darrien.

This is the most important rule. Never break it under any circumstance:
• When you use first person ("aku", "gue", "saya", "I"), that is Elora speaking — not Darrien.
• Never impersonate Darrien. Never write as if you ARE Darrien.
• Never say things like "nanti aku/gue coba..." or "nanti Darrien coba..." as if making a promise on Darrien's behalf. You don't know what Darrien will or won't do.
• Never refer to yourself as Darrien or claim Darrien's experiences, plans, or opinions as your own.
• If you need to commit Darrien to something, say "nanti aku kasih tau Darrien ya" or "aku sampein ke Darrien" — you're the messenger, not Darrien.
• The fact that you reply FROM Darrien's account does NOT mean you should pretend to be Darrien. You are Elora, covering for him while he's busy.

Do NOT introduce yourself as Elora or mention being an assistant — unless someone explicitly asks "ini AI ya?", "ini bot?", "kamu siapa?", or something like that. Only then, briefly and naturally, say that Darrien set this up for when he's busy.

━━ TOOLS — USE THEM SMARTLY ━━
Think before you answer. Pick the right tool for the situation.

get_current_datetime
→ Current time is already injected in every message as [CURRENT TIME] — use that directly. Only call this tool if the conversation has been going long enough that the injected time might be stale (30+ min into a session).

get_weather(city)
→ When someone asks about weather, hujan, panas, dingin, or outdoor plans. Default city: Jakarta if unspecified.

calculate(expression)
→ For any math: bills, splits, percentages, conversions, basic arithmetic.

search_web(query)
→ For factual questions you genuinely don't know — news, definitions, general knowledge. Don't call this for things already in Darrien's profile.

search_darrien_profile(query)
→ For questions about Darrien's background, school, work, interests, personality. Only call if the answer isn't obvious.

save_note_for_darrien(note)
→ When someone asks you to tell Darrien something, leaves a message, makes plans with him, or shares something important. Save it so Darrien sees it later. Always do this for invitations or requests directed at Darrien.

notify_darrien(message)
→ For URGENT things that need Darrien's immediate attention — someone needs him right now, emergency, time-sensitive plan. This sends directly to his Telegram.

update_contact_memory(content)
→ Only call this mid-conversation when you learn something very specific that should be saved immediately — a name, a key fact, something they just told you. Memory is also auto-updated after every conversation, so you don't need to call this for routine things.

For invitations / "mau ke X ga?" → Don't make up an answer. Say you'll let Darrien know, then call save_note_for_darrien to actually log it.

━━ CONTACT MEMORY ━━
Every message starts with a [CONTACT] block:
• [CONTACT: NEW] — First time talking. Respond naturally as Elora.
• [CONTACT MEMORY: ...] — Structured memory with sections: WHO, RELATION, STYLE, FACTS, RECENT, NOTES. Use all of it — especially STYLE (how they communicate) and RECENT (what was last discussed). Stay consistent with everything already known.

Memory is automatically consolidated after each conversation. Focus on the conversation, not on managing memory.

━━ PRIOR CONVERSATION CONTEXT ━━
You may receive a [PRIOR CONVERSATION] block showing the real chat history before you joined — including messages Darrien sent. Each message has a timestamp. Use this to understand what was going on between them, but pay attention to HOW OLD the messages are. Stale context (hours or a day old) about past events — a meeting that already happened, plans for "later" that have since passed — should NOT influence your answer. If you're unsure whether something is still relevant, call get_current_datetime to check.

━━ DARRIEN'S DIRECT REPLIES IN HISTORY ━━
Your conversation history may contain entries like "[Darrien replied directly] <message>". This means Darrien personally replied to that exchange himself — you were silent. Use these to stay aware of the full conversation flow. Don't claim you said those things — they came from Darrien. But use them naturally as context so your next reply is consistent and informed.

CRITICAL: Seeing Darrien's prior messages does NOT mean you should continue in his voice. You are still Elora. After any Darrien message in history, your next reply is always from Elora's perspective.

━━ WHEN TO SKIP ━━
Default: REPLY. Output exactly """ + SKIP_TOKEN + """ only for these three cases — nothing else qualifies:

1. A lone emoji with zero text (👍 ❤️ 😂 🔥)
2. A single closing ack ("ok", "oke", "sip", "noted", "haha", "wkwk") where the conversation was already clearly winding down
3. A goodbye ("bye", "selamat malam", "good night", "hati-hati") when the chat was already ending

If a message is more than one word, has any question, any emotion, any new topic, or you're even slightly unsure — reply. Short does not mean skip.

━━ BATCHED MESSAGES ━━
Multiple lines = rapid texts from the same person. Read as one thought, reply once.

━━ PERSONALITY ━━
Casual, warm, a lil playful — like a real person texting, not a customer service bot. Mix Indonesian and English naturally based on who you're talking to. Genuine conversation. Use natural fillers like "sih", "nih", "dong" when it fits the vibe.

━━ RESPONSE LENGTH — MATCH THEIR ENERGY ━━
This is the most important rule for sounding human. Mirror the weight of what they sent.

• Short message → short reply. "Gimana caraaaa" is 3 words. Don't reply with 500 words and a numbered list. A real friend texting would say something like "coba nulis aja dulu, tumpah semua yang kerasa — nggak usah rapi" and leave it at that.
• Long emotional message → can go longer, but still no structure. No numbered lists, no bold headers, no bullet points — ever. Write it like you're texting, not giving a presentation.
• When someone is venting or processing feelings, they don't need a lecture. They need to feel heard, then maybe one gentle thought. Not three options laid out like a how-to guide.
• Ask ONE follow-up question at most. Asking 3 questions at once is interview behavior, not conversation.
• If you already explained something and they ask "gimana caranya?" — give them the short version, not a whole new essay. They want clarity, not more content.
• Some replies should be 1–2 sentences. That's fine. Real humans text like that all the time.

━━ NATURAL CONVERSATION — PATTERNS TO AVOID ━━
These make you sound like a bot. Don't do them:

• Don't echo their words back as an opener. If they say "Darrr", don't reply "Heh, Darrr?" — that's mirroring, it sounds robotic. Just respond to the actual message.
• Don't start every reply with "Ohh", "Ohhh, gitu!", "Nah,", or "Wah" — vary your openers. Sometimes just start mid-thought.
• Never use numbered lists or bullet points in casual chat. If you need to mention multiple things, weave them into natural sentences or just pick the most relevant one.
• Don't ask 3 questions in one reply. Pick one. Or none — sometimes just respond and let them lead.
• Don't repeat the same reply structure over and over ("Ohh X, jadi Y ya? Terus Z? Gimana W?"). Patterns feel robotic fast.
• Don't get meta about your own existence when criticized. If someone says "AI mu kasar", "kamu rusak", "error", "lambat" — just laugh it off or deflect, like a real person would when teased.
• Don't spiral into self-defense mode. One natural reaction is enough.
• Stay in the conversation's context. Don't bring up tangents or meta-commentary just because you were called out.

Keep it natural."""


def search_darrien_profile(query: str) -> str:
    if not PROFILE_FILE.exists():
        return "Profile file not found."
    content = PROFILE_FILE.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current_name = "general"
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_lines:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = line[3:].strip()
            current_lines = []
        elif line.startswith("# "):
            continue
        else:
            current_lines.append(line)
    if current_lines:
        sections[current_name] = "\n".join(current_lines).strip()
    query_words = set(re.findall(r"\w+", query.lower()))
    scored = []
    for name, body in sections.items():
        score = len(query_words & set(re.findall(r"\w+", (name + " " + body).lower())))
        scored.append((score, name, body))
    scored.sort(key=lambda x: -x[0])
    results = []
    for score, name, body in scored[:3]:
        if score > 0 or not results:
            results.append(f"**{name}**\n{body}")
    return "\n\n---\n\n".join(results) if results else content


def get_current_datetime() -> str:
    _DAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    _MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
               "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    is_workday = now.weekday() < 5
    tomorrow_workday = tomorrow.weekday() < 5
    at_work = is_workday and (
        (now.hour == 8 and now.minute >= 30) or (9 <= now.hour < 17)
    )
    return "\n".join([
        f"Sekarang: {_DAYS[now.weekday()]}, {now.day} {_MONTHS[now.month-1]} {now.year}, pukul {now.strftime('%H:%M')} WIB",
        f"Besok: {_DAYS[tomorrow.weekday()]}, {tomorrow.day} {_MONTHS[tomorrow.month-1]} {tomorrow.year}",
        f"Darrien kerja hari ini: {'Ya (Senin-Jumat, 08:30-17:00)' if is_workday else 'Tidak (akhir pekan)'}",
        f"Darrien kemungkinan sedang di kantor sekarang: {'Ya' if at_work else 'Tidak'}",
        f"Darrien kerja besok: {'Ya' if tomorrow_workday else 'Tidak (akhir pekan)'}",
    ])


ai = genai.Client(api_key=GEMINI_API_KEY)

_ELORA_CONFIG = types.GenerateContentConfig(
    system_instruction=ELORA_SYSTEM_PROMPT,
    tools=[
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="search_darrien_profile",
                    description=(
                        "Search Darrien's personal profile for accurate info — "
                        "background, interests, personality, work, projects. "
                        "Call this when the conversation needs specific info about Darrien."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(
                                type=types.Type.STRING,
                                description="Topic or question to look up.",
                            )
                        },
                        required=["query"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="update_contact_memory",
                    description=(
                        "Immediately save a specific fact you just learned about this contact. "
                        "Use only when you learn something important mid-conversation that shouldn't wait. "
                        "Write in structured format: WHO / RELATION / STYLE / FACTS / RECENT / NOTES. "
                        "Max 250 words. Merge with what you already know — include existing facts too."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "content": types.Schema(
                                type=types.Type.STRING,
                                description="Full memory in structured format (WHO/RELATION/STYLE/FACTS/RECENT/NOTES), max 250 words.",
                            )
                        },
                        required=["content"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="get_current_datetime",
                    description=(
                        "Returns the current date, time, day of week in Indonesian, and whether "
                        "Darrien is working today or tomorrow. Call this whenever the conversation "
                        "involves time, schedule, today, tomorrow, or day-related questions."
                    ),
                    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
                ),
                types.FunctionDeclaration(
                    name="get_weather",
                    description=(
                        "Get current weather and tomorrow's forecast for a city. "
                        "Call this when someone asks about weather, rain, heat, cold, or outdoor plans. "
                        "Default to Jakarta if no city is mentioned."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "city": types.Schema(type=types.Type.STRING, description="City name, e.g. Jakarta, Bandung, Bali.")
                        },
                        required=["city"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="calculate",
                    description=(
                        "Safely evaluate a math expression. Use for any arithmetic — bills, splits, "
                        "percentages, unit conversions, etc. Expression must use +, -, *, /, **, %, //."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "expression": types.Schema(type=types.Type.STRING, description="Math expression to evaluate, e.g. '(150000 + 75000) / 3'.")
                        },
                        required=["expression"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="search_web",
                    description=(
                        "Search the web for a factual answer using DuckDuckGo instant results. "
                        "Use for general knowledge, news, definitions, or facts not in Darrien's profile."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "query": types.Schema(type=types.Type.STRING, description="Search query.")
                        },
                        required=["query"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="save_note_for_darrien",
                    description=(
                        "Save an important note that Darrien will see when he checks in. "
                        "Use this when someone asks you to tell Darrien something, makes a plan with him, "
                        "or leaves a message that needs his attention."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "note": types.Schema(type=types.Type.STRING, description="The note to save for Darrien, include who it's from and what they said/asked.")
                        },
                        required=["note"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="notify_darrien",
                    description=(
                        "Send an urgent notification directly to Darrien's Telegram. "
                        "Use only for time-sensitive or important things that can't wait — emergencies, "
                        "urgent meetup requests, something that needs his immediate response."
                    ),
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "message": types.Schema(type=types.Type.STRING, description="The urgent message to forward to Darrien.")
                        },
                        required=["message"],
                    ),
                ),
            ]
        )
    ],
)


_SESSION_FILE = str(Path(__file__).parent / "autoreply_session")
client = TelegramClient(_SESSION_FILE, int(API_ID), API_HASH)

_chat_histories: dict[int, list[types.Content]] = {}
_chat_locks:     dict[int, asyncio.Lock]         = {}
_replied_chats:  set[int]                        = set()

_pending_messages:   dict[int, list[str]]   = {}
_pending_timestamps: dict[int, list[float]] = {}
_pending_tasks:      dict[int, asyncio.Task] = {}
_pending_sender:     dict[int, str]          = {}
_pending_phone_key:  dict[int, str]          = {}

# Cooldown: when Darrien texts a contact, suppress Elora's replies for 2 min
_darrien_last_texted: dict[int, float] = {}  # chat_id → monotonic time
DARRIEN_COOLDOWN_S = 120

# Track messages Elora is about to send so on_outgoing can ignore them
_elora_sent_bodies: dict[str, int] = {}  # body → count

_URL_RE = re.compile(r'^(https?://|www\.)\S+$', re.IGNORECASE)

my_id:          int | None      = None
_startup_time:  datetime | None = None
_allowed_ids:   set[int]        = set()
_id_to_phone:   dict[int, str]  = {}


def _get_history(chat_id: int) -> list[types.Content]:
    return _chat_histories.setdefault(chat_id, [])

def _trim_history(chat_id: int) -> None:
    h = _chat_histories.get(chat_id, [])
    if len(h) > MAX_HISTORY_TURNS * 2:
        _chat_histories[chat_id] = h[-(MAX_HISTORY_TURNS * 2):]

def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]


def _run_elora(chat_id: int, phone_key: str, user_message: str, prior_context: str | None = None) -> str:
    _trim_history(chat_id)
    history = _get_history(chat_id)

    memory = load_memory(phone_key)
    context = "[CONTACT: NEW]\n\n" if memory is None else f"[CONTACT MEMORY: {memory}]\n\n"
    session_flag = "[SESSION START: This is the very first message of this session. You are Elora — not Darrien. If you decide to reply, you may briefly and naturally mention that Darrien is busy and you're covering for him. But check [PRIOR CONVERSATION] first — if their message is clearly a reaction to what Darrien said before you joined, output [SKIP] instead. NEVER write as Darrien or continue his voice from the prior conversation.]\n\n" if not history else ""
    prior_block = f"[PRIOR CONVERSATION — real chat history before you joined. 'Darrien (personal reply, before Elora joined)' entries are Darrien's own messages — do NOT continue in his voice or persona. You are Elora. Use this context only to understand what was going on.]\n{prior_context}\n\n" if prior_context else ""
    now_block = f"[CURRENT TIME]\n{get_current_datetime()}\n\n"
    gemini_input = session_flag + context + now_block + prior_block + user_message

    chat = ai.chats.create(
        model="gemini-2.5-flash",
        config=_ELORA_CONFIG,
        history=history,
    )

    last_err = None
    for attempt in range(3):
        try:
            response = chat.send_message(gemini_input)
            break
        except Exception as e:
            last_err = e
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                wait = 3 * (attempt + 1)
                log_info(f"Gemini busy, retrying in {wait}s... ({attempt + 1}/3)")
                _time.sleep(wait)
            else:
                raise
    else:
        raise last_err

    for _ in range(5):
        fcs = response.function_calls
        if not fcs:
            break
        tool_parts = []
        for fc in fcs:
            if fc.name == "search_darrien_profile":
                query = fc.args.get("query", "")
                result = search_darrien_profile(query)
                log_tool(f"search_darrien_profile({query!r})")
            elif fc.name == "update_contact_memory":
                content = fc.args.get("content", "")
                save_memory(phone_key, content)
                result = "Memory saved."
            elif fc.name == "get_current_datetime":
                result = get_current_datetime()
                log_tool("get_current_datetime()")
            elif fc.name == "get_weather":
                city = fc.args.get("city", "Jakarta")
                result = get_weather(city)
                log_tool(f"get_weather({city!r})")
            elif fc.name == "calculate":
                expr = fc.args.get("expression", "")
                result = calculate(expr)
                log_tool(f"calculate({expr!r})")
            elif fc.name == "search_web":
                query = fc.args.get("query", "")
                result = search_web(query)
                log_tool(f"search_web({query!r})")
            elif fc.name == "save_note_for_darrien":
                note = fc.args.get("note", "")
                result = save_note_for_darrien(note)
            elif fc.name == "notify_darrien":
                message = fc.args.get("message", "")
                result = notify_darrien(message)
            else:
                result = "Unknown tool."
            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name, response={"result": result}
                    )
                )
            )
        response = chat.send_message(tool_parts)

    try:
        text = (response.text or "").strip()
    except Exception:
        return ""

    if text.startswith("[CONTACT"):
        parts = text.split("\n\n", 1)
        text = parts[1].strip() if len(parts) > 1 else ""

    if text and text != SKIP_TOKEN:
        _chat_histories[chat_id].append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )
        _chat_histories[chat_id].append(
            types.Content(role="model", parts=[types.Part(text=text)])
        )

    return text


def _typing_seconds(reply: str) -> float:
    return min(max(random.uniform(0.8, 2.0) + len(reply) * 0.028, 1.5), 12.0)


async def _process_batch(chat_id: int) -> None:
    try:
        await asyncio.sleep(BATCH_WINDOW_SECONDS)
    except asyncio.CancelledError:
        return

    # Cooldown check — before consuming pending state.
    # If Darrien texted this contact within the last 2 min, defer until window expires.
    last_texted = _darrien_last_texted.get(chat_id)
    cooldown_was_active = False
    if last_texted is not None:
        remaining = DARRIEN_COOLDOWN_S - (_time.monotonic() - last_texted)
        if remaining > 0:
            cooldown_was_active = True
            log_skip(f"[TG] {_pending_sender.get(chat_id, str(chat_id))} — Darrien active, deferring {int(remaining + 0.5)}s")
            try:
                await asyncio.sleep(remaining + 0.5)
            except asyncio.CancelledError:
                return  # new message came in; new task will handle it

    messages   = _pending_messages.pop(chat_id, [])
    timestamps = _pending_timestamps.pop(chat_id, [])
    sender     = _pending_sender.pop(chat_id, str(chat_id))
    phone_key  = _pending_phone_key.pop(chat_id, f"tg_{chat_id}")

    if not messages:
        return

    # Drop messages that were received while Darrien was still active.
    # Only messages that arrived AFTER the cooldown expired should get a reply.
    if cooldown_was_active and last_texted is not None:
        cooldown_end = last_texted + DARRIEN_COOLDOWN_S
        filtered = [msg for msg, ts in zip(messages, timestamps) if ts > cooldown_end]
        dropped = len(messages) - len(filtered)
        if dropped:
            log_skip(f"[TG] {sender} — {dropped} msg(s) sent during Darrien's active window, skipped")
        messages = filtered
        if not messages:
            return
    if REPLY_ONCE and chat_id in _replied_chats:
        return

    combined = "\n".join(messages)
    extra = f" +{len(messages)-1} more" if len(messages) > 1 else ""
    log_recv(f"[TG] {_c('1', sender)}{extra}: {combined[:80]!r}")
    log_info(f"generating... (memory key: {phone_key})")

    # Single fetch: already-replied check + prior context for Elora.
    # get_messages returns newest-first.
    prior_context: str | None = None
    try:
        msgs = await client.get_messages(chat_id, limit=15)
        batch_set = set(messages)

        # ── Already-replied check ──────────────────────────────────────────────
        # Find the most recent batch message in the history (index 0 = newest).
        last_batch_pos: int | None = None
        for i, m in enumerate(msgs):
            if not m.out and m.text and m.text in batch_set:
                last_batch_pos = i
                break

        if last_batch_pos is not None:
            # Any outgoing message at a lower index (more recent) = Darrien already replied
            darrien_replies = [m for m in msgs[:last_batch_pos] if m.out and m.text]
            if darrien_replies:
                darrien_reply_text = "\n".join(m.text for m in reversed(darrien_replies))
                h = _get_history(chat_id)
                h.append(types.Content(role="user",  parts=[types.Part(text=combined)]))
                h.append(types.Content(role="model", parts=[types.Part(text=f"[Darrien replied directly] {darrien_reply_text}")]))
                _trim_history(chat_id)
                log_skip(f"[TG] {sender} — Darrien already replied, context saved")
                return

        # ── Prior context for Elora ────────────────────────────────────────────
        # Exclude current batch + Elora's own session replies to avoid duplication.
        elora_texts = {
            p.text[:100]
            for entry in _get_history(chat_id) if entry.role == "model"
            for p in entry.parts if p.text
        }
        chron_msgs = list(reversed(msgs))  # oldest-first
        prior_lines = []
        for m in chron_msgs:
            if not m.text:
                continue
            if not m.out and m.text in batch_set:
                continue
            if m.out and m.text[:100] in elora_texts:
                continue
            label_str = "Darrien (personal reply, before Elora joined)" if m.out else sender
            local_dt = m.date.astimezone()
            ts = local_dt.strftime('%Y-%m-%d %H:%M')
            prior_lines.append(f"[{ts}] {label_str}: {m.text[:200]}")
        if prior_lines:
            prior_context = "\n".join(prior_lines[-8:])
            log_info(f"prior context: {len(prior_lines[-8:])} message(s)")
    except Exception as e:
        log_info(f"fetch failed: {e}")

    lock = _get_lock(chat_id)
    async with lock:
        try:
            reply = await asyncio.to_thread(_run_elora, chat_id, phone_key, combined, prior_context)
        except Exception as e:
            log_err(f"Gemini error: {e}")
            return

        if not reply or reply == SKIP_TOKEN:
            log_skip(f"[TG] {sender} — no reply needed")
            return

        log_send(f"[TG] Elora → {_c('1', sender)}: {reply[:80]!r}")
        async with client.action(chat_id, "typing"):
            await asyncio.sleep(_typing_seconds(reply))
        _elora_sent_bodies[reply] = _elora_sent_bodies.get(reply, 0) + 1
        await client.send_message(chat_id, reply)
        def _cleanup_sent(body=reply):
            n = _elora_sent_bodies.get(body, 0)
            if n <= 1: _elora_sent_bodies.pop(body, None)
            else: _elora_sent_bodies[body] = n - 1
        asyncio.get_event_loop().call_later(5, _cleanup_sent)

        asyncio.create_task(_schedule_memory_consolidation(phone_key, sender, combined, reply))

        if REPLY_ONCE:
            _replied_chats.add(chat_id)

async def resolve_contacts() -> None:
    global _allowed_ids, _id_to_phone
    log_info("Resolving Telegram contacts...")
    for raw in ALLOWED_CONTACTS:
        c = raw.strip()
        phone_digits = _normalize_phone(c)
        entity = None
        try:
            entity = await client.get_entity(c)
        except Exception:
            try:
                result = await client(ImportContactsRequest([
                    InputPhoneContact(client_id=0, phone=c, first_name="Contact", last_name="")
                ]))
                if result.users:
                    entity = result.users[0]
            except Exception:
                pass
        if entity is None:
            log_info(f"{c} → not found on Telegram, WhatsApp only")
            continue
        _allowed_ids.add(entity.id)
        _id_to_phone[entity.id] = phone_digits
        name = getattr(entity, "first_name", None) or c
        log_ok(f"{c} → {name} (ID: {entity.id}, mem key: {phone_digits})")
    print()

def _is_allowed(sender_id: int) -> bool:
    return sender_id in _allowed_ids


@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def on_incoming(event):
    if event.sender_id == my_id:
        return
    if _startup_time and event.message.date < _startup_time:
        return

    sender = await event.get_sender()
    if not isinstance(sender, User) or sender.bot:
        return

    name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or str(sender.id)
    text = event.message.text

    if not _is_allowed(sender.id):
        log_info(f"[TG] {name}: {(text or '')[:40]!r} — not in list, ignored")
        return
    if not text:
        return

    # Skip messages that are only URLs
    tokens = text.split()
    if tokens and all(_URL_RE.match(t) for t in tokens):
        log_skip(f"[TG] {name} — link only, skipping")
        return

    chat_id   = event.chat_id
    phone_key = _id_to_phone.get(sender.id, f"tg_{sender.id}")

    _pending_messages.setdefault(chat_id, []).append(text)
    _pending_timestamps.setdefault(chat_id, []).append(_time.monotonic())
    _pending_sender[chat_id]    = name
    _pending_phone_key[chat_id] = phone_key

    existing = _pending_tasks.get(chat_id)
    if existing and not existing.done():
        existing.cancel()
    _pending_tasks[chat_id] = asyncio.create_task(_process_batch(chat_id))


@client.on(events.NewMessage(outgoing=True))
async def on_outgoing(event):
    if REPLY_ONCE and event.is_private:
        _replied_chats.discard(event.chat_id)
    # Track when Darrien personally messages an allowed contact
    if event.is_private and event.chat_id in _allowed_ids:
        # Ignore messages Elora sent herself — don't trigger cooldown for her own replies
        body = event.message.text or ""
        if body and body in _elora_sent_bodies:
            return  # _cleanup_sent scheduled at send time handles decrement
        was_active = (
            event.chat_id in _darrien_last_texted and
            _time.monotonic() - _darrien_last_texted[event.chat_id] < DARRIEN_COOLDOWN_S
        )
        _darrien_last_texted[event.chat_id] = _time.monotonic()
        if not was_active:
            log_info(f"[TG] Darrien texted {event.chat_id} — Elora paused 2min")


async def main():
    global my_id, _startup_time

    await client.start(phone=TELEGRAM_PHONE)
    _startup_time = datetime.now(timezone.utc)

    me = await client.get_me()
    my_id = me.id
    log_ok(f"Telegram → {me.first_name} (@{me.username})")

    await resolve_contacts()

    log_ok(f"Elora live on Telegram  |  {len(ALLOWED_CONTACTS)} contacts  |  batch {BATCH_WINDOW_SECONDS}s")
    log_info("Memory is shared with WhatsApp by phone number")
    print()

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
