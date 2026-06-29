import os
import io
import base64
import threading
from datetime import date

import requests
import fitz  # PyMuPDF
from PIL import Image
from flask import Flask, request, jsonify
from openai import OpenAI

# ------------------------------------------------------------------
# Config — all pulled from Render environment variables
# ------------------------------------------------------------------
GITHUB_TOKEN        = os.environ["GITHUB_TOKEN"]
TELEGRAM_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET      = os.environ.get("WEBHOOK_SECRET", "changeme")

# --- Email via Resend (HTTPS API — works on Render's free tier) ---
RESEND_API_KEY      = os.environ["RESEND_API_KEY"]
# When using the free test sender, RECIPIENT_EMAIL MUST be the email you
# signed up to Resend with. Verify your own domain to send elsewhere.
RECIPIENT_EMAIL     = os.environ["RECIPIENT_EMAIL"]
SENDER_EMAIL        = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")  # auto-set by Render

MODEL  = "gpt-4o"
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ------------------------------------------------------------------
# Access control
# ------------------------------------------------------------------
# Lock the bot to your own Telegram account. Message @userinfobot to get
# your numeric chat ID, then put it here. Leave EMPTY ( = set() ) to allow anyone.
ALLOWED_CHAT_IDS = {6016323640}   # <-- REPLACE 123456789 with your chat ID

# Cap how many generations can run per day (protects your free quota).
DAILY_LIMIT = 10

client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=GITHUB_TOKEN)
app = Flask(__name__)

_usage_lock = threading.Lock()
_usage = {"day": None, "count": 0}

def is_allowed(chat_id):
    return (not ALLOWED_CHAT_IDS) or (chat_id in ALLOWED_CHAT_IDS)

def under_daily_limit():
    with _usage_lock:
        today = date.today()
        if _usage["day"] != today:
            _usage["day"], _usage["count"] = today, 0
        if _usage["count"] >= DAILY_LIMIT:
            return False
        _usage["count"] += 1
        return True

PROMPT = """
Observe the provided images/documents. Identify the core topic, book, or subject matter.
Generate a minimum of 15 vocabulary terms and their definitions related to this upload.

CRITICAL FORMATTING RULES:
1. You MUST use "question" for the definition and "term" for the vocabulary word.
2. Every single term MUST be in ALL CAPS.
3. Do NOT include markdown blocks like ```javascript or ```text. Output raw text only.
4. Your output must be in two exact parts.

PART 1 (The JavaScript Array):
const vocabData = [
    { question: "Your definition goes here.", term: "TERM1" },
    { question: "Your definition goes here.", term: "TERM2" }
];

PART 2 (The plain text list):
Leave two blank lines after the array, then output just the terms, one on each line, in ALL CAPS.

Example of Part 2:
TERM1
TERM2
TERM3

Do not add any greetings, explanations, or extra text. Only provide Part 1 and Part 2.
""".strip()

# ------------------------------------------------------------------
# Telegram helpers
# ------------------------------------------------------------------
def tg_send_message(chat_id, text):
    requests.post(f"{TG_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def tg_get_file_path(file_id):
    r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id})
    return r.json()["result"]["file_path"]

def tg_download(file_path):
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    return requests.get(url).content

# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------
def file_to_base64_images(file_bytes, filename):
    images = []
    name = filename.lower()

    if name.endswith(".pdf"):
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            if len(images) >= 40:      # safety cap
                break
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            images.append(base64.b64encode(pix.tobytes("jpeg")).decode("utf-8"))
        doc.close()

    elif name.endswith((".png", ".jpg", ".jpeg")):
        Image.open(io.BytesIO(file_bytes)).verify()  # validate it's a real image
        images.append(base64.b64encode(file_bytes).decode("utf-8"))

    else:
        raise ValueError(f"Unsupported file type: {filename}")

    return images

def generate_vocab(base64_images):
    content = [{"type": "text", "text": PROMPT}]
    for b64 in base64_images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.3,
        max_tokens=3000,
    )
    out = resp.choices[0].message.content.strip()
    for junk in ["```javascript\n", "```text\n", "```\n", "```"]:
        out = out.replace(junk, "")
    return out.strip()

def send_email(subject, body):
    """Send via Resend's HTTPS API (port 443) — not SMTP, so it works on free Render."""
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": SENDER_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Email send failed ({resp.status_code}): {resp.text}")

# ------------------------------------------------------------------
# Background worker — keeps the webhook response instant
# ------------------------------------------------------------------
def process_file(chat_id, file_id, filename):
    try:
        path = tg_get_file_path(file_id)
        data = tg_download(path)
        images = file_to_base64_images(data, filename)
        if not images:
            tg_send_message(chat_id, "Couldn't read any pages/images from that file.")
            return
        vocab = generate_vocab(images)
        send_email(f"Vocab: {filename}", vocab)
        tg_send_message(chat_id, f"\u2705 Done \u2014 vocab for \u201c{filename}\u201d sent to your email.")
    except Exception as e:
        tg_send_message(chat_id, f"\u26a0\ufe0f Error: {e}")

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.route("/")
def health():
    return "Vocab bot alive", 200

@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("channel_post")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]

    if not is_allowed(chat_id):
        return jsonify(ok=True)

    file_id, filename = None, "upload"

    if "document" in message:                       # PDF or image sent as a file
        file_id = message["document"]["file_id"]
        filename = message["document"].get("file_name", "upload.pdf")
    elif "photo" in message:                         # image sent as a photo (compressed)
        file_id = message["photo"][-1]["file_id"]    # [-1] = highest resolution
        filename = "photo.jpg"
    elif "text" in message:
        tg_send_message(
            chat_id,
            "Send me a PDF or image (PNG/JPG) and I'll email you the vocab.\n"
            "Tip: send images as a *file* (not a photo) for the sharpest OCR."
        )
        return jsonify(ok=True)

    if file_id:
        if not under_daily_limit():
            tg_send_message(chat_id, f"Daily limit of {DAILY_LIMIT} reached. Try again tomorrow.")
            return jsonify(ok=True)
        tg_send_message(chat_id, "\U0001f4e5 Got it \u2014 analyzing and generating vocab\u2026")
        threading.Thread(
            target=process_file, args=(chat_id, file_id, filename), daemon=True
        ).start()

    return jsonify(ok=True)

# ------------------------------------------------------------------
# Register the webhook with Telegram on boot
# ------------------------------------------------------------------
def set_webhook():
    if RENDER_EXTERNAL_URL:
        url = f"{RENDER_EXTERNAL_URL}/webhook/{WEBHOOK_SECRET}"
        requests.get(f"{TG_API}/setWebhook", params={"url": url})

set_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
