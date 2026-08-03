from __future__ import annotations

import io
from uuid import uuid4

import pytest

from longplay_radio.storage import B2S3Store, LocalStore, ObjectReceipt, StorageError


def _put(store, data=b"approved bytes"):
    return store.put_new(
        job_id=uuid4(),
        run_id=uuid4(),
        category="assets",
        data=data,
        extension="json",
        media_type="application/json",
    )


def test_local_store_round_trips_exact_bytes(tmp_path):
    store = LocalStore(tmp_path, "longplay/genblaze/v1")
    receipt = _put(store)
    assert store.get_verified(receipt) == b"approved bytes"
    assert receipt.version_id.startswith("local-sha256:")
    assert "approved" not in receipt.key


def test_local_store_rejects_forged_version(tmp_path):
    store = LocalStore(tmp_path, "longplay/genblaze/v1")
    receipt = _put(store)
    forged = ObjectReceipt(
        receipt.backend,
        receipt.store_id,
        receipt.key,
        "local-sha256:" + "0" * 64,
        receipt.media_type,
        receipt.sha256,
        receipt.size_bytes,
    )
    with pytest.raises(StorageError, match="content-bound"):
        store.get_verified(forged)


class FakeS3:
    def __init__(self, *, corrupt=False, wrong_version=False):
        self.objects = {}
        self.corrupt = corrupt
        self.wrong_version = wrong_version
        self.get_calls = []

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = (bytes(kwargs["Body"]), kwargs["Metadata"])
        return {"VersionId": "4_z_exact_version"}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        data, metadata = self.objects[kwargs["Key"]]
        if self.corrupt:
            data += b"x"
        return {
            "VersionId": "4_z_wrong" if self.wrong_version else kwargs["VersionId"],
            "ContentLength": len(data),
            "Metadata": metadata,
            "Body": io.BytesIO(data),
        }

    def close(self):
        pass


def test_b2_requires_and_reads_exact_version():
    client = FakeS3()
    store = B2S3Store(
        bucket="public-test-bucket",
        region="us-test-001",
        client=client,
        prefix="longplay/genblaze/v1",
    )
    receipt = _put(store)
    assert receipt.version_id == "4_z_exact_version"
    assert client.get_calls[0]["VersionId"] == "4_z_exact_version"
    assert store.get_verified(receipt) == b"approved bytes"


def test_b2_rejects_different_returned_version():
    store = B2S3Store(
        bucket="public-test-bucket",
        region="us-test-001",
        client=FakeS3(wrong_version=True),
        prefix="longplay/genblaze/v1",
    )
    with pytest.raises(StorageError, match="different VersionId"):
        _put(store)


def test_b2_rejects_changed_size_or_hash():
    store = B2S3Store(
        bucket="public-test-bucket",
        region="us-test-001",
        client=FakeS3(corrupt=True),
        prefix="longplay/genblaze/v1",
    )
    with pytest.raises(StorageError, match="different object size"):
        _put(store)


def test_receipt_is_bound_to_one_opaque_store_identity(tmp_path):
    first = LocalStore(tmp_path / "one", "longplay/genblaze/v1")
    second = LocalStore(tmp_path / "two", "longplay/genblaze/v1")
    receipt = _put(first)
    assert receipt.store_id != second.store_id
    with pytest.raises(StorageError, match="backend mismatch"):
        second.get_verified(receipt)
