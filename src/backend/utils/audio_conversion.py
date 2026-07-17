"""Shared FFmpeg-based audio conversion helpers.

The public errors intentionally omit command output and filesystem paths because
these helpers are used by HTTP- and messaging-facing code.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


FFMPEG_TIMEOUT_SECONDS = 30
_CONVERSION_ERROR = "Audio conversion failed."


class AudioConversionError(RuntimeError):
    """Raised when audio cannot be converted into the requested format."""


def _input_suffix(audio_bytes: bytes) -> str:
    magic = audio_bytes[:4]
    if magic == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if magic == b"OggS":
        return ".ogg"
    if magic == b"RIFF":
        return ".wav"
    if magic[:3] == b"ID3" or magic[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return ".mp3"
    return ".bin"


def _resolve_runtime_dir(runtime_dir: str | os.PathLike[str] | None) -> Path:
    configured = runtime_dir or os.getenv("JARVIS_RUNTIME_DIR") or tempfile.gettempdir()
    path = Path(configured)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ffmpeg_commands(
    input_path: Path,
    output_path: Path,
    output_args: list[str],
) -> list[list[str]]:
    prefix = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]
    normal = [*prefix, "-i", str(input_path), *output_args, str(output_path)]
    matroska = [
        *prefix,
        "-f",
        "matroska",
        "-i",
        str(input_path),
        *output_args,
        str(output_path),
    ]
    tolerant = [
        *prefix,
        "-err_detect",
        "ignore_err",
        "-fflags",
        "+genpts+discardcorrupt",
        "-i",
        str(input_path),
        *output_args,
        str(output_path),
    ]
    return [normal, matroska, tolerant]


def _convert(
    audio_bytes: bytes,
    *,
    runtime_dir: str | os.PathLike[str] | None,
    output_suffix: str,
    output_args: list[str],
    expected_magic: bytes,
) -> bytes:
    if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
        raise AudioConversionError(_CONVERSION_ERROR)

    try:
        root = _resolve_runtime_dir(runtime_dir)
        with tempfile.TemporaryDirectory(prefix="jarvis_audio_", dir=root) as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / f"input{_input_suffix(bytes(audio_bytes))}"
            output_path = temp_path / f"output{output_suffix}"
            input_path.write_bytes(bytes(audio_bytes))

            for command in _ffmpeg_commands(input_path, output_path, output_args):
                output_path.unlink(missing_ok=True)
                result = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=FFMPEG_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                )
                if result.returncode != 0 or not output_path.is_file():
                    continue
                converted = output_path.read_bytes()
                if converted and converted.startswith(expected_magic):
                    return converted
    except (OSError, subprocess.SubprocessError):
        raise AudioConversionError(_CONVERSION_ERROR) from None

    raise AudioConversionError(_CONVERSION_ERROR)


def convert_to_wav(
    audio_bytes: bytes,
    *,
    runtime_dir: str | os.PathLike[str] | None = None,
) -> bytes:
    """Convert audio bytes to 16 kHz, mono, signed 16-bit PCM WAV."""
    return _convert(
        audio_bytes,
        runtime_dir=runtime_dir,
        output_suffix=".wav",
        output_args=[
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
        ],
        expected_magic=b"RIFF",
    )


def convert_to_ogg_opus(
    audio_bytes: bytes,
    *,
    runtime_dir: str | os.PathLike[str] | None = None,
) -> bytes:
    """Convert audio bytes to an OGG container encoded with libopus."""
    return _convert(
        audio_bytes,
        runtime_dir=runtime_dir,
        output_suffix=".ogg",
        output_args=[
            "-acodec",
            "libopus",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-f",
            "ogg",
        ],
        expected_magic=b"OggS",
    )
