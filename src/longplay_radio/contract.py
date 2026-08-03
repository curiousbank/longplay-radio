"""Strict, bounded input contract for one approval-carrying continuity line."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID


class ContractError(ValueError):
    """Raised before any renderer or storage backend is constructed."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"job JSON contains duplicate field: {key}")
        result[key] = value
    return result


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$")
_WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b", re.I)
_URL_RE = re.compile(
    r"(?:https?://|www\.|(?<![@\w-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})(?![\w-]))",
    re.I,
)
_PHONE_CANDIDATE_RE = re.compile(r"(?<!\w)\+?[\d(). -]{10,32}(?!\w)")
_SSN_RE = re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'-]{1,50}\s+"
    r"(?:ST(?:REET)?|AVE(?:NUE)?|RD|ROAD|BLVD|DR(?:IVE)?|LN|LANE|CT|COURT)\b",
    re.I,
)
_LYRIC_LABEL_RE = re.compile(
    r"\b(?:lyrics?|verse|chorus|pre-chorus|bridge|refrain)\s*[:\[]",
    re.I,
)


def canonical_script(value: object) -> str:
    if not isinstance(value, str):
        raise ContractError("script must be text")
    if value != unicodedata.normalize("NFC", value):
        raise ContractError("script must use Unicode NFC")
    if value != value.strip() or "  " in value:
        raise ContractError("script must be trimmed with single spaces")
    if any(char != " " and char.isspace() for char in value) or any(
        unicodedata.category(char).startswith("C") for char in value
    ):
        raise ContractError("script must be one printable line")
    if not 20 <= len(value) <= 360:
        raise ContractError("script must contain 20-360 characters")
    return value


def script_sha256(value: str) -> str:
    return hashlib.sha256(canonical_script(value).encode("utf-8")).hexdigest()


