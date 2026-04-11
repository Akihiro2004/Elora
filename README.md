# Elora

An AI assistant that auto-replies on your personal Telegram and WhatsApp accounts when you're away. It uses Gemini 2.5 Flash to have real conversations on your behalf, not like a bot, but more like someone who actually knows you.

The idea is you set it up once with your profile and your contacts list, and Elora handles the messages naturally. She decides when to reply and when to stay quiet, introduces herself the first time she talks to someone, and remembers things across conversations.

Both bots share the same memory per phone number, so if someone messages you on both Telegram and WhatsApp, Elora treats them as the same person.

---

## What it does

- Replies only to contacts you specify, everyone else gets ignored
- Reads your profile to talk about you accurately
- Remembers each contact (max 200 words per person, stored locally)
- Batches rapid messages into one reply so it feels natural
- Skips replying to things like "ok", "haha", emoji reactions, "good night" etc
- Shows a typing indicator with a realistic delay before sending
- Handles multiple conversations at the same time without mixing them up
- Retries automatically if Gemini is overloaded

---

## Requirements

You need:

- Python 3.11+
- Node.js 18+
- A Telegram API ID and hash from [my.telegram.org](https://my.telegram.org)
- A Gemini API key from [aistudio.google.com](https://aistudio.google.com/apikey)
- Your WhatsApp account accessible on your phone (for the QR scan)

---

## Setup

**1. Clone the repo**

```bash
git clone https://github.com/yourusername/elora.git
cd elora
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
npm install
```

**3. Set up your credentials**

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

```
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+628123456789
GEMINI_API_KEY=your_gemini_api_key
```

**4. Set up your contacts**

Copy `contacts.example.json` to `contacts.json` and add the phone numbers you want Elora to reply to:

```bash
cp contacts.example.json contacts.json
```

```json
{
  "contacts": [
    "+628123456789",
    "+628987654321"
  ],
  "batch_window_seconds": 4,
  "max_history_turns": 5,
  "reply_once_per_conversation": false
}
```

Only these numbers will get replies. Anyone else messaging you gets silently ignored.

**5. Set up your profile**

Copy `profile.example.md` to `your_name_profile.md` and fill it with info about yourself. This is what Elora reads when someone asks about you.

```bash
cp profile.example.md yourname_profile.md
```

Then update the `PROFILE_FILE` path in `bot.py` and `whatsapp_bot.js` to point to your file.

**6. Run it**

Telegram bot:

```bash
python bot.py
```

WhatsApp bot (runs separately):

```bash
node whatsapp_bot.js
```

First run on Telegram will ask you to enter your phone number and a verification code. First run on WhatsApp will show a QR code you scan with your phone under Linked Devices.

---

## Config options

These go in `contacts.json`:

| Key | Default | What it does |
|-----|---------|--------------|
| `contacts` | required | List of phone numbers Elora will reply to |
| `batch_window_seconds` | 4 | How long to wait after the last message before replying. Higher means more batching |
| `max_history_turns` | 5 | How many back-and-forth exchanges to keep in memory per chat |
| `reply_once_per_conversation` | false | If true, Elora only replies once and goes silent until you reply manually |

---

## How memory works

Each contact gets a `.txt` file in the `memory/` folder named by their phone number (e.g. `6281234567890.txt`). Elora writes a summary of who they are, their relation to you, and whether she already introduced herself. Max 200 words per contact.

Since both bots use the same phone number as the key, memory is automatically shared across platforms. If Elora already introduced herself to someone on Telegram, she won't do it again on WhatsApp.

Memory files are local only and excluded from git.

---

## Project structure

```
elora/
  bot.py                   Telegram bot
  whatsapp_bot.js          WhatsApp bot
  contacts.json            Your contacts and config (gitignored)
  contacts.example.json    Template for contacts.json
  profile.example.md       Template for your personal profile
  .env                     Your credentials (gitignored)
  .env.example             Template for .env
  memory/                  Per-contact memory files (gitignored)
  requirements.txt         Python dependencies
  package.json             Node dependencies
```

---

## Notes

- This uses your personal Telegram and WhatsApp accounts, not bot accounts. That means it looks like you're replying. Use it responsibly.
- Telegram requires your phone number and a login code on first run. This creates a local session file so you only have to do it once.
- WhatsApp requires scanning a QR code on first run. Same deal, it saves a local session after that.
- The Gemini system prompt tells Elora when to skip replying, so she won't respond to every single message. This is intentional.
- If you want to adjust her personality or behavior, edit the `ELORA_SYSTEM_PROMPT` string in either bot file.

---

## License

MIT
