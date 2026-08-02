"""Lazy, single-point access to the heavy ML models: Demucs, Whisper,
CREPE/pYIN (spec 6.5).

Each stage that needs a model takes the matching `Protocol` as a
constructor argument -- dependency injection, not a global singleton (spec
12.1 forbids globals; `ModelRegistry` is the one explicit exception the
spec itself carves out). `ModelRegistry` is where the real implementation
gets constructed, on first use, from a config snapshot; tests inject a fake
implementing the same `Protocol` instead, so a stage's own logic is
verified on synthetic signals without downloading model weights or
touching the network (spec 15.2).

Every stage already runs in its own child process (`PipelineRunner`,
enforcing spec 6.5's other memory requirement -- Demucs and Whisper must
never be resident together), which guarantees the OS reclaims a model's
memory the instant that process exits. `release()` below is still the
explicit-hygiene half of spec 6.5: it frees memory before a *second* model
loads within the same stage process (relevant once a stage needs more than
one model) and gives a log line marking exactly when a model's memory
should have dropped, instead of only inferring it from RSS after the fact.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from vocalcoach.config import PitchEngine
from vocalcoach.constants import (
    CREPE_BATCH_SIZE,
    CREPE_MODEL_CAPACITY,
    CREPE_VOICED_THRESHOLD,
    PITCH_FMAX_HZ,
    PITCH_FMIN_HZ,
)
from vocalcoach.models.audio import Lyrics, LyricsWord

logger = logging.getLogger(__name__)


class VocalSeparator(Protocol):
    """Isolates the vocal stem from a music mixture (spec 6.3.2, ADR-0003)."""

    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        """Returns the isolated vocal stem: mono float32 PCM, same sample
        rate and length as `mixture`."""
        ...

    def release(self) -> None: ...


class Transcriber(Protocol):
    """Transcribes a vocal stem to words with per-word timecodes (spec 6.3.3)."""

    def transcribe(self, samples: np.ndarray, sample_rate_hz: int) -> Lyrics: ...

    def release(self) -> None: ...


class PitchDetector(Protocol):
    """Tracks fundamental frequency over time (spec 6.3.5)."""

    def detect(
        self, samples: np.ndarray, sample_rate_hz: int, hop_seconds: float
    ) -> list[float | None]:
        """Returns one Hz value per `hop_seconds` frame; `None` where unvoiced."""
        ...

    def release(self) -> None: ...


class DemucsSeparator:
    """Real `VocalSeparator` backed by Demucs v4 (spec ADR-0003)."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._separator: Any = None

    def _loaded(self) -> Any:
        if self._separator is None:
            from demucs.api import Separator  # heavy import deferred to first use

            # No `repo=` here: passing one tells Demucs to treat it as a
            # self-contained *local-only* folder that must already hold the
            # exact model files, with no fallback to download them --
            # every separate_reference run failed with "htdemucs is neither
            # a single pre-trained model or a bag of models" the moment the
            # weights volume was empty. Leaving repo unset uses Demucs' own
            # HuggingFace-Hub-then-remote-repo download, cached under
            # TORCH_HOME/XDG_CACHE_HOME (worker/Dockerfile already points
            # both at the model-weights volume, spec 5.4/6.5).
            self._separator = Separator(model=self._model_name, device="cpu")
        return self._separator

    def separate_vocals(self, mixture: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        import torch
        from demucs.audio import convert_audio

        separator = self._loaded()
        # Demucs' pretrained models are trained on stereo mixtures; stage 1
        # hands us mono, so duplicate it to a 2-channel signal rather than
        # special-casing a mono path through the model.
        stereo = np.stack([mixture, mixture], axis=0)
        wav = torch.from_numpy(stereo).float()
        _origin, stems = separator.separate_tensor(wav, sr=sample_rate_hz)
        vocals = stems["vocals"]
        # separate_tensor's own docstring: "the wave will be resampled if it
        # doesn't match the model" -- htdemucs' native rate is 44.1kHz, so
        # for any other sample_rate_hz (this pipeline runs at 22050) `vocals`
        # comes back at 44100 regardless of what was passed in, silently
        # violating this method's own contract (VocalSeparator: "same sample
        # rate ... as mixture"). Every caller downstream trusted the
        # original sample_rate_hz when writing this array back out to a WAV
        # (write_mono in separate_reference.py), so the file's declared rate
        # didn't match its real sample count -- every reader computed
        # exactly double the true duration from it (a 165s song's vocal stem
        # read back as 330s), which is what made P4's reference pitch curve
        # roughly 2x the size it should be and made every alignment against
        # it fail outright (features.py's frame counts no longer had a
        # remotely reachable DTW path against the recording's correct ones).
        # Converting back to sample_rate_hz here is what the contract always
        # promised. channels=1 also downmixes stereo to mono in the same
        # call (demucs.audio.convert_audio_channels: wav.mean(dim=-2)).
        mono = convert_audio(vocals, separator.samplerate, sample_rate_hz, 1)
        return np.asarray(mono[0].cpu().numpy())

    def release(self) -> None:
        self._separator = None
        gc.collect()


class WhisperTranscriber:
    """Real `Transcriber` backed by `faster-whisper`'s CTranslate2 runtime
    (spec 6.3.3, 5.1, ADR-0021) -- the same Whisper models openai-whisper
    used, substantially faster and lighter on CPU at int8 quantization,
    comparable transcription quality.
    """

    _TARGET_SAMPLE_RATE_HZ = 16000  # Whisper's fixed training sample rate

    def __init__(self, model_name: str, weights_dir: Path, compute_type: str) -> None:
        self._model_name = model_name
        self._weights_dir = weights_dir
        self._compute_type = compute_type
        self._model: Any = None

    def _loaded(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy import deferred to first use

            self._model = WhisperModel(
                self._model_name,
                device="cpu",
                compute_type=self._compute_type,
                download_root=str(self._weights_dir),
            )
        return self._model

    def transcribe(self, samples: np.ndarray, sample_rate_hz: int) -> Lyrics:
        import librosa

        model = self._loaded()
        audio = samples
        if sample_rate_hz != self._TARGET_SAMPLE_RATE_HZ:
            audio = librosa.resample(
                samples, orig_sr=sample_rate_hz, target_sr=self._TARGET_SAMPLE_RATE_HZ
            )
        segments, info = model.transcribe(audio.astype(np.float32), word_timestamps=True)
        words = [
            LyricsWord(word=str(word.word).strip(), start=float(word.start), end=float(word.end))
            for segment in segments
            for word in (segment.words or [])
        ]
        return Lyrics(language=str(info.language or "unknown"), words=words)

    def release(self) -> None:
        self._model = None
        gc.collect()


class CrepePitchDetector:
    """Real `PitchDetector` backed by torchcrepe (spec 6.3.5, `PITCH_ENGINE=crepe`)."""

    def __init__(self) -> None:
        self._warmed_up = False

    def detect(
        self, samples: np.ndarray, sample_rate_hz: int, hop_seconds: float
    ) -> list[float | None]:
        import torch
        import torchcrepe

        self._warmed_up = True
        audio = torch.from_numpy(samples).float().unsqueeze(0)
        hop_length = max(1, round(sample_rate_hz * hop_seconds))
        pitch, periodicity = torchcrepe.predict(
            audio,
            sample_rate_hz,
            hop_length,
            fmin=PITCH_FMIN_HZ,
            fmax=PITCH_FMAX_HZ,
            model=CREPE_MODEL_CAPACITY,
            batch_size=CREPE_BATCH_SIZE,
            device="cpu",
            return_periodicity=True,
        )
        hz = pitch[0].cpu().numpy()
        voiced = periodicity[0].cpu().numpy() >= CREPE_VOICED_THRESHOLD
        return [float(v) if is_voiced else None for v, is_voiced in zip(hz, voiced, strict=True)]

    def release(self) -> None:
        self._warmed_up = False
        gc.collect()


class PyinPitchDetector:
    """Real `PitchDetector` backed by librosa's pYIN (spec 6.3.5,
    `PITCH_ENGINE=pyin`) -- faster than CREPE on CPU, less accurate."""

    def detect(
        self, samples: np.ndarray, sample_rate_hz: int, hop_seconds: float
    ) -> list[float | None]:
        import librosa

        hop_length = max(1, round(sample_rate_hz * hop_seconds))
        f0, voiced_flag, _voiced_prob = librosa.pyin(
            samples,
            fmin=PITCH_FMIN_HZ,
            fmax=PITCH_FMAX_HZ,
            sr=sample_rate_hz,
            hop_length=hop_length,
        )
        return [
            float(value) if is_voiced and not np.isnan(value) else None
            for value, is_voiced in zip(f0, voiced_flag, strict=True)
        ]

    def release(self) -> None:
        gc.collect()


class ModelRegistry:
    """Single lazy-load/release point for every heavy model a stage needs."""

    def __init__(
        self,
        *,
        demucs_model: str,
        whisper_model: str,
        pitch_engine: PitchEngine,
        weights_dir: Path,
        whisper_compute_type: str = "int8",
    ) -> None:
        self._demucs_model = demucs_model
        self._whisper_model = whisper_model
        self._whisper_compute_type = whisper_compute_type
        self._pitch_engine = pitch_engine
        self._weights_dir = weights_dir
        self._separator: VocalSeparator | None = None
        self._transcriber: Transcriber | None = None
        self._pitch_detector: PitchDetector | None = None

    def vocal_separator(self) -> VocalSeparator:
        if self._separator is None:
            logger.info("loading demucs model", extra={"model": self._demucs_model})
            self._separator = DemucsSeparator(self._demucs_model)
        return self._separator

    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            logger.info(
                "loading whisper model",
                extra={"model": self._whisper_model, "compute_type": self._whisper_compute_type},
            )
            self._transcriber = WhisperTranscriber(
                self._whisper_model, self._weights_dir, self._whisper_compute_type
            )
        return self._transcriber

    def pitch_detector(self) -> PitchDetector:
        if self._pitch_detector is None:
            logger.info("loading pitch engine", extra={"engine": self._pitch_engine})
            self._pitch_detector = (
                CrepePitchDetector() if self._pitch_engine == "crepe" else PyinPitchDetector()
            )
        return self._pitch_detector

    def release_all(self) -> None:
        """Releases every model this registry has loaded so far (spec 6.5)."""
        for model, name in (
            (self._separator, "demucs"),
            (self._transcriber, "whisper"),
            (self._pitch_detector, "pitch"),
        ):
            if model is not None:
                model.release()
                logger.info("released model", extra={"model": name})
        self._separator = None
        self._transcriber = None
        self._pitch_detector = None
