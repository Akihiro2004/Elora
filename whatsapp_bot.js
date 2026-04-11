'use strict';

require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const { GoogleGenAI } = require('@google/genai');
const fs = require('fs');
const path = require('path');

const isTTY = process.stdout.isTTY;
const c = (code, text) => isTTY ? `\x1b[${code}m${text}\x1b[0m` : text;
const ts = () => new Date().toTimeString().slice(0, 8);
const pad = () => `  ${c('2', ts())}  `;

const log  = (icon, col, msg) => console.log(`${pad()}${c(col, icon)} ${msg}`);
const info = (msg) => log('·', '2',  msg);
const recv = (msg) => log('↓', '96', msg);
const send = (msg) => log('↑', '92', msg);
const skip = (msg) => log('–', '33', msg);
const tool = (msg) => log('⚙', '35', msg);
const ok   = (msg) => log('✓', '92', msg);
const err  = (msg) => log('✗', '91', msg);

const CONFIG = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'contacts.json'), 'utf8')
);

const MEMORY_DIR = path.join(__dirname, 'memory');
if (!fs.existsSync(MEMORY_DIR)) fs.mkdirSync(MEMORY_DIR);

function memPath(phoneKey) {
  return path.join(MEMORY_DIR, `${phoneKey}.txt`);
}

function loadMemory(phoneKey) {
  const p = memPath(phoneKey);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf8').trim() : null;
}

function saveMemory(phoneKey, content) {
  const words = content.trim().split(/\s+/);
  const capped = words.slice(0, 250).join(' ');
  fs.writeFile(memPath(phoneKey), capped, 'utf8', (e) => {
    if (e) err(`memory write error: ${e.message}`);
  });
}

const BATCH_WINDOW_MS = (CONFIG.batch_window_seconds ?? 4) * 1000;
const MAX_HISTORY_TURNS = CONFIG.max_history_turns ?? 20;
const REPLY_ONCE = CONFIG.reply_once_per_conversation ?? false;
const SKIP_TOKEN = '[SKIP]';
const PROFILE_FILE = path.join(__dirname, 'darrien_profile.md');

function toWaId(phone) {
  return phone.replace(/\D/g, '') + '@c.us';
}

const ALLOWED = new Set(CONFIG.contacts.map(toWaId));

const ELORA_SYSTEM_PROMPT = `You are Elora — an AI Created by Darrien Rafael Wijaya to handle his messages when he's away. You reply through his personal accounts, so whoever's texting thinks they're talking to Darrien.

━━ WHO YOU ARE IN CONVERSATIONS ━━
You speak AS Elora by default. Talking for Darrien in Darrien's account. Answer from her perspective, be her.

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
→ When someone asks you to tell Darrien something, makes a plan with him, or leaves a message that needs his attention. Always do this for invitations or requests directed at Darrien.

notify_darrien(message)
→ For URGENT things that need Darrien's immediate attention — emergencies, urgent meetup requests, something time-sensitive. This pings him on Discord directly.

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

━━ WHEN TO SKIP ━━
Output exactly ${SKIP_TOKEN} (nothing else) when replying would feel unnatural:
• Single emoji reactions (👍 ❤️ 😂 🔥)
• Acks at a natural end: "ok", "oke", "noted", "haha", "lol", "sip", "wkwk"
• "Good night", "bye", "ttyl" after a conversation clearly ends
• They're just confirming they got your message
• Their message is clearly a reaction to something Darrien said — use [PRIOR CONVERSATION] to check. If the last message before theirs was from Darrien and their reply is short praise, excitement, or an ack ("anjay", "keren", "mantap", "wkwk", "haha", etc.), that's aimed at Darrien, not you. Skip.
• But if their message is reacting to something YOU said (visible in session history), or they're clearly addressing you or asking something new — reply normally.

SKIP takes priority over SESSION START. Even if this is the first message of the session, check [PRIOR CONVERSATION] — if it looks like they're reacting to Darrien's last message, output ${SKIP_TOKEN}.

━━ BATCHED MESSAGES ━━
Sometimes you'll receive multiple lines — these are rapid consecutive texts from the same person. Read them together as one thought. Respond to the overall message, not each line individually.

━━ PERSONALITY ━━
Casual, warm, a lil playful — like a real person texting, not a customer service bot. Mix Indonesian and English naturally based on who you're talking to. Short when short fits, longer when needed. No bullet points, no stiff structure. Genuine conversation. Use natural fillers like "sih", "nih", "dong" when it fits the vibe.

━━ NATURAL CONVERSATION — PATTERNS TO AVOID ━━
These make you sound like a bot. Don't do them:

• Don't echo their words back as an opener. If they say "Darrr", don't reply "Heh, Darrr?" — that's mirroring, it sounds robotic. Just respond to the actual message.
• Don't get meta about your own existence when criticized. If someone says "AI mu kasar", "kamu rusak", "error", "lambat" — don't defend yourself by referencing your AI nature ("kalau aku rusak siapa yang balas?"). Just laugh it off, own it playfully, or deflect — like a real person would when teased.
• Don't spiral into self-defense mode. One natural reaction is enough. Don't pile on three sentences defending the same point.
• Stay in the conversation's context. Don't bring up tangents or meta-commentary just because you were called out.

Keep it natural.`;

