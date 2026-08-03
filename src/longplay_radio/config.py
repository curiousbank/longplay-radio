"""Environment-owned worker configuration; no job can alter these values."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    pass


_PREFIX_RE = re.compile(r"^longplay/genblaze/v[1-9][0-9]*$")
_MODEL_RE = re.compile(r"^tts_models/[A-Za-z0-9._/-]{3,180}$")
_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{4,48}[A-Za-z0-9]$")
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def secure_executable(value: str, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise ConfigurationError(f"{name} is not available") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ConfigurationError(f"{name} must be an executable regular file")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ConfigurationError(f"{name} must not be group/world writable")
    return resolved


@dataclass(frozen=True, slots=True)
class Settings:
    renderer: str
    storage: str
    local_root: Path
    object_prefix: str
    coqui_bin: Path | None = None
    coqui_model: str | None = None
    coqui_timeout_seconds: int = 120
    b2_bucket: str | None = None
    b2_region: str | None = None
    b2_key_id: str | None = None
    b2_app_key: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        renderer = os.environ.get("LONGPLAY_RENDERER", "fake")
        if renderer not in {"fake", "coqui"}:
            raise ConfigurationError("LONGPLAY_RENDERER must be fake or coqui")
        storage = os.environ.get("LONGPLAY_STORAGE", "local")
        if storage not in {"local", "b2_s3"}:
            raise ConfigurationError("LONGPLAY_STORAGE must be local or b2_s3")
        prefix = os.environ.get("LONGPLAY_OBJECT_PREFIX", "longplay/genblaze/v1").strip("/")
        if not _PREFIX_RE.fullmatch(prefix):
            raise ConfigurationError("LONGPLAY_OBJECT_PREFIX is outside the public namespace")

        kwargs: dict[str, object] = {
            "renderer": renderer,
            "storage": storage,
            "local_root": Path(os.environ.get("LONGPLAY_LOCAL_ROOT", "./var/storage")),
            "object_prefix": prefix,
            "coqui_timeout_seconds": _integer(
                "LONGPLAY_COQUI_TIMEOUT_SECONDS", 120, minimum=10, maximum=600
            ),
        }

        if renderer == "coqui":
            binary = os.environ.get("LONGPLAY_COQUI_BIN", "")
            model = os.environ.get("LONGPLAY_COQUI_MODEL", "")
            if not _MODEL_RE.fullmatch(model):
                raise ConfigurationError("LONGPLAY_COQUI_MODEL is not a safe model name")
            kwargs.update(
                coqui_bin=secure_executable(binary, "LONGPLAY_COQUI_BIN"),
                coqui_model=model,
            )

        if storage == "b2_s3":
            bucket = os.environ.get("B2_BUCKET", "")
            region = os.environ.get("B2_REGION", "")
            key_id = os.environ.get("B2_KEY_ID", "")
            app_key = os.environ.get("B2_APP_KEY", "")
            if not _BUCKET_RE.fullmatch(bucket):
                raise ConfigurationError("B2_BUCKET is not a safe bucket name")
            if not _REGION_RE.fullmatch(region):
                raise ConfigurationError("B2_REGION is not a safe region slug")
            if not key_id or not app_key:
                raise ConfigurationError("B2 credentials are required for b2_s3")
            kwargs.update(
                b2_bucket=bucket,
                b2_region=region,
                b2_key_id=key_id,
                b2_app_key=app_key,
            )

        return cls(**kwargs)

    def public_status(self) -> dict[str, object]:
        return {
            "renderer": self.renderer,
            "storage": self.storage,
            "object_prefix": self.object_prefix,
            "coqui_configured": self.coqui_bin is not None,
            "b2_credentials_present": bool(self.b2_key_id and self.b2_app_key),
        }
