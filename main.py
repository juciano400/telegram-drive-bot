import os
import io
import logging
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
DRIVE_FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
SERVICE_ACCOUNT_FILE = "/app/service_account.json"
ALLOWED_USER_IDS = set(
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
)

genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel("gemini-1.5-flash")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=creds)

def upload_to_drive(file_bytes, filename, mime_type):
    service = get_drive_service()
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    uploaded = service.files().create(
        body=file_metadata, media_body=media,
        fields="id, name, size, mimeType, webViewLink"
    ).execute()
    return uploaded

def analyze_file(filename, mime_type, size_bytes):
    size_kb = size_bytes / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
    prompt = (
        f"Um arquivo foi salvo no Google Drive com sucesso.\n"
        f"Nome: {filename}\nTipo: {mime_type}\nTamanho: {size_str}\n\n"
        f"Gere uma confirmação amigável e breve em português (máximo 4 linhas), "
        f"mencionando o nome, tipo e tamanho. Use um emoji adequado ao tipo de arquivo."
    )
    return gemini.generate_content(prompt).text.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.effective_user.first_name or "por aí"
    await update.message.reply_text(
        f"👋 Olá, {nome}! Tudo bem?\n\n"
        f"Eu sou seu assistente de arquivos pessoal. 📂\n\n"
        f"É simples assim: me envie qualquer arquivo — PDF, foto, vídeo, áudio, documento — "
        f"e eu salvo automaticamente na sua pasta do Google Drive, sem complicação.\n\n"
        f"Pode mandar o primeiro arquivo quando quiser! 🚀"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("⛔ Você não tem permissão para usar este bot.")
        return

    msg = update.message
    file_obj = mime_type = filename = None

    if msg.document:
        file_obj, filename, mime_type = msg.document, msg.document.file_name or "arquivo", msg.document.mime_type or "application/octet-stream"
    elif msg.photo:
        file_obj, filename, mime_type = msg.photo[-1], f"foto_{msg.message_id}.jpg", "image/jpeg"
    elif msg.video:
        file_obj, filename, mime_type = msg.video, msg.video.file_name or f"video_{msg.message_id}.mp4", msg.video.mime_type or "video/mp4"
    elif msg.audio:
        file_obj, filename, mime_type = msg.audio, msg.audio.file_name or f"audio_{msg.message_id}.mp3", msg.audio.mime_type or "audio/mpeg"
    elif msg.voice:
        file_obj, filename, mime_type = msg.voice, f"voice_{msg.message_id}.ogg", "audio/ogg"
    else:
        await msg.reply_text("❓ Tipo de arquivo não suportado. Tente enviar como documento.")
        return

    aviso = await msg.reply_text("📤 Fazendo upload para o Google Drive...\nAguarde um instante!")

    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        uploaded = upload_to_drive(file_bytes, filename, mime_type)
        gemini_text = analyze_file(filename, mime_type, len(file_bytes))
        link = uploaded.get("webViewLink", "")

        await aviso.delete()
        await msg.reply_text(
            f"✅ *Upload concluído com sucesso!*\n\n"
            f"{gemini_text}\n\n"
            f"🔗 [Abrir no Google Drive]({link})",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Erro no upload")
        await aviso.delete()
        await msg.reply_text(
            f"❌ *Ops, algo deu errado!*\n\n"
            f"Não consegui fazer o upload deste arquivo.\n"
            f"Erro: `{str(e)}`",
            parse_mode="Markdown"
        )

app_flask = Flask(__name__)
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(
    filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
    handle_file
))

@app_flask.post("/webhook")
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.initialize()
    await application.process_update(update)
    return jsonify({"ok": True})

@app_flask.get("/")
def health():
    return "OK", 200

if __name__ == "__main__":
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