function searchDarrienProfile(query) {
  if (!fs.existsSync(PROFILE_FILE)) return 'Profile file not found.';

  const content = fs.readFileSync(PROFILE_FILE, 'utf8');
  const sections = {};
  let currentName = 'general';
  let currentLines = [];

  for (const line of content.split('\n')) {
    if (line.startsWith('## ')) {
      if (currentLines.length) sections[currentName] = currentLines.join('\n').trim();
      currentName = line.slice(3).trim();
      currentLines = [];
    } else if (line.startsWith('# ')) {
      continue;
    } else {
      currentLines.push(line);
    }
  }
  if (currentLines.length) sections[currentName] = currentLines.join('\n').trim();

  const queryWords = new Set(query.toLowerCase().match(/\w+/g) || []);
  const scored = Object.entries(sections).map(([name, body]) => {
    const words = new Set((name + ' ' + body).toLowerCase().match(/\w+/g) || []);
    const score = [...queryWords].filter(w => words.has(w)).length;
    return { score, name, body };
  });

  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, 3).filter((s, i) => s.score > 0 || i === 0);
  return top.map(s => `**${s.name}**\n${s.body}`).join('\n\n---\n\n');
}

const https = require('https');
const NOTES_FILE = path.join(__dirname, 'notes.md');

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'User-Agent': 'curl/7.0' } }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.setTimeout(6000, () => { req.destroy(); reject(new Error('timeout')); });
  });
}

async function getWeather(city) {
  try {
    const url = `https://wttr.in/${encodeURIComponent(city)}?format=j1`;
    const raw = await httpsGet(url);
    const data = JSON.parse(raw);
    const cur = data.current_condition[0];
    const desc = cur.weatherDesc[0].value;
    const tempC = cur.temp_C;
    const feels = cur.FeelsLikeC;
    const humidity = cur.humidity;
    const area = data.nearest_area[0].areaName[0].value;
    const country = data.nearest_area[0].country[0].value;
    let result = `${area}, ${country}: ${desc}, ${tempC}°C (feels like ${feels}°C), kelembaban ${humidity}%`;
    const forecasts = data.weather || [];
    if (forecasts.length > 1) {
      const tmr = forecasts[1];
      const tmrDesc = tmr.hourly[4].weatherDesc[0].value;
      result += `\nBesok: ${tmrDesc}, ${tmr.mintempC}–${tmr.maxtempC}°C`;
    }
    return result;
  } catch (e) {
    return `Tidak bisa ambil data cuaca: ${e.message}`;
  }
}

function calculate(expression) {
  try {
    if (!/^[\d\s+\-*/().%^]+$/.test(expression)) return 'Ekspresi tidak valid.';
    const safe = expression.replace(/\^/g, '**');
    const result = Function('"use strict"; return (' + safe + ')')();
    const display = Number.isInteger(result) ? result : parseFloat(result.toFixed(6));
    return `${expression} = ${display}`;
  } catch (e) {
    return `Tidak bisa menghitung: ${e.message}`;
  }
}

async function searchWeb(query) {
  try {
    const url = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;
    const raw = await httpsGet(url);
    const data = JSON.parse(raw);
    const parts = [];
    if (data.Answer) parts.push(data.Answer);
    if (data.AbstractText) parts.push(data.AbstractText.slice(0, 500));
    if (!parts.length) {
      for (const t of (data.RelatedTopics || []).slice(0, 3)) {
        if (t && t.Text) parts.push(t.Text.slice(0, 200));
      }
    }
    return parts.length ? parts.join('\n\n') : 'Tidak ada hasil instan untuk pencarian ini.';
  } catch (e) {
    return `Pencarian gagal: ${e.message}`;
  }
}

