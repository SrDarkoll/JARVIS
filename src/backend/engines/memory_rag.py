import hashlib
import json
import os
import threading

from core.jarvis_config import CACHE_DIR, RAG_ENABLED, RUNTIME_DIR
from core.jarvis_state import DEFAULT_PROFILE_ID as _OWNER_PID

# It will be attempted to import FAISS in a secure way
if RAG_ENABLED:
    try:
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document

        FAISS_DISPONIBLE = True
    except Exception:
        FAISS_DISPONIBLE = False
else:
    FAISS = None
    Document = None
    FAISS_DISPONIBLE = False

_HUGGINGFACE_EMBEDDINGS_CLASS = None
_EMBEDDINGS_IMPORT_ERROR = None


def _load_huggingface_embeddings_class():
    global _HUGGINGFACE_EMBEDDINGS_CLASS, _EMBEDDINGS_IMPORT_ERROR

    if _HUGGINGFACE_EMBEDDINGS_CLASS is not None:
        return _HUGGINGFACE_EMBEDDINGS_CLASS
    if _EMBEDDINGS_IMPORT_ERROR is not None:
        return None

    try:
        from langchain_huggingface import HuggingFaceEmbeddings as embeddings_class  # type: ignore
    except Exception as exc:
        _EMBEDDINGS_IMPORT_ERROR = exc
        return None

    _HUGGINGFACE_EMBEDDINGS_CLASS = embeddings_class
    return _HUGGINGFACE_EMBEDDINGS_CLASS


def _format_embeddings_import_error() -> str:
    if _EMBEDDINGS_IMPORT_ERROR is None:
        return "UnknownError"
    return type(_EMBEDDINGS_IMPORT_ERROR).__name__

_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BASE)
_RUNTIME_DIR = RUNTIME_DIR
VECTOR_DB_DIR = os.getenv("JARVIS_FAISS_DIR") or os.path.join(_RUNTIME_DIR, "faiss_index")
# Explicit repository + local cache avoids failures like "NoneType" with empty HF_HOME or weird cwd.
EMBEDDING_MODEL = (
    os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip()
    or "sentence-transformers/all-MiniLM-L6-v2"
)
_HF_CACHE = os.getenv("JARVIS_HF_CACHE") or os.path.join(
    os.getenv("JARVIS_CACHE_DIR") or CACHE_DIR,
    "huggingface",
)
FAISS_MANIFEST_FILE = "index.sha256.json"


def _read_positive_int_env(name: str, default: int) -> int:
    try:
        value = int((os.getenv(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_FAISS_FILE_BYTES = _read_positive_int_env(
    "JARVIS_MAX_FAISS_FILE_BYTES", 256 * 1024 * 1024
)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _faiss_required_files(index_dir: str) -> dict[str, str]:
    return {
        "index.faiss": os.path.join(index_dir, "index.faiss"),
        "index.pkl": os.path.join(index_dir, "index.pkl"),
    }


def _path_inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(child), os.path.abspath(parent)]) == os.path.abspath(parent)
    except Exception:
        return False


def _validate_faiss_index(index_dir: str) -> tuple[bool, str]:
    allowed_root = os.path.abspath(_RUNTIME_DIR)
    if not _path_inside(index_dir, allowed_root):
        return False, "FAISS index is outside the configured runtime directory."
    if not os.path.isdir(index_dir):
        return False, "FAISS index directory does not exist."

    manifest_path = os.path.join(index_dir, FAISS_MANIFEST_FILE)
    if not os.path.isfile(manifest_path) or os.path.islink(manifest_path):
        return False, "FAISS manifest is missing."

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f) or {}
    except Exception as exc:
        return False, (
            "FAISS manifest is not valid JSON "
            f"({type(exc).__name__})."
        )

    files = _faiss_required_files(index_dir)
    expected = manifest.get("files") or {}
    for name, path in files.items():
        if not os.path.isfile(path) or os.path.islink(path):
            return False, f"Required FAISS file is missing or unsafe: {name}"
        size = os.path.getsize(path)
        if size <= 0 or size > MAX_FAISS_FILE_BYTES:
            return False, f"Required FAISS file has unsafe size: {name}"
        file_meta = expected.get(name) or {}
        if int(file_meta.get("size", -1)) != size:
            return False, f"FAISS manifest size mismatch: {name}"
        if str(file_meta.get("sha256") or "") != _sha256_file(path):
            return False, f"FAISS manifest hash mismatch: {name}"
    return True, ""


def _write_faiss_manifest(index_dir: str) -> None:
    os.makedirs(index_dir, exist_ok=True)
    files = {}
    for name, path in _faiss_required_files(index_dir).items():
        if os.path.isfile(path) and not os.path.islink(path):
            files[name] = {
                "size": os.path.getsize(path),
                "sha256": _sha256_file(path),
            }
    if set(files) == set(_faiss_required_files(index_dir)):
        manifest_path = os.path.join(index_dir, FAISS_MANIFEST_FILE)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "files": files}, f, ensure_ascii=False, indent=2)


