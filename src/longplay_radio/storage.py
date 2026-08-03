"""Write-once local and exact-version Backblaze B2 object storage."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

from longplay_radio.config import Settings


class StorageError(RuntimeError):
    pass


_KEY_RE = re.compile(
    r"^longplay/genblaze/v[1-9][0-9]*/jobs/"
    r"[0-9a-f-]{36}/runs/[0-9a-f-]{36}/"
    r"(?:assets|manifests)/[0-9a-f-]{36}/[0-9a-f]{64}\.[a-z0-9]{1,12}$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:+/=-]{0,511}$")
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.+-]*$")
_METADATA_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_OBJECT_BYTES = 16 * 1024 * 1024
_AMBIGUOUS_VERSIONS = {"latest", "null", "none", "unversioned"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store_id(backend: str, *coordinates: str) -> str:
    material = "\0".join((backend, *coordinates)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _validate_key(key: str) -> None:
    if (
        not isinstance(key, str)
        or len(key.encode("utf-8")) > 1024
        or not _KEY_RE.fullmatch(key)
        or "//" in key
        or any(part in {".", ".."} for part in key.split("/"))
    ):
        raise StorageError("object key is outside the LONGPLAY namespace")


def _valid_version(value: object) -> bool:
    return (
        isinstance(value, str)
        and _VERSION_RE.fullmatch(value) is not None
        and value.lower() not in _AMBIGUOUS_VERSIONS
    )


@dataclass(frozen=True, slots=True)
class ObjectReceipt:
    backend: str
    store_id: str
    key: str
    version_id: str
    media_type: str
    sha256: str
    size_bytes: int

    @property
    def immutable_uri(self) -> str:
        backend = quote(self.backend, safe="")
        store_id = quote(self.store_id, safe="")
        key = quote(self.key, safe="/")
        version = quote(self.version_id, safe="")
        return f"longplay-storage:///{backend}/{store_id}/{key}?versionId={version}"

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


class ObjectStore(ABC):
    backend: str
    store_id: str

    def __init__(self, prefix: str):
        self.prefix = prefix

    def object_key(
        self,
        *,
        job_id: UUID,
        run_id: UUID,
        category: str,
        digest: str,
        extension: str,
    ) -> str:
        if category not in {"assets", "manifests"}:
            raise StorageError("unknown object category")
        if not _SHA256_RE.fullmatch(digest):
            raise StorageError("object digest must be lowercase SHA-256")
        if not re.fullmatch(r"[a-z0-9]{1,12}", extension):
            raise StorageError("object extension is unsafe")
        key = (
            f"{self.prefix}/jobs/{job_id}/runs/{run_id}/{category}/"
            f"{uuid4()}/{digest}.{extension}"
        )
        _validate_key(key)
        return key

    def put_new(
        self,
        *,
        job_id: UUID,
        run_id: UUID,
        category: str,
        data: bytes,
        extension: str,
        media_type: str,
        metadata: dict[str, str] | None = None,
    ) -> ObjectReceipt:
        if not isinstance(data, bytes) or not 1 <= len(data) <= _MAX_OBJECT_BYTES:
            raise StorageError("object is outside the 1-16 MiB bound")
        if not _MEDIA_TYPE_RE.fullmatch(media_type):
            raise StorageError("object media type is invalid")
        digest = sha256_bytes(data)
        key = self.object_key(
            job_id=job_id,
            run_id=run_id,
            category=category,
            digest=digest,
            extension=extension,
        )
        receipt = self.put_verified(
            key=key,
            data=data,
            media_type=media_type,
            metadata={**(metadata or {}), "sha256": digest},
        )
        if (
            receipt.store_id != self.store_id
            or receipt.sha256 != digest
            or receipt.size_bytes != len(data)
        ):
            raise StorageError("storage receipt did not match object bytes")
        return receipt

    @abstractmethod
    def put_verified(
        self,
        *,
        key: str,
        data: bytes,
        media_type: str,
        metadata: dict[str, str],
    ) -> ObjectReceipt:
        pass

    @abstractmethod
    def get_verified(self, receipt: ObjectReceipt) -> bytes:
        pass

    def close(self) -> None:
        return None


class LocalStore(ObjectStore):
    backend = "local"

    def __init__(self, root: Path, prefix: str):
        super().__init__(prefix)
        self.root = root.resolve()
        self.store_id = _store_id(self.backend, str(self.root))
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError("object key escaped local storage root")
        return path

    def put_verified(
        self,
        *,
        key: str,
        data: bytes,
        media_type: str,
        metadata: dict[str, str],
    ) -> ObjectReceipt:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256_bytes(data)
        handle, temporary_name = tempfile.mkstemp(prefix=".longplay-", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, path)
            except FileExistsError as exc:
                raise StorageError("unique local object key already exists") from exc
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        receipt = ObjectReceipt(
            backend=self.backend,
            store_id=self.store_id,
            key=key,
            version_id=f"local-sha256:{digest}",
            media_type=media_type,
            sha256=digest,
            size_bytes=len(data),
        )
        self.get_verified(receipt)
        return receipt

    def get_verified(self, receipt: ObjectReceipt) -> bytes:
        if receipt.backend != self.backend or receipt.store_id != self.store_id:
            raise StorageError("storage receipt backend mismatch")
        expected_version = f"local-sha256:{receipt.sha256}"
        if receipt.version_id != expected_version:
            raise StorageError("local receipt version is not content-bound")
        try:
            data = self._path(receipt.key).read_bytes()
        except OSError as exc:
            raise StorageError("local object read-back failed") from exc
        _verify_download(data, receipt)
        return data


class _Body(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def close(self) -> None: ...


class B2S3Store(ObjectStore):
    backend = "b2_s3"

    def __init__(self, *, bucket: str, region: str, client: S3Client, prefix: str):
        super().__init__(prefix)
        self.bucket = bucket
        self.region = region
        self.store_id = _store_id(self.backend, region, bucket)
        self.client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "B2S3Store":
        if not all(
            (
                settings.b2_bucket,
                settings.b2_region,
                settings.b2_key_id,
                settings.b2_app_key,
            )
        ):
            raise StorageError("complete B2 S3 configuration is required")
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=f"https://s3.{settings.b2_region}.backblazeb2.com",
            region_name=settings.b2_region,
            aws_access_key_id=settings.b2_key_id,
            aws_secret_access_key=settings.b2_app_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
                connect_timeout=30,
                read_timeout=300,
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                s3={"addressing_style": "path"},
            ),
        )
        return cls(
            bucket=settings.b2_bucket,
            region=settings.b2_region,
            client=client,
            prefix=settings.object_prefix,
        )

    def put_verified(
        self,
        *,
        key: str,
        data: bytes,
        media_type: str,
        metadata: dict[str, str],
    ) -> ObjectReceipt:
        _validate_key(key)
        digest = sha256_bytes(data)
        clean_metadata = _validated_metadata({**metadata, "sha256": digest})
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentLength=len(data),
                ContentType=media_type,
                Metadata=clean_metadata,
            )
        except Exception as exc:
            raise StorageError("B2 PutObject failed") from exc
        version_id = response.get("VersionId")
        if not _valid_version(version_id):
            raise StorageError("B2 did not return an exact object VersionId")
        receipt = ObjectReceipt(
            backend=self.backend,
            store_id=self.store_id,
            key=key,
            version_id=version_id,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(data),
        )
        self.get_verified(receipt)
        return receipt

    def get_verified(self, receipt: ObjectReceipt) -> bytes:
        if (
            receipt.backend != self.backend
            or receipt.store_id != self.store_id
            or not _valid_version(receipt.version_id)
        ):
            raise StorageError("B2 receipt does not name an exact version")
        _validate_key(receipt.key)
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=receipt.key,
                VersionId=receipt.version_id,
            )
        except Exception as exc:
            raise StorageError("B2 exact-version GetObject failed") from exc
        body = response.get("Body")
        if response.get("VersionId") != receipt.version_id:
            _close_body(body)
            raise StorageError("B2 returned a different VersionId")
        if response.get("ContentLength") != receipt.size_bytes:
            _close_body(body)
            raise StorageError("B2 returned a different object size")
        metadata = response.get("Metadata")
        if not isinstance(metadata, dict) or metadata.get("sha256") != receipt.sha256:
            _close_body(body)
            raise StorageError("B2 SHA-256 metadata did not match")
        if body is None or not hasattr(body, "read"):
            raise StorageError("B2 response had no readable body")
        try:
            data = body.read(receipt.size_bytes + 1)
        except Exception as exc:
            raise StorageError("B2 response body read failed") from exc
        finally:
            _close_body(body)
        _verify_download(data, receipt)
        return data

    def close(self) -> None:
        self.client.close()


def _validated_metadata(metadata: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not _METADATA_KEY_RE.fullmatch(key):
            raise StorageError("object metadata key is invalid")
        if (
            not isinstance(value, str)
            or len(value.encode("utf-8")) > 1024
            or _CONTROL_RE.search(value)
        ):
            raise StorageError("object metadata value is invalid")
        result[key] = value
    return result


def _close_body(body: object) -> None:
    close = getattr(body, "close", None)
    if callable(close):
        close()


def _verify_download(data: bytes, receipt: ObjectReceipt) -> None:
    if len(data) != receipt.size_bytes:
        raise StorageError("read-back size did not match receipt")
    if sha256_bytes(data) != receipt.sha256:
        raise StorageError("read-back SHA-256 did not match receipt")


def store_from_settings(settings: Settings) -> ObjectStore:
    if settings.storage == "local":
        return LocalStore(settings.local_root, settings.object_prefix)
    if settings.storage == "b2_s3":
        return B2S3Store.from_settings(settings)
    raise StorageError("unsupported storage backend")
