import io
import json
import os
import re
import sys
import tempfile
import threading
import wave

from core.jarvis_observability import obs_event
from utils.jarvis_tts_lexicon import TTS_PRONUN_DEFAULT

IS_WINDOWS = sys.platform == "win32"

# This configuration assumes eSpeak is installed here in Windows, customizable via .env
if IS_WINDOWS:
    ESPEAK_ROOT = os.getenv("ESPEAK_ROOT", r"C:\Program Files\eSpeak NG")
    if os.path.exists(ESPEAK_ROOT):
        os.environ["PATH"] += os.pathsep + ESPEAK_ROOT
        os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = os.path.join(ESPEAK_ROOT, "libespeak-ng.dll")
        os.environ["ESPEAK_DATA_PATH"] = ESPEAK_ROOT
else:
    # Unix-like systems usually have espeak-ng in standard paths like /usr/lib/libespeak-ng.so
    ESPEAK_ROOT = os.getenv("ESPEAK_ROOT", "/usr")
    # Si el usuario configura ESPEAK_ROOT, le hacemos caso; si no, dejamos que phonemizer lo encuentre
    if "ESPEAK_ROOT" in os.environ and os.path.exists(ESPEAK_ROOT):
        # phonemizer auto-detects libespeak-ng.so or dylib on linux/mac if in PATH
        os.environ["ESPEAK_DATA_PATH"] = ESPEAK_ROOT

from piper.config import SynthesisConfig
from piper.voice import PiperVoice

try:
    from rvc_python.infer import RVCInference

    HAVE_RVC = True
except ImportError:
    HAVE_RVC = False