def _strict_object(
    value: object,
    *,
    name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{name} must be a JSON object")
    allowed = required | (optional or set())
    missing = required - value.keys()
    extra = value.keys() - allowed
    if missing:
        raise ContractError(f"{name} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise ContractError(f"{name} has unknown fields: {', '.join(sorted(extra))}")
    return value


def _bounded_text(value: object, name: str, *, minimum: int = 1, maximum: int = 120) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ContractError(f"{name} must be trimmed text")
    if value != unicodedata.normalize("NFC", value):
        raise ContractError(f"{name} must use Unicode NFC")
    if not minimum <= len(value) <= maximum:
        raise ContractError(f"{name} length is outside {minimum}-{maximum}")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ContractError(f"{name} must be printable")
    return value


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be a UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ContractError(f"{name} must be a UUID string") from exc
    if str(parsed) != value.lower():
        raise ContractError(f"{name} must be a canonical UUID string")
    return parsed


def _bool_false(value: object, name: str) -> bool:
    if value is not False:
        raise ContractError(f"{name} must be false")
    return False


def _safe_copy(value: str, name: str) -> str:
    for pattern in (_EMAIL_RE, _URL_RE, _SSN_RE, _CARD_RE, _ADDRESS_RE):
        if pattern.search(value):
            raise ContractError(f"{name} contains contact, financial, or location data")
    for candidate in _PHONE_CANDIDATE_RE.finditer(value):
        digit_count = sum(char.isdigit() for char in candidate.group())
        if 10 <= digit_count <= 15:
            raise ContractError(f"{name} contains contact, financial, or location data")
    if _LYRIC_LABEL_RE.search(value):
        raise ContractError(f"{name} contains lyric markup")
    return value


def _contains(haystack: str, needle: str) -> bool:
    haystack_words = tuple(_WORD_RE.findall(haystack.casefold()))
    needle_words = tuple(_WORD_RE.findall(needle.casefold()))
    if not needle_words or len(needle_words) > len(haystack_words):
        return False
    width = len(needle_words)
    return any(
        haystack_words[index : index + width] == needle_words
        for index in range(len(haystack_words) - width + 1)
    )


def _reject_repeated_phrases(script: str) -> None:
    words = [word.casefold() for word in _WORD_RE.findall(script)]
    five_grams = [tuple(words[index : index + 5]) for index in range(len(words) - 4)]
    if len(five_grams) != len(set(five_grams)):
        raise ContractError("script contains a repeated five-word phrase")


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: UUID
    approved_by_ref: str
    approved_at: datetime
    rights_basis: str
    voice_rights_basis: str
    contains_song_lyrics: bool
    contains_personal_data: bool
    script_sha256: str

    @classmethod
    def parse(cls, value: object, *, expected_script_sha256: str) -> "Approval":
        data = _strict_object(
            value,
            name="approval",
            required={
                "approval_id",
                "approved_by_ref",
                "approved_at",
                "rights_basis",
                "voice_rights_basis",
                "contains_song_lyrics",
                "contains_personal_data",
                "script_sha256",
            },
        )
        approved_by = _bounded_text(data["approved_by_ref"], "approved_by_ref", maximum=96)
        if not _SAFE_REF_RE.fullmatch(approved_by):
            raise ContractError("approved_by_ref must be an opaque safe reference")
        approved_at_raw = data["approved_at"]
        if not isinstance(approved_at_raw, str):
            raise ContractError("approved_at must be an ISO-8601 string")
        try:
            approved_at = datetime.fromisoformat(approved_at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("approved_at must be a valid ISO-8601 time") from exc
        if approved_at.tzinfo is None or approved_at.utcoffset() is None:
            raise ContractError("approved_at must include a timezone")
        rights_basis = _bounded_text(data["rights_basis"], "rights_basis", maximum=64)
        if rights_basis not in {"original_continuity_copy", "licensed_continuity_copy"}:
            raise ContractError("rights_basis is not supported")
        voice_rights = _bounded_text(
            data["voice_rights_basis"], "voice_rights_basis", maximum=64
        )
        if voice_rights != "generic_synthetic_voice":
            raise ContractError("only generic synthetic voice rights are supported")
        digest = data["script_sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ContractError("script_sha256 must be lowercase SHA-256")
        if digest != expected_script_sha256:
            raise ContractError("approval hash does not match the canonical script")
        return cls(
            approval_id=_uuid(data["approval_id"], "approval_id"),
            approved_by_ref=approved_by,
            approved_at=approved_at,
            rights_basis=rights_basis,
            voice_rights_basis=voice_rights,
            contains_song_lyrics=_bool_false(
                data["contains_song_lyrics"], "contains_song_lyrics"
            ),
            contains_personal_data=_bool_false(
                data["contains_personal_data"], "contains_personal_data"
            ),
            script_sha256=digest,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "approval_id": str(self.approval_id),
            "approved_by_ref": self.approved_by_ref,
            "approved_at": self.approved_at.isoformat(),
            "rights_basis": self.rights_basis,
            "voice_rights_basis": self.voice_rights_basis,
            "contains_song_lyrics": self.contains_song_lyrics,
            "contains_personal_data": self.contains_personal_data,
            "script_sha256": self.script_sha256,
        }


@dataclass(frozen=True, slots=True)
class DJJob:
    schema_version: str
    job_id: UUID
    station_name: str
    show_name: str
    track_title: str
    artist_name: str
    script: str
    target_duration_seconds: int
    approval: Approval

    @classmethod
    def parse(cls, value: object) -> "DJJob":
        data = _strict_object(
            value,
            name="job",
            required={
                "schema_version",
                "job_id",
                "station_name",
                "show_name",
                "track_title",
                "artist_name",
                "script",
                "target_duration_seconds",
                "approval",
            },
        )
        if data["schema_version"] != "longplay.dj-job.v1":
            raise ContractError("unsupported schema_version")
        station = _safe_copy(
            _bounded_text(data["station_name"], "station_name", maximum=80),
            "station_name",
        )
        show = _safe_copy(
            _bounded_text(data["show_name"], "show_name", maximum=100),
            "show_name",
        )
        title = _safe_copy(
            _bounded_text(data["track_title"], "track_title", maximum=120),
            "track_title",
        )
        artist = _safe_copy(
            _bounded_text(data["artist_name"], "artist_name", maximum=120),
            "artist_name",
        )
        script = _safe_copy(canonical_script(data["script"]), "script")
        words = _WORD_RE.findall(script)
        if not 8 <= len(words) <= 55:
            raise ContractError("script must contain 8-55 words")
        _reject_repeated_phrases(script)
        for value_name, metadata in (
            ("station_name", station),
            ("track_title", title),
            ("artist_name", artist),
        ):
            if not _contains(script, metadata):
                raise ContractError(f"script must name {value_name}")
        duration = data["target_duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= 24:
            raise ContractError("target_duration_seconds must be an integer from 5 to 24")
        if len(words) / duration > 3.2:
            raise ContractError("script is too long for the target duration")
        digest = script_sha256(script)
        approval = Approval.parse(data["approval"], expected_script_sha256=digest)
        return cls(
            schema_version=data["schema_version"],
            job_id=_uuid(data["job_id"], "job_id"),
            station_name=station,
            show_name=show,
            track_title=title,
            artist_name=artist,
            script=script,
            target_duration_seconds=duration,
            approval=approval,
        )

    @classmethod
    def from_path(cls, path: Path, *, maximum_bytes: int = 32_768) -> "DJJob":
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ContractError("job file is not readable") from exc
        if not 1 <= size <= maximum_bytes:
            raise ContractError("job file is outside the 1-32768 byte bound")
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContractError("job file must contain UTF-8 JSON") from exc
        return cls.parse(value)

    def canonical_payload(self) -> bytes:
        value = {
            "schema_version": self.schema_version,
            "job_id": str(self.job_id),
            "station_name": self.station_name,
            "show_name": self.show_name,
            "track_title": self.track_title,
            "artist_name": self.artist_name,
            "script": self.script,
            "target_duration_seconds": self.target_duration_seconds,
            "approval": self.approval.public_dict(),
        }
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload()).hexdigest()