class MemoryRAG:
    def __init__(self):
        self.db = None
        self.embeddings = None
        self._lock = threading.Lock()
        self.lista = FAISS_DISPONIBLE

        if not RAG_ENABLED:
            self.lista = False
            return

        if os.getenv("JARVIS_TEST_MODE") == "1":
            self.lista = False
            return

        if not FAISS_DISPONIBLE:
            print("[RAG] FAISS/SentenceTransformers libraries not detected.")
            return

        # Initialize in a background thread to avoid blocking the boot
        threading.Thread(target=self._init_bg, daemon=True).start()

    def _init_bg(self):
        from utils.jarvis_i18n import get_bt
        bt = get_bt()
        print(bt["log_rag_loading"].format(model=EMBEDDING_MODEL))

        embeddings_class = _load_huggingface_embeddings_class()
        if embeddings_class is None:
            from core.jarvis_observability import obs_event

            reason = _format_embeddings_import_error()
            obs_event("rag_embedding_provider_unavailable", error=reason)
            print(f"[RAG] Embedding provider unavailable ({reason}). RAG disabled.")
            self.lista = False
            return

        try:
            os.makedirs(_HF_CACHE, exist_ok=True)
            if not (os.environ.get("HF_HOME") or "").strip():
                os.environ["HF_HOME"] = os.path.abspath(_HF_CACHE)
            self.embeddings = embeddings_class(
                model_name=EMBEDDING_MODEL,
                cache_folder=os.path.join(_HF_CACHE, "embeddings"),
            )
        except Exception as exc:
            from core.jarvis_observability import obs_event

            error_type = type(exc).__name__
            obs_event("rag_init_huggingface_error", error=error_type)
            print(f"[RAG] Failed to download/load HF model ({error_type}).")
            self.lista = False
            return

        with self._lock:
            if os.path.exists(VECTOR_DB_DIR) and os.path.isdir(VECTOR_DB_DIR):
                try:
                    valid_index, reason = _validate_faiss_index(VECTOR_DB_DIR)
                    if not valid_index:
                        raise ValueError(reason)
                    self.db = FAISS.load_local(
                        VECTOR_DB_DIR,
                        self.embeddings,
                        allow_dangerous_deserialization=True,
                    )
                    from utils.jarvis_i18n import get_bt
                    bt = get_bt()
                    print(
                        bt["log_rag_ready"].format(size=self.db.index.ntotal)
                    )
                except Exception as exc:
                    from core.jarvis_observability import obs_event

                    error_type = type(exc).__name__
                    obs_event("rag_index_load_error", error=error_type)
                    print(
                        "[RAG] FAISS index was not readable and will be ignored "
                        f"({error_type})."
                    )
                    self.db = None

    def agregar_interaccion(
        self, user_msg: str, ai_msg: str, profile_id: str = ""
    ):
        """Registers a pair of messages (User->AI) as a document with profile metadata."""
        if not self.lista or self.embeddings is None:
            return
        pid = (str(profile_id or "").strip().lower() or _OWNER_PID)[:64]
        scope = "shared" if pid == _OWNER_PID else "private"

        texto = (
            f"The User said: '{user_msg}'.\nJ.A.R.V.I.S. (AI) responded: '{ai_msg}'."
        )
        doc = Document(
            page_content=texto,
            metadata={"tipo": "conversacion", "profile_id": pid, "scope": scope},
        )

        with self._lock:
            try:
                if self.db is None:
                    self.db = FAISS.from_documents([doc], self.embeddings)
                else:
                    self.db.add_documents([doc])

                def _save_task():
                    try:
                        with self._lock:
                            self.db.save_local(VECTOR_DB_DIR)
                            _write_faiss_manifest(VECTOR_DB_DIR)
                    except Exception as exc:
                        print(f"[RAG BG SAVE] Failed ({type(exc).__name__}).")

                threading.Thread(target=_save_task, daemon=True).start()
            except Exception as exc:
                from core.jarvis_observability import obs_event

                error_type = type(exc).__name__
                obs_event("rag_indexing_error", error=error_type)
                print(f"[RAG] Error scheduling memory ({error_type}).")

    def buscar_contexto(
        self, query: str, top_k: int = 3, profile_id: str = ""
    ) -> str:
        """Returns a block of text that will be appended to the system prompt."""
        if not self.lista or self.db is None or not query:
            return ""
        pid = (str(profile_id or "").strip().lower() or _OWNER_PID)[:64]

        try:
            resultados = self.db.similarity_search(query, k=max(int(top_k), 12))
            if not resultados:
                return ""

            def _normalizar(txt: str) -> str:
                t = str(txt or "").lower()
                return (
                    t.replace("á", "a")
                    .replace("é", "e")
                    .replace("í", "i")
                    .replace("ó", "o")
                    .replace("ú", "u")
                    .replace("ü", "u")
                    .replace("ñ", "n")
                )

            bloqueados = ("vengadores", "avengers", "marvel", "priorizar situacion")
            filtrados = []
            for d in resultados:
                norm = _normalizar(getattr(d, "page_content", ""))
                if any(b in norm for b in bloqueados):
                    continue
                md = getattr(d, "metadata", {}) or {}
                d_scope = str(md.get("scope") or "private").strip().lower()
                d_pid = str(md.get("profile_id") or "").strip().lower()

                # Interconnected memory: each profile sees its private memory + shared memory.
                if d_scope == "shared":
                    pass
                elif d_pid != pid:
                    continue
                filtrados.append(d)
            if not filtrados:
                return ""

            texto = "\n--- DEEP MEMORY RECOVERY (FAISS) ---\n"
            for i, doc in enumerate(filtrados[: max(1, int(top_k))], 1):
                texto += f"[{i}] {doc.page_content}\n"
            texto += "------------------------------------------------\n"
            return texto
        except Exception as exc:
            from core.jarvis_observability import obs_event

            error_type = type(exc).__name__
            obs_event("rag_search_error", error=error_type)
            print(f"[RAG] Error searching FAISS memory ({error_type}).")
            return ""


# Asynchronous singleton instance
rag_motor = MemoryRAG()
