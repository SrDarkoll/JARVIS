"""Motor biométrico de identificación de voz.

Encapsula Speechbrain ECAPA-VoxCeleb para embeddings de speaker,
gestión de perfiles en SQLite y matching por similitud coseno.
"""

from __future__ import annotations

# Re-exporta todo desde voice_id para compatibilidad con imports existentes.
# La lógica real vive aquí — voice_id.py es solo un wrapper de 2 líneas.
import io
import os
import sqlite3
import tempfile
import threading
import time as _time
import traceback

import numpy as np
import soundfile as sf
from core.jarvis_config import CACHE_DIR, RUNTIME_DIR, VOICE_ID_ENABLED
from core.jarvis_observability import obs_event
from core.jarvis_state import DEFAULT_PROFILE_ID
from utils.audio_conversion import AudioConversionError, convert_to_wav
from utils.jarvis_i18n import get_bt

VOICE_ID_DISPONIBLE = False
SpeakerRecognition = None

if os.getenv("JARVIS_TEST_MODE") == "1":
    print("[VOICE_ID] Disabled in test mode.")
elif not VOICE_ID_ENABLED:
    print("[VOICE_ID] Disabled by runtime configuration.")
else:
    try:
        from speechbrain.inference import SpeakerRecognition as _SpeakerRecognition

        SpeakerRecognition = _SpeakerRecognition

        VOICE_ID_DISPONIBLE = True
        print("[VOICE_ID] Speechbrain ECAPA-VoxCeleb available.")
    except ImportError as e:
        print(f"[VOICE_ID] Speechbrain no available: {e}")

OWNER_PID = DEFAULT_PROFILE_ID  # Alias for clarity in biometric context

