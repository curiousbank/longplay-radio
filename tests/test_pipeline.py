from __future__ import annotations

import json

from genblaze_core import Manifest

from longplay_radio.config import Settings
from longplay_radio.contract import DJJob
from longplay_radio.pipeline import run_dj_job
from longplay_radio.storage import LocalStore


def test_fake_job_runs_through_genblaze_and_verified_storage(example_data, tmp_path):
    job = DJJob.parse(example_data)
    settings = Settings(
        renderer="fake",
        storage="local",
        local_root=tmp_path,
        object_prefix="longplay/genblaze/v1",
    )
    store = LocalStore(tmp_path, settings.object_prefix)
    receipt = run_dj_job(job, settings, store)
    assert receipt.manifest_verified is True
    assert receipt.approved_script_sha256 == job.approval.script_sha256
    assert store.get_verified(receipt.audio_object)[:4] == b"RIFF"
    manifest_bytes = store.get_verified(receipt.manifest_object)
    manifest = Manifest.model_validate_json(manifest_bytes)
    assert manifest.verify()
    assert manifest.canonical_hash == receipt.manifest_hash
    assert json.loads(store.get_verified(receipt.script_object))["script"] == job.script
