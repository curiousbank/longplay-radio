from __future__ import annotations

import stat
from pathlib import Path

from genblaze_core import Modality, Pipeline

from longplay_radio.providers import CoquiAudioProvider


def test_coqui_adapter_uses_only_generic_fixed_arguments(tmp_path):
    log = tmp_path / "args.log"
    executable = tmp_path / "tts"
    executable.write_text(
        """#!/usr/bin/env python3
import struct, sys, wave
from pathlib import Path
args = sys.argv[1:]
Path(%r).write_text('\\n'.join(args))
output = Path(args[args.index('--out_path') + 1])
with wave.open(str(output), 'wb') as wav:
    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
    wav.writeframes(struct.pack('<h', 0) * 16000)
""" % str(log)
    )
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    model = "tts_models/en/ljspeech/tacotron2-DDC"
    provider = CoquiAudioProvider(
        executable=executable,
        model=model,
        output_dir=tmp_path / "out",
        timeout_seconds=10,
    )
    result = (
        Pipeline("coqui-test", project_id="longplay-radio")
        .step(
            provider,
            model=model,
            prompt="LONGPLAY presents Blue Horizon by The Local Satellites right now.",
            modality=Modality.AUDIO,
        )
        .run(timeout=20, max_retries=1, raise_on_failure=True)
    )
    run = result.run if hasattr(result, "run") else result[0]
    assert run.steps[-1].assets
    arguments = log.read_text().splitlines()
    assert arguments[0] == "--text"
    assert arguments[2:4] == ["--model_name", model]
    assert arguments[4] == "--out_path"
    assert "--speaker_wav" not in arguments
    assert "--speaker_idx" not in arguments