class TTSEngine:
    def __init__(self, model_path, pronun_file, reparar_unicode_func):
        self.model_path = model_path
        self.pronun_file = pronun_file
        self.reparar_unicode = reparar_unicode_func
        self.voice = None
        self.rvc = None
        self.speaker_id = None  # Automatically detected in _init_piper

        self.rvc_path = os.getenv(
            "RVC_MODEL_PATH",
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "models",
                "rvc_voice",
                "JARVIS_98e_4704s.pth",
            ),
        )
        self.rvc_index = os.getenv(
            "RVC_INDEX_PATH",
            os.path.join(os.path.dirname(self.rvc_path), "JARVIS.index"),
        )

        self.tts_lock = threading.Lock()
        self.synthesis_lock = threading.Lock()

        self.tts_pronun_map = {}
        self.tts_pronun_default = dict(TTS_PRONUN_DEFAULT)

        self._init_piper()
        self.cargar_reglas()

    def _segmentar_texto(self, texto: str, max_chars: int = 170) -> list:
        t = self.reparar_unicode(str(texto or ""))
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return []
        if len(t) <= max_chars:
            return [t]

        frases = re.split(r"(?<=[.!?])\s+", t)
        out = []
        cur = ""
        for fr in frases:
            fr = fr.strip()
            if not fr:
                continue
            if len(fr) > max_chars:
                words = fr.split()
                tmp = ""
                for w in words:
                    cand = (tmp + " " + w).strip()
                    if len(cand) <= max_chars:
                        tmp = cand
                    else:
                        if tmp:
                            out.append(tmp)
                        tmp = w
                if tmp:
                    out.append(tmp)
                continue

            cand = (cur + " " + fr).strip() if cur else fr
            if len(cand) <= max_chars:
                cur = cand
            else:
                if cur:
                    out.append(cur)
                cur = fr
        if cur:
            out.append(cur)
        return out

    def _init_piper(self):
        print(f"[TTS] Loading Piper voice engine from {self.model_path}...")
        try:
            self.voice = PiperVoice.load(self.model_path)
            # Detect multi-speaker models and use speaker_id=0
            if self.voice.config.num_speakers > 1:
                self.speaker_id = 0
                print(
                    f"[TTS] Multi-speaker model detected ({self.voice.config.num_speakers} voices). Using speaker_id={self.speaker_id}."
                )
            else:
                self.speaker_id = None
            print(
                f"[TTS] Base voice loaded (sample_rate={self.voice.config.sample_rate})."
            )
        except Exception as e:
            print(f"[ERROR TTS] Could not load Piper: {e}")

    def reload_model(self, new_model_path: str) -> bool:
        """Hot-swap the TTS voice model at runtime. Returns True on success."""
        if not os.path.exists(new_model_path):
            print(f"[TTS] Model file not found: {new_model_path}")
            return False
        with self.synthesis_lock:
            old_path = self.model_path
            try:
                self.model_path = new_model_path
                self.voice = PiperVoice.load(self.model_path)
                if self.voice.config.num_speakers > 1:
                    self.speaker_id = 0
                else:
                    self.speaker_id = None
                print(f"[TTS] Voice model hot-swapped: {os.path.basename(new_model_path)}")
                return True
            except Exception as e:
                print(f"[ERROR TTS] Hot-swap failed, reverting: {e}")
                try:
                    self.model_path = old_path
                    self.voice = PiperVoice.load(old_path)
                except Exception as e2:
                    print(f"[CRITICAL TTS] Revert also failed: {e2}")
                return False

        print("[TTS] Initializing voice clone (RVC)...")
        use_rvc = os.getenv("JARVIS_USE_RVC", "false").lower() == "true"
        if HAVE_RVC and os.path.exists(self.rvc_path) and use_rvc:
            try:
                self.rvc = RVCInference(device="cpu")
                idx = self.rvc_index if os.path.exists(self.rvc_index) else ""
                self.rvc.load_model(self.rvc_path, version="v2", index_path=idx)
                self.rvc.set_params(
                    f0method="rmvpe",
                    f0up_key=0,
                    index_rate=0.8,
                    filter_radius=3,
                    rms_mix_rate=0.2,
                    protect=0.33,
                )
                print("[TTS] JARVIS voice (.pth) loaded and ready to speak!")
            except Exception as e:
                print(f"[ERROR TTS] Could not initialize RVC: {e}")
                self.rvc = None
        else:
            print(
                "[TTS] RVC not detected or .pth file missing. JARVIS will speak with generic Piper voice."
            )

    def cargar_reglas(self):
        loaded = {}
        if os.path.exists(self.pronun_file):
            try:
                with open(self.pronun_file, encoding="utf-8") as f:
                    loaded = json.load(f) or {}
            except Exception as e:
                print(f"[TTS] Could not read pronunciation rules: {e}")

        base = dict(self.tts_pronun_default)
        for k, v in loaded.items():
            key = self.reparar_unicode(str(k or "")).strip().lower()
            val = self.reparar_unicode(str(v or "")).strip()
            if key and val:
                base[key] = val

        with self.tts_lock:
            self.tts_pronun_map = base

    def reset_reglas(self):
        with self.tts_lock:
            self.tts_pronun_map.clear()
            self.tts_pronun_map.update(dict(self.tts_pronun_default))
            snapshot = dict(self.tts_pronun_map)

        try:
            with open(self.pronun_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TTS] Error saving reset: {e}")
        return snapshot

    def update_reglas(self, reglas_norm: dict, replace: bool = False):
        with self.tts_lock:
            if replace:
                self.tts_pronun_map.clear()
            self.tts_pronun_map.update(reglas_norm)
            snapshot = dict(self.tts_pronun_map)

        try:
            with open(self.pronun_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TTS] Error saving rules: {e}")
        return snapshot

    def aplicar_pronunciacion(self, texto: str) -> str:
        salida = self.reparar_unicode(str(texto or ""))
        with self.tts_lock:
            reglas = dict(self.tts_pronun_map)
        if not reglas:
            return salida

        def _match_pattern(raw: str) -> str:
            return rf"(?i)(?<![a-z0-9áéíóúñü]){re.escape(raw)}(?![a-z0-9áéíóúñü])"

        for src in sorted(reglas.keys(), key=len, reverse=True):
            dst = reglas.get(src, "")
            if not dst:
                continue

            def _replace(m, _dst=dst):
                found = m.group(0)
                if found.isupper():
                    return _dst.upper()
                if found[:1].isupper():
                    return _dst[:1].upper() + _dst[1:]
                return _dst

            salida = re.sub(_match_pattern(src), _replace, salida)
        return salida

    def sintetizar(self, texto: str) -> bytes:
        if not self.voice:
            raise RuntimeError("The Piper engine is not loaded.")

        texto_limpio = self.aplicar_pronunciacion(texto)
        # Sanitize minimal or empty inputs to avoid wave writer errors.
        if not texto_limpio or len(texto_limpio.strip()) < 2:
            texto_limpio = "Understood."
        if len(texto_limpio) > 900:
            texto_limpio = texto_limpio[:900].rsplit(" ", 1)[0].strip()

        segmentos = self._segmentar_texto(texto_limpio, max_chars=170)
        if not segmentos:
            raise RuntimeError("Empty TTS text.")

        # Synthesis configuration with speaker_id for multi-speaker models
        syn_cfg = (
            SynthesisConfig(speaker_id=self.speaker_id) if self.speaker_id is not None else None
        )

        import time as _t

        t_lock = _t.time()
        if not self.synthesis_lock.acquire(timeout=8):
            raise RuntimeError(
                f"synthesis_lock occupied for {_t.time() - t_lock:.1f}s (another thread synthesizing)"
            )
        print(f"[TTS Engine] Lock acquired in {_t.time() - t_lock:.2f}s, {len(segmentos)} segments")
        try:
            buf_out = io.BytesIO()
            wav_writer = None

            try:
                for seg in segmentos:
                    if not seg or len(seg.strip()) < 2:
                        continue
                    seg_buf = io.BytesIO()
                    seg_wav = wave.open(seg_buf, "wb")
                    # Minimum header to avoid `# channels not specified`
                    # if Piper does not write frames for a short segment.
                    seg_wav.setnchannels(1)
                    seg_wav.setsampwidth(2)
                    seg_wav.setframerate(self.voice.config.sample_rate)

                    try:
                        # synthesize_wav writes frames and configures the WAV format
                        self.voice.synthesize_wav(seg, seg_wav, syn_config=syn_cfg)
                    except Exception as e_seg:
                        print(f"[ERROR Piper Segment] {e_seg}")
                        try:
                            seg_wav.close()
                        except Exception:
                            pass
                        continue
                    else:
                        try:
                            seg_wav.close()
                        except Exception as e_close_seg:
                            print(f"[ERROR Closing Segment] {e_close_seg}")
                            continue

                    # Read the generated segment to join it to the main buffer
                    seg_buf.seek(0)
                    try:
                        seg_reader = wave.open(seg_buf, "rb")
                        try:
                            if wav_writer is None:
                                wav_writer = wave.open(buf_out, "wb")
                                wav_writer.setnchannels(seg_reader.getnchannels())
                                wav_writer.setsampwidth(seg_reader.getsampwidth())
                                wav_writer.setframerate(seg_reader.getframerate())

                            wav_writer.writeframes(seg_reader.readframes(seg_reader.getnframes()))
                        finally:
                            seg_reader.close()
                    except Exception as e_read:
                        print(f"[ERROR Reading Segment] {e_read}")

            finally:
                if wav_writer is not None:
                    try:
                        wav_writer.close()
                    except Exception as e_close:
                        print(f"[ERROR Closing wav_writer] {e_close}")

            if wav_writer is None:
                raise RuntimeError("Piper did not generate audio for the given text.")

            buf_out.seek(0)
            audio_bytes = buf_out.read()
        finally:
            self.synthesis_lock.release()
            print(
                f"[TTS Engine] Lock released, audio: {len(audio_bytes) if 'audio_bytes' in locals() else 0} bytes"
            )
        # --- RVC block (optional, if active) ---
        if self.rvc:
            input_wav = None
            output_wav = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_input:
                    tmp_input.write(audio_bytes)
                    input_wav = tmp_input.name

                output_wav = input_wav.replace(".wav", "_jarvis.wav")
                self.rvc.infer_file(input_path=input_wav, output_path=output_wav)

                if os.path.exists(output_wav):
                    with open(output_wav, "rb") as f:
                        return f.read()
                raise FileNotFoundError("RVC did not produce an output file.")
            except Exception as e:
                obs_event("rvc_inference_error", error=str(e)[:300])
                print(f"[ERROR RVC] Using Piper's base voice: {e}")
                return audio_bytes
            finally:
                try:
                    if input_wav and os.path.exists(input_wav):
                        os.remove(input_wav)
                    if output_wav and os.path.exists(output_wav):
                        os.remove(output_wav)
                except Exception as cleanup_err:
                    obs_event("rvc_cleanup_error", error=str(cleanup_err)[:300])

        return audio_bytes
