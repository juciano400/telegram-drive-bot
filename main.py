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
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
DRIVE_FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]
SERVICE_ACCOUNT_JSON = os.environ["SERVICE_ACCOUNT_JSON"]

def get_drive_service():
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.file"]
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
    genai.configure(api_key=GEMINI_API_KEY)
    gemini = genai.GenerativeModel("gemini-1.5-flash")
    size_kb = size_bytes / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.2f} MB"
    prompt = (
        f"Um arquivo foi salvo no Google Drive com sucesso.\n"
        f"Nome: {filename}\nTipo: {mime_type}\nTamanho: {size_str}\n\n"
        f"Gere uma confirmacao amigavel e breve em portugues (maximo 4 linhas), "
        f"mencionando o nome, tipo e tamanho. Use um emoji adequado ao tipo de arquivo."
    )
    return gemini.generate_content(prompt).text.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.effective_user.first_name or "usuario"
    await update.message.reply_text(
        f"Ola, {nome}! Tudo bem?\n\n"
        f"Eu sou seu assistente de arquivos pessoal.\n\n"
        f"Me envie qualquer arquivo - PDF, foto, video, audio, documento - "
        f"e eu salvo automaticamente na sua pasta do Google Drive.\n\n"
        f"Pode mandar o primeiro arquivo quando quiser!"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    file_obj = mime_type = filename = None

    if msg.document:
        file_obj  = msg.document
        filename  = msg.document.file_name or "arquivo"
        mime_type = msg.document.mime_type or "application/octet-stream"
    elif msg.photo:
        file_obj  = msg.photo[-1]
        filename  = f"foto_{msg.message_id}.jpg"
        mime_type = "image/jpeg"
    elif msg.video:
        file_obj  = msg.video
        filename  = msg.video.file_name or f"video_{msg.message_id}.mp4"
        mime_type = msg.video.mime_type or "video/mp4"
    elif msg.audio:
        file_obj  = msg.audio
        filename  = msg.audio.file_name or f"audio_{msg.message_id}.mp3"
        mime_type = msg.audio.mime_type or "audio/mpeg"
    elif msg.voice:
        file_obj  = msg.voice
        filename  = f"voice_{msg.message_id}.ogg"
        mime_type = "audio/ogg"
    else:
        await msg.reply_text("Tipo de arquivo nao suportado. Tente enviar como documento.")
        return

    aviso = await msg.reply_text("Fazendo upload para o Google Drive... Aguarde!")

    try:
        tg_file    = await context.bot.get_file(file_obj.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        uploaded   = upload_to_drive(file_bytes, filename, mime_type)
        gemini_text = analyze_file(filename, mime_type, len(file_bytes))
        link = uploaded.get("webViewLink", "")
        await aviso.delete()
        await msg.reply_text(
            f"Upload concluido com sucesso!\n\n"
            f"{gemini_text}\n\n"
            f"Link: {link}"
        )
    except Exception as e:
        logger.exception("Erro no upload")
        await aviso.delete()
        await msg.reply_text(f"Ops, algo deu errado!\nErro: {str(e)}")

app_flask = Flask(__name__)
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(
    filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
    handle_file
))

@app_flask.post("/webhook")
async def webhook():
    data   = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.initialize()
    await application.process_update(update)
    return jsonify({"ok": True})

@app_flask.get("/")
def health():
    return "OK", 200

if __name__ == "__main__":
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