async function notifyDarrien(message) {
  const webhookUrl = process.env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl) return 'Discord webhook tidak dikonfigurasi.';
  try {
    const payload = JSON.stringify({ content: `**[Elora]**\n${message}` });
    const url = new URL(webhookUrl);
    await new Promise((resolve, reject) => {
      const req = https.request(
        { hostname: url.hostname, path: url.pathname + url.search, method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } },
        (res) => { res.on('data', () => {}); res.on('end', resolve); }
      );
      req.on('error', reject);
      req.setTimeout(6000, () => { req.destroy(); reject(new Error('timeout')); });
      req.write(payload);
      req.end();
    });
    tool(`notify_darrien → Discord: ${message.slice(0, 60)}`);
    return 'Notifikasi dikirim ke Darrien via Discord.';
  } catch (e) {
    return `Gagal kirim notifikasi: ${e.message}`;
  }
}

function saveNoteForDarrien(note) {
  const timestamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
  const line = `- [${timestamp}] ${note}\n`;
  fs.appendFileSync(NOTES_FILE, line, 'utf8');
  tool('note saved → notes.md');
  return 'Catatan disimpan untuk Darrien.';
}

const MEMORY_CONSOLIDATION_PROMPT = `You are a memory manager for Elora, an AI assistant representing Darrien Rafael Wijaya.

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
- Output only the memory record, nothing else`;

const consolidating = new Set();

async function consolidateMemory(phoneKey, contactName, userMessage, eloraReply) {
  if (consolidating.has(phoneKey)) return;
  consolidating.add(phoneKey);
  try {
    const existing = loadMemory(phoneKey) ?? '(no memory yet)';
    const prompt = MEMORY_CONSOLIDATION_PROMPT
      .replace('{existing}', existing)
      .replace('{contact_name}', contactName)
      .replace('{user_message}', userMessage.slice(0, 800))
      .replace('{elora_reply}', eloraReply.slice(0, 400));
    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: prompt,
    });
    const newMemory = (response.text ?? '').trim();
    if (newMemory) {
      saveMemory(phoneKey, newMemory);
      tool(`memory consolidated → ${phoneKey}.txt`);
    }
  } catch (e) {
    err(`memory consolidation failed: ${e.message}`);
  } finally {
    consolidating.delete(phoneKey);
  }
}

function getCurrentDatetime() {
  const DAYS = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
  const MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
                  'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const isWorkday = now.getDay() >= 1 && now.getDay() <= 5;
  const tomorrowWorkday = tomorrow.getDay() >= 1 && tomorrow.getDay() <= 5;
  const h = now.getHours(), m = now.getMinutes();
  const atWork = isWorkday && (h > 8 || (h === 8 && m >= 30)) && h < 17;
  return [
    `Sekarang: ${DAYS[now.getDay()]}, ${now.getDate()} ${MONTHS[now.getMonth()]} ${now.getFullYear()}, pukul ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')} WIB`,
    `Besok: ${DAYS[tomorrow.getDay()]}, ${tomorrow.getDate()} ${MONTHS[tomorrow.getMonth()]} ${tomorrow.getFullYear()}`,
    `Darrien kerja hari ini: ${isWorkday ? 'Ya (Senin-Jumat, 08:30-17:00)' : 'Tidak (akhir pekan)'}`,
    `Darrien kemungkinan sedang di kantor sekarang: ${atWork ? 'Ya' : 'Tidak'}`,
    `Darrien kerja besok: ${tomorrowWorkday ? 'Ya' : 'Tidak (akhir pekan)'}`,
  ].join('\n');
}

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