DB_PATH = os.getenv("JARVIS_DB_PATH") or os.path.join(RUNTIME_DIR, "memoria_jarvis.db")
os.makedirs(RUNTIME_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
_lock = threading.Lock()

# =========================================================
# UMBRALES — COSINE SIMILARITY
# =========================================================
UMBRAL_SIMILITUD = float((os.getenv("VOICE_ID_THRESHOLD") or "0.35").strip() or "0.35")
UMBRAL_DIFERENCIA_TOP2 = float((os.getenv("VOICE_ID_TOP2_GAP") or "0.05").strip() or "0.05")
# 0.25 para el Administrador - ajustado por rendimiento real
UMBRAL_ADMIN_DIRECTO = float((os.getenv("VOICE_ID_OWNER_THRESHOLD") or "0.32").strip() or "0.32")

MIN_AUDIO_DURATION_MS = 200
MIN_AUDIO_ENERGY = 0.003
VOICE_ACTIVITY_THRESHOLD = 0.05
MAX_EMBEDDINGS_PER_PROFILE = 10


class VoiceIdentifier:
    def __init__(self):
        self.encoder = None
        self.perfiles_voz: dict = {}
        self._available = VOICE_ID_DISPONIBLE
        self._ultimo_candidato: tuple[str | None, str | None, float] = (None, None, 0.0)
        self._ultimo_debug: dict = {}

        if not self._available:
            print("[VOICE_ID] Modelo de voz no available.")
            return

        threading.Thread(target=self._init_bg, daemon=True).start()

    def _init_bg(self):
        try:
            self._init_db()
            self.encoder = SpeakerRecognition.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(CACHE_DIR, "speechbrain-ecapa"),
            )
            self._encoder_ready = True
            self._cargar_perfiles()
            bt = get_bt()
            print(bt["log_voice_ready"].format(count=len(self.perfiles_voz)))
        except Exception as e:
            print(f"[VOICE_ID] Error initializing Speechbrain: {e}")
            obs_event("voice_id_init_error", error=str(e)[:300])
            self._available = False

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_profiles (
                profile_id TEXT PRIMARY KEY,
                nombre TEXT,
                embedding BLOB,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()

    def _cargar_perfiles(self):
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT profile_id, nombre, embedding FROM voice_profiles").fetchall()
        conn.close()

        loaded_profiles = {}
        with _lock:
            for pid, nombre, emb_blob in rows:
                if emb_blob:
                    emb = np.frombuffer(emb_blob, dtype=np.float32)
                    try:
                        conn2 = sqlite3.connect(DB_PATH)
                        n = conn2.execute(
                            "SELECT COUNT(*) FROM voice_embeddings WHERE profile_id=?", (pid,)
                        ).fetchone()[0]
                        conn2.close()
                    except Exception:
                        n = 1
                    loaded_profiles[pid] = {"nombre": nombre, "embedding": emb, "n_samples": n}

        self.perfiles_voz = loaded_profiles

        if OWNER_PID not in self.perfiles_voz:
            bt = get_bt()
            print(bt["log_voice_owner_not_found"])
        else:
            n = self.perfiles_voz[OWNER_PID].get("n_samples", 1)
            print(f"[VOICE_ID] Owner profile loaded with {n} sample(s).")
            if n < 3:
                print(
                    "[VOICE_ID] AVISO: El owner tiene menos de 3 samples. "
                    "Usa el panel → 'Registrar voz del owner' para mejorar el reconocimiento."
                )

        # Limpieza automática de invitados sin actividad en 30 días
        self._limpiar_perfiles_viejos(dias=30)

        # Protección: si owner desapareció de memoria pero está en DB, recuperarlo
        if OWNER_PID not in self.perfiles_voz:
            try:
                conn_check = sqlite3.connect(DB_PATH)
                row = conn_check.execute(
                    "SELECT profile_id, nombre, embedding FROM voice_profiles WHERE profile_id=?", (OWNER_PID,)
                ).fetchone()
                conn_check.close()
                if row and row[2]:
                    emb = np.frombuffer(row[2], dtype=np.float32)
                    with _lock:
                        self.perfiles_voz[OWNER_PID] = {
                            "nombre": row[1],
                            "embedding": emb,
                            "n_samples": 0,
                        }
                    print("[VOICE_ID] Owner profile recovered after cleanup.")
            except Exception as e_recover:
                print(f"[VOICE_ID] Could not recover owner profile: {e_recover}")

    def get_ultimo_candidato(self) -> tuple[str | None, str | None, float]:
        """Retorna el mejor candidato del último identificar(), aunque esté bajo el umbral."""
        with _lock:
            if hasattr(self, "_ultimo_candidato"):
                return self._ultimo_candidato
        return None, None, 0.0

    def get_ultimo_debug(self) -> dict:
        """Retorna metadatos de la última evaluación biométrica."""
        with _lock:
            if hasattr(self, "_ultimo_debug") and isinstance(self._ultimo_debug, dict):
                return dict(self._ultimo_debug)
        return {}

    def identificar(self, audio_bytes: bytes) -> tuple[str | None, str | None, float]:
        """Retorna (profile_id, nombre, similitud_coseno)."""
        if not isinstance(audio_bytes, (bytes, bytearray)):
            with _lock:
                self._ultimo_debug = {
                    "decision": "invalid_audio_type",
                    "profiles_evaluated": 0,
                }
            return None, None, 0.0
        if not self._wait_encoder_ready(timeout=3.0):
            with _lock:
                self._ultimo_debug = {
                    "decision": "encoder_not_ready",
                    "profiles_evaluated": 0,
                }
            return None, None, 0.0
        try:
            emb = self._embedding_desde_audio_bytes(audio_bytes)
            if emb is None:
                with _lock:
                    self._ultimo_debug = {
                        "decision": "embedding_failed",
                        "profiles_evaluated": 0,
                    }
                return None, None, 0.0

            matches = []
            with _lock:
                for pid, datos in self.perfiles_voz.items():
                    ref = datos["embedding"]
                    sim = float(np.dot(emb, ref) / (np.linalg.norm(emb) * np.linalg.norm(ref) + 1e-8))
                    matches.append((pid, datos["nombre"], sim))

            if not matches:
                with _lock:
                    self._ultimo_candidato = (None, None, 0.0)
                with _lock:
                    self._ultimo_debug = {
                        "decision": "no_profiles",
                        "profiles_evaluated": 0,
                    }
                obs_event("voice_similarity_scored", decision="no_profiles", profiles_evaluated=0)
                return None, None, 0.0

            matches.sort(key=lambda x: x[2], reverse=True)
            mejor_pid, mejor_nombre, mejor_sim = matches[0]
            segundo_pid, segundo_nombre, segunda_sim = matches[1] if len(matches) > 1 else (None, None, 0.0)

            # Guardar candidato para soft match / session continuity
            with _lock:
                self._ultimo_candidato = (mejor_pid, mejor_nombre, mejor_sim)

            print(f"[BIO] Best match: {mejor_nombre} (sim={mejor_sim:.4f})")

            aceptado = False
            accepted_pid = None
            accepted_nombre = None
            decision = "below_threshold"

            if mejor_pid == OWNER_PID and mejor_sim >= UMBRAL_ADMIN_DIRECTO:
                aceptado = True
                accepted_pid = mejor_pid
                accepted_nombre = mejor_nombre
                decision = "owner_direct"

            gap = mejor_sim - segunda_sim
            if not aceptado and mejor_sim >= UMBRAL_SIMILITUD and gap >= UMBRAL_DIFERENCIA_TOP2:
                aceptado = True
                accepted_pid = mejor_pid
                accepted_nombre = mejor_nombre
                decision = "main_threshold"

            with _lock:
                self._ultimo_debug = {
                    "decision": decision,
                    "accepted": bool(aceptado),
                    "accepted_profile_id": accepted_pid,
                    "accepted_nombre": accepted_nombre,
                    "top_profile_id": mejor_pid,
                    "top_nombre": mejor_nombre,
                    "top_sim": float(mejor_sim),
                    "second_profile_id": segundo_pid,
                    "second_nombre": segundo_nombre,
                    "second_sim": float(segunda_sim),
                    "top2_gap": float(gap),
                    "threshold_main": float(UMBRAL_SIMILITUD),
                    "threshold_owner": float(UMBRAL_ADMIN_DIRECTO),
                    "threshold_gap": float(UMBRAL_DIFERENCIA_TOP2),
                    "profiles_evaluated": int(len(matches)),
                }

            obs_event(
                "voice_similarity_scored",
                decision=decision,
                accepted=bool(aceptado),
                accepted_profile_id=accepted_pid or "",
                top_profile_id=mejor_pid,
                top_nombre=mejor_nombre,
                top_sim=round(float(mejor_sim), 4),
                second_profile_id=segundo_pid or "",
                second_nombre=segundo_nombre or "",
                second_sim=round(float(segunda_sim), 4),
                top2_gap=round(float(gap), 4),
                threshold_main=round(float(UMBRAL_SIMILITUD), 4),
                threshold_owner=round(float(UMBRAL_ADMIN_DIRECTO), 4),
                threshold_gap=round(float(UMBRAL_DIFERENCIA_TOP2), 4),
                profiles_evaluated=int(len(matches)),
            )

            if aceptado:
                return accepted_pid, accepted_nombre, mejor_sim

            return None, None, mejor_sim
        except Exception as e:
            print(f"[VOICE_ID] identify error: {e}")
            with _lock:
                self._ultimo_debug = {
                    "decision": "identify_exception",
                    "error": str(e)[:160],
                }
            obs_event("voice_id_error", error=str(e)[:200])
            return None, None, 0.0

    def verificar_voz_en_cada_mensaje(
        self, audio_bytes: bytes, perfil_esperado: str | None
    ) -> tuple[str | None, str | None, float, bool]:
        if not isinstance(audio_bytes, (bytes, bytearray)):
            return None, None, 0.0, False
        if not self._wait_encoder_ready(timeout=3.0):
            return None, None, 0.0, False
        try:
            emb = self._embedding_desde_audio_bytes(audio_bytes)
            if emb is None:
                return None, None, 0.0, False

            matches = []
            with _lock:
                for pid, datos in self.perfiles_voz.items():
                    sim = float(
                        np.dot(emb, datos["embedding"])
                        / (np.linalg.norm(emb) * np.linalg.norm(datos["embedding"]) + 1e-8)
                    )
                    matches.append((pid, datos["nombre"], sim))

            if not matches:
                return None, None, 0.0, False

            matches.sort(key=lambda x: x[2], reverse=True)
            mejor_pid, mejor_nombre, mejor_sim = matches[0]
            es_confiable = mejor_sim >= 0.55

            if perfil_esperado and mejor_pid != perfil_esperado:
                print(f"[VOICE ID] WARNING: Voz de '{mejor_nombre}' pero perfil era '{perfil_esperado}'!")
                return mejor_pid, mejor_nombre, mejor_sim, False

            return mejor_pid, mejor_nombre, mejor_sim, es_confiable
        except Exception as e:
            print(f"[VOICE_ID] verify_voice error: {e}")
            return None, None, 0.0, False

    def registrar_voz(self, audio_bytes: bytes, profile_id: str, nombre: str) -> bool:
        """Guarda la huella de voz. Recalcula centroide con todos los samples."""
        if not isinstance(audio_bytes, (bytes, bytearray)):
            return False
        if not self._wait_encoder_ready(timeout=8.0):
            return False
        try:
            emb = self._embedding_desde_audio_bytes(audio_bytes)
            if emb is None:
                return False

            emb_blob_nuevo = emb.astype(np.float32).tobytes()

            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute(
                    "INSERT INTO voice_embeddings(profile_id, embedding) VALUES(?, ?)",
                    (profile_id, emb_blob_nuevo),
                )
                rows = conn.execute(
                    """
                    SELECT embedding FROM voice_embeddings
                    WHERE profile_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (profile_id, MAX_EMBEDDINGS_PER_PROFILE),
                ).fetchall()

                n_samples = len(rows)
                all_embs = [np.frombuffer(blob, dtype=np.float32) for (blob,) in rows if blob]
                if not all_embs:
                    return False

                centroid = np.mean(all_embs, axis=0)
                norm = np.linalg.norm(centroid)
                if norm > 1e-8:
                    centroid = centroid / norm

                conn.execute(
                    """
                    INSERT INTO voice_profiles(profile_id, nombre, embedding, created_at)
                    VALUES(?, ?, ?, datetime('now'))
                    ON CONFLICT(profile_id) DO UPDATE SET
                        embedding = excluded.embedding,
                        nombre = excluded.nombre
                    """,
                    (profile_id, nombre, centroid.astype(np.float32).tobytes()),
                )
                conn.commit()
            finally:
                conn.close()

            with _lock:
                self.perfiles_voz[profile_id] = {
                    "nombre": nombre,
                    "embedding": centroid,
                    "n_samples": n_samples,
                }

            print(f"[VOICE_ID] Perfil '{profile_id}' actualizado. Centroide de {n_samples} sample(s).")
            obs_event("voice_registered", profile_id=profile_id, nombre=nombre, n_samples=n_samples)
            return True
        except Exception as e:
            print(f"[VOICE_ID] register_voice error: {e}")
            obs_event("voice_register_error", error=str(e)[:200])
            return False

    def similitud_con_perfil(self, audio_bytes: bytes, profile_id: str) -> float:
        pid = str(profile_id or "").strip().lower()
        if not pid or not self._wait_encoder_ready(timeout=2.0):
            return 0.0
        try:
            emb = self._embedding_desde_audio_bytes(audio_bytes)
            if emb is None:
                return 0.0
            with _lock:
                target = (self.perfiles_voz.get(pid) or {}).get("embedding")
            if target is None:
                return 0.0
            return float(np.dot(emb, target) / (np.linalg.norm(emb) * np.linalg.norm(target) + 1e-8))
        except Exception as e:
            print(f"[VOICE_ID] profile_similarity error: {e}")
            return 0.0

    def purge_non_owner_profiles(self, owner_profile_id: str | None = None) -> int:
        owner = str(owner_profile_id or OWNER_PID).strip().lower() or OWNER_PID
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("DELETE FROM voice_profiles WHERE profile_id <> ?", (owner,))
            deleted = int(cur.rowcount or 0)
            cur.execute("DELETE FROM voice_embeddings WHERE profile_id <> ?", (owner,))
            conn.commit()
            conn.close()
            with _lock:
                self.perfiles_voz = {k: v for k, v in self.perfiles_voz.items() if k == owner}
            if deleted:
                print(f"[VOICE_ID] Purge: {deleted} non-owner profiles deleted.")
            return deleted
        except Exception as e:
            print(f"[VOICE_ID] purge_non_owner_profiles error: {e}")
            return 0

    def reset_owner_profile(self) -> bool:
        """Borra todos los samples del owner para re-enrollment limpio."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM voice_profiles WHERE profile_id = ?", (OWNER_PID,))
            conn.execute("DELETE FROM voice_embeddings WHERE profile_id = ?", (OWNER_PID,))
            conn.commit()
            conn.close()
            with _lock:
                self.perfiles_voz.pop(OWNER_PID, None)
            print("[VOICE_ID] Owner profile reset. Ready for re-enrollment.")
            return True
        except Exception as e:
            print(f"[VOICE_ID] reset_owner_profile error: {e}")
            return False

    def get_profile_stats(self) -> dict:
        stats = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            for pid in list(self.perfiles_voz.keys()):
                n = conn.execute("SELECT COUNT(*) FROM voice_embeddings WHERE profile_id=?", (pid,)).fetchone()[0]
                stats[pid] = {
                    "nombre": self.perfiles_voz[pid]["nombre"],
                    "n_samples": n,
                    "umbral_activo": UMBRAL_ADMIN_DIRECTO if pid == OWNER_PID else UMBRAL_SIMILITUD,
                }
            conn.close()
        except Exception as e:
            print(f"[VOICE_ID] get_profile_stats error: {e}")
        return stats

    def _limpiar_perfiles_viejos(self, dias: int = 30) -> int:
        """Elimina perfiles invitados sin actividad. El owner NUNCA se toca."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM voice_profiles
                WHERE profile_id <> ?
                AND profile_id NOT IN (
                    SELECT profile_id FROM voice_embeddings
                    WHERE created_at >= datetime('now', ?)
                )
            """,
                (OWNER_PID, f"-{dias} days"),
            )
            deleted = int(cur.rowcount or 0)
            cur.execute("""
                DELETE FROM voice_embeddings
                WHERE profile_id NOT IN (SELECT profile_id FROM voice_profiles)
            """)
            conn.commit()
            conn.close()
            ids_activos = self._get_active_profile_ids()
            with _lock:
                self.perfiles_voz = {k: v for k, v in self.perfiles_voz.items() if k == OWNER_PID or k in ids_activos}
            if deleted:
                print(f"[VOICE_ID] Limpieza: {deleted} perfil(es) de invitados inactivos eliminados.")
            return deleted
        except Exception as e:
            print(f"[VOICE_ID] _clean_old_profiles error: {e}")
            return 0

    def _get_active_profile_ids(self) -> set:
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT profile_id FROM voice_profiles").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    # =========================================================
    # Audio processing helpers
    # =========================================================

    def _check_audio_quality(self, wav: np.ndarray, sr: int) -> tuple[bool, str]:
        duration_ms = (len(wav) / sr) * 1000
        if duration_ms < MIN_AUDIO_DURATION_MS:
            return False, f"audio_muy_corto ({duration_ms:.0f}ms)"
        if wav.dtype == np.int16:
            wav = wav.astype(np.float32) / 32767.0
        wav = np.clip(wav, -1.0, 1.0)
        rms = np.sqrt(np.mean(wav**2))
        if rms < MIN_AUDIO_ENERGY:
            return False, f"audio_muy_silencioso (RMS: {rms:.4f})"
        return True, "ok"

    def _apply_vad(self, wav: np.ndarray, sr: int) -> np.ndarray:
        try:
            hop_length = int(sr * 0.01)
            frame_length = int(sr * 0.02)
            energy = np.array(
                [
                    np.sqrt(np.mean(wav[i : i + frame_length] ** 2))
                    for i in range(0, len(wav) - frame_length, hop_length)
                ]
            )
            if len(energy) == 0:
                return wav
            threshold = max(np.max(energy) * VOICE_ACTIVITY_THRESHOLD, MIN_AUDIO_ENERGY)
            active_frames = energy > threshold
            if not np.any(active_frames):
                return wav
            active_indices = np.where(active_frames)[0] * hop_length
            pad_samples = int(sr * 0.3)
            start = max(0, active_indices[0] - pad_samples)
            end = min(len(wav), active_indices[-1] + pad_samples)
            min_samples = int(sr * 1.5)
            segment = wav[start:end]
            if len(segment) < min_samples and len(wav) >= min_samples:
                center = (start + end) // 2
                half = min_samples // 2
                start = max(0, center - half)
                end = min(len(wav), start + min_samples)
                segment = wav[start:end]
            return segment
        except Exception as e:
            print(f"[VOICE_ID] VAD error: {e}")
            return wav

    def _preprocess_audio_bytes(self, audio_bytes: bytes) -> bytes | None:
        try:
            if not isinstance(audio_bytes, (bytes, bytearray)) or len(audio_bytes) < 100:
                return None

            audio_a_procesar = audio_bytes
            magic = audio_bytes[:4] if len(audio_bytes) >= 4 else b""
            es_wav = magic[:4] == b"RIFF" and b"WAVE" in audio_bytes[:12]

            if not es_wav:
                try:
                    audio_a_procesar = convert_to_wav(audio_bytes, runtime_dir=RUNTIME_DIR)
                except AudioConversionError:
                    return None

            wav, sr = sf.read(io.BytesIO(audio_a_procesar))
            if wav.ndim > 1:
                wav = wav.mean(axis=1)

            valido, razon = self._check_audio_quality(wav, sr)
            if not valido:
                print(f"[VOICE_ID] Audio rejected: {razon}")
                return None

            wav = self._apply_vad(wav, sr)

            if sr != 16000:
                # Optional voice dependency; core mode must import without SciPy.
                from scipy.signal import resample_poly  # noqa: PLC0415

                gcd = np.gcd(sr, 16000)
                wav = resample_poly(wav, 16000 // gcd, sr // gcd)
                sr = 16000

            if wav.dtype in (np.float32, np.float64):
                wav = (wav * 32767).astype(np.int16)

            valido, razon = self._check_audio_quality(wav, sr)
            if not valido:
                print(f"[VOICE_ID] Audio rejected post-VAD: {razon}")
                return None

            _fd_tmp, tmp_path = tempfile.mkstemp(suffix=".wav", dir=RUNTIME_DIR)
            os.close(_fd_tmp)
            try:
                sf.write(tmp_path, wav, sr)
                with open(tmp_path, "rb") as rf:
                    result = rf.read()
                print(f"[VOICE_ID] Preprocessing OK: {len(result)} bytes, sr={sr}")
                return result
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[VOICE_ID] Error preprocessing audio: {e}")
            traceback.print_exc()
            return None

    def _embedding_desde_audio_bytes(self, audio_bytes: bytes) -> np.ndarray | None:
        try:
            # Torch is loaded only when biometric inference is actually requested.
            import torch  # noqa: PLC0415

            if not isinstance(audio_bytes, (bytes, bytearray)):
                return None

            print(f"[VOICE_ID] Input audio: {len(audio_bytes)} bytes, tipo={type(audio_bytes).__name__}")
            normalized = self._preprocess_audio_bytes(audio_bytes)
            print(f"[VOICE_ID] Preprocesado result: tipo={type(normalized).__name__ if normalized else 'None'}")

            if normalized is None:
                return None

            wav_data, sr = sf.read(io.BytesIO(normalized))
            print(f"[VOICE_ID] soundfile.read: wav_data.shape={wav_data.shape}, sr={sr}")
            if wav_data.ndim > 1:
                wav_data = wav_data.mean(axis=1)
            signal = torch.from_numpy(wav_data).float().unsqueeze(0)
            print(f"[VOICE_ID] Signal pre.encode_batch: shape={signal.shape}")
            embeddings = self.encoder.encode_batch(signal)
            print(f"[VOICE_ID] Embeddings: tipo={type(embeddings)}, shape={getattr(embeddings, 'shape', 'N/A')}")
            if embeddings is not None:
                emb = embeddings.squeeze().numpy()
                norm = np.linalg.norm(emb)
                if norm > 1e-8:
                    emb = emb / norm
                print(
                    f"[VOICE_ID] Embedding: shape={emb.shape}, "
                    f"norm={np.linalg.norm(emb):.4f}, finite={np.all(np.isfinite(emb))}"
                )
                if np.all(np.isfinite(emb)):
                    return emb
            return None
        except Exception as e:
            print(f"[VOICE_ID] Error extracting embedding: {e}")
            traceback.print_exc()
            return None

    def _wait_encoder_ready(self, timeout: float = 5.0) -> bool:
        if not self._available:
            return False
        if getattr(self, "_encoder_ready", False):
            return True
        t0 = _time.time()
        while not getattr(self, "_encoder_ready", False) and (_time.time() - t0) < float(timeout or 0):
            _time.sleep(0.1)
        return getattr(self, "_encoder_ready", False)


# Instancia singleton — se importa desde aquí o desde voice_id.py
voice_id_motor = VoiceIdentifier()
