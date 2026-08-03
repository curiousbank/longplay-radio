"""Keyless Genblaze audio providers: deterministic signal and generic Coqui."""

from __future__ import annotations

import hashlib
import math
import os
import struct
import subprocess
import wave
from pathlib import Path

from genblaze_core import Asset, AudioMetadata, Modality, ProviderError
from genblaze_core.models.step import Step
from genblaze_core.providers import DiscoverySupport, ProviderCapabilities, SyncProvider


MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 30
FAKE_MODEL = "longplay-deterministic-signal-v1"


def _safe_text(step: Step, expected_model: str) -> str:
    if step.modality != Modality.AUDIO or step.model != expected_model:
        raise ProviderError("renderer received an unsupported model or modality")
    text = step.prompt or ""
    if not 20 <= len(text) <= 360 or any(ord(char) < 32 for char in text):
        raise ProviderError("renderer text is outside the approved bound")
    return text


class FakeAudioProvider(SyncProvider):
    """Create an audible deterministic signal, explicitly not human speech."""

    name = "longplay-fake"
    discovery_support = DiscoverySupport.NONE

    def __init__(self, *, output_dir: Path):
        super().__init__()
        self.output_dir = output_dir.resolve()

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.AUDIO],
            supported_inputs=["text"],
            models=[FAKE_MODEL],
            output_formats=["audio/wav"],
        )

    def generate(self, step: Step, config=None) -> Step:
        text = _safe_text(step, FAKE_MODEL)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output = (self.output_dir / f"{step.step_id}.wav").resolve()
        if not output.is_relative_to(self.output_dir):
            raise ProviderError("fake output escaped its temporary directory")
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        sample_rate = 22_050
        duration = min(5.0, max(2.0, len(text) / 80))
        frame_count = int(duration * sample_rate)
        frames = bytearray()
        frequency = 220 + digest[0]
        for index in range(frame_count):
            t = index / sample_rate
            gate = 1.0 if digest[(index * len(digest)) // frame_count] & 1 else 0.38
            envelope = max(0.0, min(1.0, t * 10, (duration - t) * 10))
            sample = math.sin(2 * math.pi * frequency * t)
            sample += 0.3 * math.sin(2 * math.pi * frequency * 1.5 * t)
            value = int(9_000 * gate * envelope * sample)
            frames.extend(struct.pack("<h", max(-32_767, min(32_767, value))))
        try:
            with wave.open(str(output), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(bytes(frames))
        except (OSError, wave.Error) as exc:
            raise ProviderError("fake WAV could not be written") from exc
        step.assets.append(
            Asset(
                url=output.as_uri(),
                media_type="audio/wav",
                size_bytes=output.stat().st_size,
                duration=duration,
                audio=AudioMetadata(channels=1, sample_rate=sample_rate, codec="pcm_s16le"),
                metadata={
                    "fake_mode": True,
                    "not_speech": True,
                    "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                },
            )
        )
        return step


class CoquiAudioProvider(SyncProvider):
    """Invoke one configured generic Coqui model without cloning inputs."""

    name = "coqui-local"
    discovery_support = DiscoverySupport.NONE

    def __init__(
        self,
        *,
        executable: Path,
        model: str,
        output_dir: Path,
        timeout_seconds: int,
    ):
        super().__init__()
        self.executable = executable
        self.model = model
        self.output_dir = output_dir.resolve()
        self.timeout_seconds = timeout_seconds

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.AUDIO],
            supported_inputs=["text"],
            models=[self.model],
            output_formats=["audio/wav"],
        )

    def generate(self, step: Step, config=None) -> Step:
        text = _safe_text(step, self.model)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output = (self.output_dir / f"{step.step_id}.wav").resolve()
        if not output.is_relative_to(self.output_dir):
            raise ProviderError("Coqui output escaped its temporary directory")
        output.unlink(missing_ok=True)
        environment = {
            name: os.environ[name]
            for name in ("HOME", "PATH", "LANG", "LC_ALL", "XDG_CACHE_HOME", "CUDA_VISIBLE_DEVICES")
            if name in os.environ
        }
        environment["NUMBA_CACHE_DIR"] = str(self.output_dir / "numba-cache")
        try:
            completed = subprocess.run(
                [
                    str(self.executable),
                    "--text",
                    text,
                    "--model_name",
                    self.model,
                    "--out_path",
                    str(output),
                ],
                check=False,
                timeout=self.timeout_seconds,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            output.unlink(missing_ok=True)
            raise ProviderError("Coqui execution failed") from exc
        if completed.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            raise ProviderError("Coqui execution failed")
        size, duration, channels, sample_rate = inspect_wav(output)
        step.assets.append(
            Asset(
                url=output.as_uri(),
                media_type="audio/wav",
                size_bytes=size,
                duration=duration,
                audio=AudioMetadata(
                    channels=channels, sample_rate=sample_rate, codec="pcm"
                ),
                metadata={
                    "local_inference": True,
                    "voice_cloning": False,
                    "generic_voice": True,
                    "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                },
            )
        )
        return step


def inspect_wav(path: Path) -> tuple[int, float, int, int]:
    try:
        size = path.stat().st_size
        if not 1 <= size <= MAX_AUDIO_BYTES:
            raise ProviderError("WAV is outside the 1-16 MiB bound")
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            sample_width = audio.getsampwidth()
        duration = frames / sample_rate
    except (OSError, EOFError, ZeroDivisionError, wave.Error) as exc:
        raise ProviderError("renderer returned an unreadable WAV") from exc
    if channels not in {1, 2} or sample_width not in {1, 2, 3, 4}:
        raise ProviderError("WAV channel or sample width is unsupported")
    if not 8_000 <= sample_rate <= 192_000 or not 0.5 <= duration <= MAX_AUDIO_SECONDS:
        raise ProviderError("WAV rate or duration is outside the safe bound")
    return size, duration, channels, sample_rate