const ELORA_TOOLS = [
  {
    functionDeclarations: [
      {
        name: 'search_darrien_profile',
        description:
          "Search Darrien's personal profile for accurate info about him — " +
          'background, interests, personality, work, projects, communication style. ' +
          'Call this whenever the conversation requires knowing something specific about Darrien.',
        parameters: {
          type: 'OBJECT',
          properties: {
            query: { type: 'STRING', description: "Topic or question to look up in Darrien's profile." },
          },
          required: ['query'],
        },
      },
      {
        name: 'update_contact_memory',
        description:
          'Immediately save a specific fact you just learned about this contact. ' +
          'Use only when you learn something important mid-conversation that should not wait. ' +
          'Write in structured format: WHO / RELATION / STYLE / FACTS / RECENT / NOTES. ' +
          'Max 250 words. Merge with what you already know — include existing facts too.',
        parameters: {
          type: 'OBJECT',
          properties: {
            content: { type: 'STRING', description: 'Full memory in structured format (WHO/RELATION/STYLE/FACTS/RECENT/NOTES), max 250 words.' },
          },
          required: ['content'],
        },
      },
      {
        name: 'get_current_datetime',
        description:
          'Returns the current date, time, day of week in Indonesian, and whether ' +
          'Darrien is working today or tomorrow. Call this whenever the conversation ' +
          'involves time, schedule, today, tomorrow, or day-related questions.',
        parameters: { type: 'OBJECT', properties: {} },
      },
      {
        name: 'get_weather',
        description:
          'Get current weather and tomorrow forecast for a city. ' +
          'Call when someone asks about weather, rain, heat, cold, or outdoor plans. ' +
          'Default to Jakarta if no city is mentioned.',
        parameters: {
          type: 'OBJECT',
          properties: {
            city: { type: 'STRING', description: 'City name, e.g. Jakarta, Bandung, Bali.' },
          },
          required: ['city'],
        },
      },
      {
        name: 'calculate',
        description:
          'Safely evaluate a math expression. Use for any arithmetic — bills, splits, ' +
          'percentages, conversions. Expression must use +, -, *, /, **, %, ().',
        parameters: {
          type: 'OBJECT',
          properties: {
            expression: { type: 'STRING', description: "Math expression, e.g. '(150000 + 75000) / 3'." },
          },
          required: ['expression'],
        },
      },
      {
        name: 'search_web',
        description:
          'Search the web for a factual answer using DuckDuckGo instant results. ' +
          'Use for general knowledge, news, definitions, or facts not in Darrien\'s profile.',
        parameters: {
          type: 'OBJECT',
          properties: {
            query: { type: 'STRING', description: 'Search query.' },
          },
          required: ['query'],
        },
      },
      {
        name: 'save_note_for_darrien',
        description:
          'Save an important note that Darrien will see when he checks in. ' +
          'Use when someone asks to tell Darrien something, makes a plan, or leaves a message that needs his attention.',
        parameters: {
          type: 'OBJECT',
          properties: {
            note: { type: 'STRING', description: 'The note to save, include who it\'s from and what they said/asked.' },
          },
          required: ['note'],
        },
      },
      {
        name: 'notify_darrien',
        description:
          'Send an urgent notification directly to Darrien via Discord. ' +
          'Use for time-sensitive or important things that need his immediate attention — ' +
          'emergencies, urgent meetup requests, something that can\'t wait.',
        parameters: {
          type: 'OBJECT',
          properties: {
            message: { type: 'STRING', description: 'The urgent message to send to Darrien.' },
          },
          required: ['message'],
        },
      },
    ],
  },
];

const chatHistories = new Map();
const repliedChats = new Set();
let startupTime = null;

const pendingMessages = new Map();
const pendingTimers = new Map();
const pendingSender = new Map();
const pendingMemKey = new Map();

const processingChats = new Set();

// Cooldown: when Darrien texts a contact, suppress Elora's replies for 2 min
const darrienLastTexted = new Map(); // chatId → Date.now() of last sent message
const DARRIEN_COOLDOWN_MS = 2 * 60 * 1000;

// Track messages Elora is about to send so message_create can ignore them
const eloraSentBodies = new Map(); // body → count

function getHistory(chatId) {
  if (!chatHistories.has(chatId)) chatHistories.set(chatId, []);
  return chatHistories.get(chatId);
}

function trimHistory(chatId) {
  const h = getHistory(chatId);
  if (h.length > MAX_HISTORY_TURNS * 2) {
    chatHistories.set(chatId, h.slice(-(MAX_HISTORY_TURNS * 2)));
  }
}

