"""
TelegramManager: Isolation of the Telegram messaging service.
Resolves concurrency risks and mixing of Asyncio with blocking threads.
"""
import os
import asyncio
import threading
import tempfile
import io
import requests as http_requests
from telegram import Bot, Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from core.jarvis_config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from core.runtime_logger import log_error, log_warning
from utils.jarvis_i18n import get_current_language, get_whisper_lang

class TelegramManager:
    def __init__(self):
        self.app = None
        self.loop = None
        self.thread = None
        self._brain = None
        self._whisper_model = None
        self._tts_engine = None
        self._obs_event = None

    def _text(self, en: str, es: str) -> str:
        return en if get_current_language().startswith("en") else es

    def _whisper_language(self) -> str:
        try:
            return get_whisper_lang(get_current_language())
        except Exception:
            return "en"

    def inject_dependencies(self, brain, whisper_model, tts_engine, obs_event):
        self._brain = brain
        self._whisper_model = whisper_model
        self._tts_engine = tts_engine
        self._obs_event = obs_event

    def _allowed_chat_ids(self) -> set[str]:
        raw = str(TELEGRAM_CHAT_ID or "").strip()
        return {item.strip() for item in raw.split(",") if item.strip()}

    def _chat_allowed(self, update: Update) -> bool:
        allowed = self._allowed_chat_ids()
        if not allowed:
            return False
        chat = getattr(update, "effective_chat", None)
        chat_id = str(getattr(chat, "id", "") or "").strip()
        if not chat_id and getattr(update, "message", None):
            chat_id = str(getattr(update.message, "chat_id", "") or "").strip()
        if chat_id in allowed:
            return True
        log_warning("telegram_unauthorized_chat", chat_id=chat_id or "unknown")
        if self._obs_event:
            self._obs_event("telegram_unauthorized_chat", chat_id=chat_id[:64])
        return False

    async def _texto_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        if not self._chat_allowed(update):
            return
        user_input = update.message.text.strip()
        if self._obs_event:
            self._obs_event("telegram_msg_in", text=user_input[:200])

        try:
            # The brain is synchronous, we run it in a thread to not block the bot loop
            reply, _ = await asyncio.to_thread(self._brain.procesar_mensaje, user_input, profile_id="telegram_user")
            await update.message.reply_text(reply)
        except Exception as e:
            log_error("telegram_text_handler_failed", error=str(e))
            await update.message.reply_text(
                self._text(
                    "Sorry, Administrator. My messaging core failed.",
                    "Lo siento, Administrador. Mi núcleo de mensajería ha fallado.",
                )
            )

    async def _voz_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.voice or not self._whisper_model:
            return
        if not self._chat_allowed(update):
            return
        if self._obs_event:
            self._obs_event("telegram_voice_in")

        try:
            file = await update.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                ogg_path = tmp.name

            await file.download_to_drive(ogg_path)

            # Whisper is intensive, we run it in a thread
            def _transcribir():
                segments, _ = self._whisper_model.transcribe(
                    ogg_path, language=self._whisper_language(), vad_filter=True, beam_size=3
                )
                return " ".join([s.text for s in segments]).strip()

            try:
                text = await asyncio.to_thread(_transcribir)
            finally:
                if os.path.exists(ogg_path):
                    os.remove(ogg_path)

            if not text:
                await update.message.reply_text(
                    self._text(
                        "I could not understand the audio, Administrator.",
                        "No pude entender el audio, Administrador.",
                    )
                )
                return

            reply, _ = await asyncio.to_thread(self._brain.procesar_mensaje, text, profile_id="telegram_user")
            await update.message.reply_text(f"📝 {text}\n\n{reply}")
        except Exception as e:
            log_error("telegram_voice_handler_failed", error=str(e))
            await update.message.reply_text(
                self._text("Error processing your voice message.", "Error procesando su mensaje de voz.")
            )

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        log_error("telegram_runtime_error", error=str(context.error))

    def start(self):
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            log_warning("telegram_not_configured")
            return

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._texto_handler))
        self.app.add_handler(MessageHandler(filters.VOICE, self._voz_handler))
        self.app.add_error_handler(self._error_handler)

        from utils.jarvis_i18n import get_bt
        bt = get_bt()
        log_warning("telegram_polling_start", detail=bt["log_telegram_active"])
        # We use run_polling so it doesn't try to catch signals (close_loop=False if necessary)
        # but in a daemon thread, run_polling is usually enough if there are no signal conflicts.
        self.app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)

    def send_message_sync(self, texto: str, audio: bool = True) -> bool:
        """Synchronous (legacy) sending compatible with the rest of the system."""
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            return False
        try:
            from utils.jarvis_text import normalizar_tratamiento_admin
            texto_limpio = normalizar_tratamiento_admin((texto or "").strip())
            texto_para_leer = self._brain._limpiar_metadatos_voz(texto_limpio)

            if audio and self._tts_engine:
                from pydub import AudioSegment
                audio_bytes = self._tts_engine.sintetizar(texto_para_leer[:600])
                with io.BytesIO(audio_bytes) as wav_buf:
                    snd = AudioSegment.from_wav(wav_buf)
                    with io.BytesIO() as ogg_buf:
                        snd.export(ogg_buf, format="opus", codec="libopus")
                        ogg_buf.seek(0)
                        url_voice = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice"
                        http_requests.post(
                            url_voice,
                            data={"chat_id": TELEGRAM_CHAT_ID},
                            files={"voice": ("jarvis.ogg", ogg_buf, "audio/ogg")},
                            timeout=15,
                        )
                        return True

            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            http_requests.post(
                url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto_para_leer}, timeout=5
            )
            return True
        except Exception as e:
            log_error("telegram_send_failed", error=str(e))
            return False

telegram_manager = TelegramManager()
