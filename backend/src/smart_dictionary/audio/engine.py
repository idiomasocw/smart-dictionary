"""Kokoro-82M on the CPU (Apache-2.0), espeak-ng for sentences, and AAC encoding.

The model files come from kokoro-onnx's release. They're downloaded once into the model
directory and checked against their SHA-256 before use, so a corrupt or altered file never
speaks. Everything here needs the `audio` dependency group; the API itself never imports it.
"""

import hashlib
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from smart_dictionary.audio.phonemes import from_espeak

if TYPE_CHECKING:
    import numpy as np

ENGINE = "kokoro-82m@1.0"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL = "kokoro-v1.0.onnx"
VOICE_FILE = "voices-v1.0.bin"
MODEL_FILES = {
    MODEL: "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    VOICE_FILE: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
# One voice per variety for headwords; the examples are US English (STYLE §8), so they use
# the US voice. Kokoro's best-rated voices in each accent.
VOICES = {"en-US": "af_heart", "en-GB": "bf_emma"}
EXAMPLE_VARIETY = "en-US"

SAMPLE_RATE = 24_000
BIT_RATE = 48_000
LEAD, TAIL = 0.15, 0.25  # seconds of silence around the sound, so no player clips it
PEAK = 0.89  # -1 dBFS: every file peaks at the same level


def ensure_model(directory: Path) -> None:
    """Download what's missing, and refuse any file whose SHA-256 doesn't match."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in MODEL_FILES.items():
        path = directory / name
        if not path.exists():
            partial = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(RELEASE + name, partial)
            partial.replace(path)
        if _sha256(path) != expected:
            path.unlink()
            raise RuntimeError(f"{name} didn't match its SHA-256; deleted, so it downloads again")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Speaker:
    """Kokoro with its model loaded once. Not thread-safe; use one per worker process."""

    engine = ENGINE

    def __init__(self, directory: Path, threads: int | None = None) -> None:
        import kokoro_onnx
        import onnxruntime
        from phonemizer.backend import EspeakBackend

        ensure_model(directory)
        options = onnxruntime.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
        session = onnxruntime.InferenceSession(
            str(directory / MODEL), sess_options=options, providers=["CPUExecutionProvider"]
        )
        # Building Kokoro also points phonemizer at the espeak-ng library it bundles.
        self._kokoro = kokoro_onnx.Kokoro.from_session(session, str(directory / VOICE_FILE))
        self._espeak = {
            variety: EspeakBackend(
                language=variety.lower(), preserve_punctuation=True, with_stress=True, tie="^"
            )
            for variety in VOICES
        }
        self._lock = threading.Lock()

    def phonemize(self, text: str, variety: str) -> str:
        """A sentence's phonemes, in Kokoro's alphabet."""
        with self._lock:  # espeak-ng keeps process-wide state
            raw = self._espeak[variety].phonemize([text])[0]
        return from_espeak(raw.strip(), variety)

    def say(self, phonemes: str, voice: str) -> "np.ndarray":
        audio, _ = self._kokoro.create(phonemes, voice=voice, is_phonemes=True)
        return audio


def encode(samples: "np.ndarray") -> tuple[bytes, int]:
    """Mono AAC-LC in an MP4 container (.m4a), and its length in milliseconds."""
    import av
    import numpy as np

    peak = float(np.abs(samples).max()) or 1.0
    audio = np.concatenate(
        [
            np.zeros(round(LEAD * SAMPLE_RATE), dtype=np.float32),
            samples.astype(np.float32) * (PEAK / peak),
            np.zeros(round(TAIL * SAMPLE_RATE), dtype=np.float32),
        ]
    )
    # A real file, not memory: faststart moves the index to the front in a second pass,
    # which needs to read back what was written. Players can then start before the end.
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "audio.m4a"
        with av.open(str(path), mode="w", format="mp4", options={"movflags": "+faststart"}) as out:
            stream = out.add_stream("aac", rate=SAMPLE_RATE, layout="mono")
            stream.bit_rate = BIT_RATE
            frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="fltp", layout="mono")
            frame.sample_rate = SAMPLE_RATE
            for packet in stream.encode(frame):
                out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)
        return path.read_bytes(), round(len(audio) / SAMPLE_RATE * 1000)