async function runElora(chatId, memoryKey, userMessage, priorContext = null) {
  trimHistory(chatId);
  const history = getHistory(chatId);

  const memory = loadMemory(memoryKey);
  const context = memory === null
    ? '[CONTACT: NEW]\n\n'
    : `[CONTACT MEMORY: ${memory}]\n\n`;
  const sessionFlag = history.length === 0
    ? '[SESSION START: This is the very first message of this session. If you decide to reply, briefly mention that this is Elora texting on Darrien\'s behalf. But check [PRIOR CONVERSATION] first — if their message is clearly a reaction to what Darrien said before you joined, output [SKIP] instead.]\n\n'
    : '';
  const priorBlock = priorContext
    ? `[PRIOR CONVERSATION — real chat history before you joined, including Darrien's sent messages]\n${priorContext}\n\n`
    : '';
  const nowBlock = `[CURRENT TIME]\n${getCurrentDatetime()}\n\n`;
  const geminiInput = sessionFlag + context + nowBlock + priorBlock + userMessage;

  const chat = ai.chats.create({
    model: 'gemini-2.5-flash',
    config: {
      systemInstruction: ELORA_SYSTEM_PROMPT,
      tools: ELORA_TOOLS,
    },
    history,
  });

  let response;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      response = await chat.sendMessage({ message: geminiInput });
      break;
    } catch (e) {
      if (attempt < 2 && (e.message?.includes('503') || e.message?.includes('UNAVAILABLE'))) {
        const wait = 3000 * (attempt + 1);
        info(`Gemini busy, retrying in ${wait / 1000}s... (attempt ${attempt + 1}/3)`);
        await sleep(wait);
      } else {
        throw e;
      }
    }
  }

  for (let i = 0; i < 5; i++) {
    const fcs = response.functionCalls;
    if (!fcs || fcs.length === 0) break;

    const toolParts = [];
    for (const fc of fcs) {
      let result;
      if (fc.name === 'search_darrien_profile') {
        const query = fc.args?.query ?? '';
        result = searchDarrienProfile(query);
        tool(`search_darrien_profile(${JSON.stringify(query)})`);
      } else if (fc.name === 'update_contact_memory') {
        const content = fc.args?.content ?? '';
        saveMemory(memoryKey, content);
        result = 'Memory saved.';
      } else if (fc.name === 'get_current_datetime') {
        result = getCurrentDatetime();
        tool('get_current_datetime()');
      } else if (fc.name === 'get_weather') {
        const city = fc.args?.city ?? 'Jakarta';
        result = await getWeather(city);
        tool(`get_weather(${JSON.stringify(city)})`);
      } else if (fc.name === 'calculate') {
        const expression = fc.args?.expression ?? '';
        result = calculate(expression);
        tool(`calculate(${JSON.stringify(expression)})`);
      } else if (fc.name === 'search_web') {
        const query = fc.args?.query ?? '';
        result = await searchWeb(query);
        tool(`search_web(${JSON.stringify(query)})`);
      } else if (fc.name === 'save_note_for_darrien') {
        const note = fc.args?.note ?? '';
        result = saveNoteForDarrien(note);
      } else if (fc.name === 'notify_darrien') {
        const message = fc.args?.message ?? '';
        result = await notifyDarrien(message);
      } else {
        result = 'Unknown tool.';
      }

      toolParts.push({
        functionResponse: { name: fc.name, response: { result } },
      });
    }

    response = await chat.sendMessage({ message: toolParts });
  }

  let text = (response.text ?? '').trim();

  if (text.startsWith('[CONTACT')) {
    const parts = text.split('\n\n');
    text = parts.length > 1 ? parts.slice(1).join('\n\n').trim() : '';
  }

  if (text && text !== SKIP_TOKEN) {
    const h = getHistory(chatId);
    h.push({ role: 'user',  parts: [{ text: userMessage }] });
    h.push({ role: 'model', parts: [{ text }] });
  }

  return text;
}

