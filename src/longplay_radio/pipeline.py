"""Run an approval-carrying DJ job through Genblaze and durable storage."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

from genblaze_core import (
    Manifest,
    Modality,
    Pipeline,
    PromptVisibility,
    RunBuilder,
    RunStatus,
    StepBuilder,
    StepStatus,
)

from longplay_radio.config import Settings
from longplay_radio.contract import DJJob
from longplay_radio.providers import (
    FAKE_MODEL,
    MAX_AUDIO_BYTES,
    CoquiAudioProvider,
    FakeAudioProvider,
    inspect_wav,
)
from longplay_radio.storage import ObjectReceipt, ObjectStore, sha256_bytes


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DJReceipt:
    schema_version: str
    job_id: str
    run_id: str
    provider_run_id: str
    job_payload_sha256: str
    approved_script_sha256: str
    renderer: str
    model: str
    manifest_hash: str
    manifest_object_sha256: str
    manifest_verified: bool
    script_object: ObjectReceipt
    audio_object: ObjectReceipt
    manifest_object: ObjectReceipt

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        return value

    def to_json(self) -> str:
        return json.dumps(self.public_dict(), sort_keys=True, indent=2)


def run_dj_job(job: DJJob, settings: Settings, store: ObjectStore) -> DJReceipt:
    run_id = uuid4()
    with tempfile.TemporaryDirectory(prefix="longplay-radio-") as directory:
        output_dir = Path(directory)
        if settings.renderer == "fake":
            provider = FakeAudioProvider(output_dir=output_dir)
            model = FAKE_MODEL
        elif settings.renderer == "coqui":
            if settings.coqui_bin is None or settings.coqui_model is None:
                raise PipelineError("Coqui renderer is not completely configured")
            provider = CoquiAudioProvider(
                executable=settings.coqui_bin,
                model=settings.coqui_model,
                output_dir=output_dir,
                timeout_seconds=settings.coqui_timeout_seconds,
            )
            model = settings.coqui_model
        else:
            raise PipelineError("unsupported renderer")

        try:
            result = (
                Pipeline("longplay-radio-continuity", project_id="longplay-radio")
                .step(
                    provider,
                    model=model,
                    prompt=job.script,
                    modality=Modality.AUDIO,
                )
                .run(
                    timeout=settings.coqui_timeout_seconds,
                    max_retries=1,
                    raise_on_failure=True,
                )
            )
            provider_run = result.run if hasattr(result, "run") else result[0]
            provider_run_id = str(provider_run.run_id)
            if not provider_run.steps or not provider_run.steps[-1].assets:
                raise PipelineError("Genblaze provider returned no audio asset")
            provider_asset = provider_run.steps[-1].assets[0]
            audio, duration, provider_metadata = _materialize_audio(
                provider_asset, output_dir, job
            )
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()

        script_bytes = job.canonical_payload()
        script_object = store.put_new(
            job_id=job.job_id,
            run_id=run_id,
            category="assets",
            data=script_bytes,
            extension="json",
            media_type="application/vnd.longplay.approved-script+json",
            metadata={
                "job-payload-sha256": job.payload_sha256,
                "approved-script-sha256": job.approval.script_sha256,
            },
        )
        audio_object = store.put_new(
            job_id=job.job_id,
            run_id=run_id,
            category="assets",
            data=audio,
            extension="wav",
            media_type="audio/wav",
            metadata={
                "source-text-sha256": job.approval.script_sha256,
                "renderer": settings.renderer,
            },
        )

        run = _build_durable_run(
            job=job,
            run_id=run_id,
            provider_run_id=provider_run_id,
            renderer_name=provider.name,
            model=model,
            duration=duration,
            provider_metadata=provider_metadata,
            script_object=script_object,
            audio_object=audio_object,
        )
        manifest = Manifest.from_run(run)
        if not manifest.verify():
            raise PipelineError(
                f"Genblaze manifest verification failed: {manifest.verification_report()}"
            )
        manifest_bytes = manifest.to_canonical_json().encode("utf-8")
        manifest_object = store.put_new(
            job_id=job.job_id,
            run_id=run_id,
            category="manifests",
            data=manifest_bytes,
            extension="json",
            media_type="application/vnd.genblaze.manifest+json",
            metadata={
                "genblaze-canonical-hash": manifest.canonical_hash,
                "manifest-object-sha256": sha256_bytes(manifest_bytes),
            },
        )

        # A final read checks that every receipt still names the exact bytes after
        # the manifest write, not merely immediately after each upload.
        store.get_verified(script_object)
        store.get_verified(audio_object)
        store.get_verified(manifest_object)

        return DJReceipt(
            schema_version="longplay.dj-receipt.v1",
            job_id=str(job.job_id),
            run_id=str(run_id),
            provider_run_id=provider_run_id,
            job_payload_sha256=job.payload_sha256,
            approved_script_sha256=job.approval.script_sha256,
            renderer=settings.renderer,
            model=model,
            manifest_hash=manifest.canonical_hash,
            manifest_object_sha256=sha256_bytes(manifest_bytes),
            manifest_verified=True,
            script_object=script_object,
            audio_object=audio_object,
            manifest_object=manifest_object,
        )


def _materialize_audio(asset, output_dir: Path, job: DJJob) -> tuple[bytes, float, dict]:
    parsed = urlparse(asset.url)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise PipelineError("renderer output must be a local file")
    path = Path(unquote(parsed.path)).resolve()
    if not path.is_relative_to(output_dir.resolve()):
        raise PipelineError("renderer output escaped its temporary directory")
    try:
        size, duration, _, _ = inspect_wav(path)
        audio = path.read_bytes()
    except OSError as exc:
        raise PipelineError("renderer output could not be materialized") from exc
    if size != len(audio) or len(audio) > MAX_AUDIO_BYTES:
        raise PipelineError("renderer output size changed during materialization")
    metadata = dict(asset.metadata or {})
    if metadata.get("source_text_sha256") != job.approval.script_sha256:
        raise PipelineError("renderer did not bind audio to the approved script")
    if duration > job.target_duration_seconds + 6:
        raise PipelineError("rendered audio exceeded the approved duration allowance")
    return audio, duration, metadata


def _build_durable_run(
    *,
    job: DJJob,
    run_id: UUID,
    provider_run_id: str,
    renderer_name: str,
    model: str,
    duration: float,
    provider_metadata: dict,
    script_object: ObjectReceipt,
    audio_object: ObjectReceipt,
):
    builder = (
        RunBuilder("LONGPLAY RADIO")
        .run_id(str(run_id))
        .tenant("longplay-radio")
        .project("longplay-radio")
        .status(RunStatus.COMPLETED)
        .meta(
            job_schema=job.schema_version,
            job_payload_sha256=job.payload_sha256,
            approved_script_sha256=job.approval.script_sha256,
            approval_id_sha256=hashlib.sha256(
                str(job.approval.approval_id).encode("utf-8")
            ).hexdigest(),
            provider_pipeline_run_id=provider_run_id,
            contains_song_lyrics=False,
            contains_personal_data=False,
        )
    )
    script_step = (
        StepBuilder("human-approval", "bounded-continuity-v1")
        .prompt(job.script)
        .visibility(PromptVisibility.PRIVATE)
        .modality(Modality.TEXT)
        .params(target_duration_seconds=job.target_duration_seconds)
        .status(StepStatus.SUCCEEDED)
        .meta(
            rights_basis=job.approval.rights_basis,
            voice_rights_basis=job.approval.voice_rights_basis,
        )
        .asset(
            script_object.immutable_uri,
            script_object.media_type,
            sha256=script_object.sha256,
            size_bytes=script_object.size_bytes,
            metadata={
                "storage_backend": script_object.backend,
                "storage_key": script_object.key,
                "storage_version_id": script_object.version_id,
            },
        )
        .build()
    )
    audio_step = (
        StepBuilder(renderer_name, model)
        .prompt(job.script)
        .visibility(PromptVisibility.PRIVATE)
        .modality(Modality.AUDIO)
        .params(target_duration_seconds=job.target_duration_seconds)
        .status(StepStatus.SUCCEEDED)
        .meta(
            source_text_sha256=job.approval.script_sha256,
            voice_cloning=False,
            generic_voice=True,
        )
        .asset(
            audio_object.immutable_uri,
            audio_object.media_type,
            sha256=audio_object.sha256,
            size_bytes=audio_object.size_bytes,
            duration=duration,
            metadata={
                **provider_metadata,
                "storage_backend": audio_object.backend,
                "storage_key": audio_object.key,
                "storage_version_id": audio_object.version_id,
            },
        )
        .build()
    )
    return builder.add_step(script_step).add_step(audio_step).build()