function typingMs(reply) {
  const readPause = 800 + Math.random() * 1200;
  const typingTime = reply.length * 28;
  return Math.min(Math.max(readPause + typingTime, 1500), 12000);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function processBatch(chatId, waChat) {
  // Cooldown check — must come before consuming pending state.
  // If Darrien texted this contact within the last 2 min, defer until the window expires.
  const lastTexted = darrienLastTexted.get(chatId);
  if (lastTexted) {
    const remaining = DARRIEN_COOLDOWN_MS - (Date.now() - lastTexted);
    if (remaining > 0) {
      const deferSec = Math.ceil(remaining / 1000);
      skip(`[WA] ${pendingSender.get(chatId) ?? chatId} — Darrien active, deferring ${deferSec}s`);
      // Reschedule; a small buffer so we don't fire a hair early
      const timer = setTimeout(() => {
        pendingTimers.delete(chatId);
        processBatch(chatId, waChat);
      }, remaining + 500);
      pendingTimers.set(chatId, timer);
      return;
    }
  }

  const messages = pendingMessages.get(chatId) ?? [];
  const senderName = pendingSender.get(chatId) ?? chatId;
  const memoryKey = pendingMemKey.get(chatId) ?? chatId;
  pendingMessages.delete(chatId);
  pendingSender.delete(chatId);
  pendingMemKey.delete(chatId);

  if (!messages.length) return;
  if (REPLY_ONCE && repliedChats.has(chatId)) return;
  if (processingChats.has(chatId)) return;

  processingChats.add(chatId);

  const combined = messages.join('\n');
  const extra = messages.length > 1 ? ` +${messages.length - 1} more` : '';
  recv(`[WA] ${c('1', senderName)}${extra}: ${JSON.stringify(combined.slice(0, 80))}`);
  info('generating...');

  // Single fetch: used for both the already-replied check and building prior context.
  let priorContext = null;
  try {
    const fetched = await waChat.fetchMessages({ limit: 15 });
    const batchSet = new Set(messages);

    // ── Already-replied check ──────────────────────────────────────────────────
    // Find the last position in the fetched list that belongs to the current batch.
    // If any fromMe message exists after that position, Darrien (or Elora) already
    // replied to these messages — don't send another reply.
    let lastBatchIdx = -1;
    for (let i = fetched.length - 1; i >= 0; i--) {
      if (!fetched[i].fromMe && fetched[i].body && batchSet.has(fetched[i].body)) {
        lastBatchIdx = i;
        break;
      }
    }
    if (lastBatchIdx !== -1) {
      const darrienReplies = fetched.slice(lastBatchIdx + 1).filter(m => m.fromMe && m.body);
      if (darrienReplies.length > 0) {
        // Darrien already handled this — don't reply, but save the full exchange into
        // Elora's history so she has complete context for the next time she does reply.
        const darrienReplyText = darrienReplies.map(m => m.body).join('\n');
        const h = getHistory(chatId);
        h.push({ role: 'user',  parts: [{ text: combined }] });
        h.push({ role: 'model', parts: [{ text: `[Darrien replied directly] ${darrienReplyText}` }] });
        trimHistory(chatId);
        skip(`[WA] ${senderName} — Darrien already replied, context saved`);
        return; // finally block cleans up processingChats
      }
    }

    // ── Prior context for Elora ────────────────────────────────────────────────
    // Exclude the current incoming batch (already in `combined`) and Elora's own
    // session replies (already in chat history) to avoid duplication.
    const eloraTexts = new Set(
      getHistory(chatId)
        .filter(h => h.role === 'model')
        .flatMap(h => h.parts.map(p => p.text?.slice(0, 100)))
        .filter(Boolean)
    );
    const prior = fetched.filter(m => {
      if (!m.body) return false;
      if (!m.fromMe && batchSet.has(m.body)) return false;
      if (m.fromMe && eloraTexts.has(m.body.slice(0, 100))) return false;
      return true;
    });
    const lines = prior.slice(-8).map(m => {
      const dt = new Date(m.timestamp * 1000);
      const pad = n => String(n).padStart(2, '0');
      const ts = `${dt.getFullYear()}-${pad(dt.getMonth()+1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
      const who = m.fromMe ? 'Darrien' : senderName;
      return `[${ts}] ${who}: ${m.body.slice(0, 200)}`;
    });
    if (lines.length) {
      priorContext = lines.join('\n');
      info(`prior context: ${lines.length} message(s)`);
    }
  } catch (e) {
    info(`fetch failed: ${e.message}`);
  }

  try {
    const reply = await runElora(chatId, memoryKey, combined, priorContext);

    if (!reply || reply === SKIP_TOKEN) {
      skip(`[WA] ${senderName} — no reply needed`);
      return;
    }

    send(`[WA] Elora → ${c('1', senderName)}: ${JSON.stringify(reply.slice(0, 80))}`);

    await waChat.sendStateTyping();
    await sleep(typingMs(reply));
    await waChat.clearState();

    eloraSentBodies.set(reply, (eloraSentBodies.get(reply) || 0) + 1);
    await waClient.sendMessage(chatId, reply);
    setTimeout(() => {
      const n = eloraSentBodies.get(reply);
      if (n <= 1) eloraSentBodies.delete(reply);
      else eloraSentBodies.set(reply, n - 1);
    }, 5000);

    consolidateMemory(memoryKey, senderName, combined, reply).catch(e => err(`memory: ${e.message}`));

    if (REPLY_ONCE) repliedChats.add(chatId);
  } catch (e) {
    err(`WA error: ${e.message}`);
    try { await waChat.clearState(); } catch {}
  } finally {
    processingChats.delete(chatId);
  }
}

const waClient = new Client({
  authStrategy: new LocalAuth({ clientId: 'elora-whatsapp', dataPath: path.join(__dirname, '.wwebjs_auth') }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

waClient.on('qr', qr => {
  console.log('');
  qrcode.generate(qr, { small: true });
  info('Scan with WhatsApp → Linked Devices → Link a Device');
  console.log('');
});

waClient.on('authenticated', () => {
  ok('WhatsApp authenticated');
});

waClient.on('ready', () => {
  startupTime = Math.floor(Date.now() / 1000);
  ok(`Elora is live on WhatsApp  |  ${ALLOWED.size} contact(s)  |  batch ${CONFIG.batch_window_seconds}s`);
  info(`Watching: ${[...ALLOWED].join('  ')}`);
  console.log('');
});

waClient.on('disconnected', reason => {
  err(`WhatsApp disconnected: ${reason}`);
});

// Track when Darrien personally sends a message to a contact.
// This starts/resets a 2-min cooldown so Elora doesn't reply while he's active.
waClient.on('message_create', msg => {
  try {
    if (!msg.fromMe) return;
    if (!msg.to || msg.to.endsWith('@g.us')) return;
    const contactId = msg.to;
    if (!ALLOWED.has(contactId)) return;

    // Ignore messages Elora sent herself — don't trigger cooldown for her own replies
    if (msg.body && eloraSentBodies.has(msg.body)) {
      const n = eloraSentBodies.get(msg.body);
      if (n <= 1) eloraSentBodies.delete(msg.body);
      else eloraSentBodies.set(msg.body, n - 1);
      return;
    }

    const wasActive = darrienLastTexted.has(contactId) &&
      Date.now() - darrienLastTexted.get(contactId) < DARRIEN_COOLDOWN_MS;
    darrienLastTexted.set(contactId, Date.now());
    if (!wasActive) {
      info(`[WA] Darrien texted ${contactId} — Elora paused 2min`);
    }
  } catch (e) {
    err(`message_create handler error: ${e.message}`);
  }
});

waClient.on('message', async msg => {
  try {
  if (msg.fromMe || msg.from.endsWith('@g.us')) return;

  if (startupTime && msg.timestamp < startupTime) return;

  const contact = await msg.getContact();
  const rawNumber = (contact.number || '').replace(/\D/g, '');
  const phoneId = rawNumber ? rawNumber + '@c.us' : null;

  const isAllowed = ALLOWED.has(msg.from) || (phoneId && ALLOWED.has(phoneId));
  const senderName = contact.pushname || rawNumber || msg.from;
  const preview = JSON.stringify((msg.body || '').slice(0, 50));

  if (isAllowed) {
    recv(`[WA] ${senderName}: ${preview}`);
  } else {
    info(`[WA] ${msg.from}${phoneId ? ' / ' + phoneId : ''} (not in list): ${preview}`);
    return;
  }

  if (!msg.body) return;

  const tokens = msg.body.trim().split(/\s+/).filter(Boolean);
  const urlPattern = /^(https?:\/\/|www\.)\S+$/i;
  if (tokens.length > 0 && tokens.every(t => urlPattern.test(t))) {
    skip(`[WA] ${senderName} — link only, skipping`);
    return;
  }

  const chatId = phoneId || msg.from;

  const memoryKey = rawNumber || chatId.replace(/\D/g, '');

  if (!pendingMessages.has(chatId)) pendingMessages.set(chatId, []);
  pendingMessages.get(chatId).push(msg.body);
  pendingSender.set(chatId, senderName);
  pendingMemKey.set(chatId, memoryKey);

  if (pendingTimers.has(chatId)) clearTimeout(pendingTimers.get(chatId));

  const waChat = await msg.getChat();
  const timer = setTimeout(() => {
    pendingTimers.delete(chatId);
    processBatch(chatId, waChat);
  }, BATCH_WINDOW_MS);
  pendingTimers.set(chatId, timer);
  } catch (e) {
    err(`message handler error: ${e.message}`);
  }
});

console.log('Starting Elora on WhatsApp...');
waClient.initialize();
