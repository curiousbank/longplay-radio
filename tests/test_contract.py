from __future__ import annotations

import hashlib
import json

import pytest

from longplay_radio.contract import ContractError, DJJob


def test_synthetic_job_is_valid(example_data):
    job = DJJob.parse(example_data)
    assert job.approval.script_sha256 == hashlib.sha256(job.script.encode()).hexdigest()
    assert job.payload_sha256 != job.approval.script_sha256


@pytest.mark.parametrize(
    "script",
    [
        "LONGPLAY. Lyrics: sing this Blue Horizon verse by The Local Satellites tonight.",
        "LONGPLAY. Email host@example.com before Blue Horizon by The Local Satellites.",
        "LONGPLAY. Visit https://example.com for Blue Horizon by The Local Satellites.",
        "LONGPLAY. Call 313-555-1212 for Blue Horizon by The Local Satellites.",
        "LONGPLAY. Call 3135551212 for Blue Horizon by The Local Satellites.",
        "LONGPLAY. Call (313)555-1212 for Blue Horizon by The Local Satellites.",
        "LONGPLAY. Call +44 20 7946 0958 for Blue Horizon by The Local Satellites.",
        "LONGPLAY. Visit example.ai for Blue Horizon by The Local Satellites.",
        "LONGPLAY. one two three four five one two three four five Blue Horizon by The Local Satellites.",
    ],
)
def test_rejects_lyric_private_link_and_refrain_copy(example_data, script):
    example_data["script"] = script
    example_data["approval"]["script_sha256"] = hashlib.sha256(script.encode()).hexdigest()
    with pytest.raises(ContractError):
        DJJob.parse(example_data)


def test_rejects_hash_mismatch(example_data):
    example_data["script"] += " Now."
    with pytest.raises(ContractError, match="hash"):
        DJJob.parse(example_data)


@pytest.mark.parametrize("separator", ["\n", "\r", "\t", "\u2028", "\u2029", "\u00a0"])
def test_rejects_every_non_ascii_line_or_space_separator(example_data, separator):
    script = example_data["script"].replace(" Between", separator + "Between", 1)
    example_data["script"] = script
    example_data["approval"]["script_sha256"] = hashlib.sha256(script.encode()).hexdigest()
    with pytest.raises(ContractError, match="one printable line"):
        DJJob.parse(example_data)


def test_rejects_unknown_job_field(example_data):
    example_data["provider"] = "arbitrary.module.Provider"
    with pytest.raises(ContractError, match="unknown"):
        DJJob.parse(example_data)


def test_requires_metadata_in_script(example_data):
    example_data["artist_name"] = "Someone Else"
    with pytest.raises(ContractError, match="artist_name"):
        DJJob.parse(example_data)


@pytest.mark.parametrize(
    ("field", "partial"),
    [("station_name", "LONG"), ("track_title", "Blue Horiz")],
)
def test_metadata_requires_exact_contiguous_tokens(example_data, field, partial):
    example_data[field] = partial
    with pytest.raises(ContractError, match=field):
        DJJob.parse(example_data)


@pytest.mark.parametrize("field", ["station_name", "rights_basis"])
def test_file_parser_rejects_duplicate_top_level_and_nested_members(
    example_data, tmp_path, field
):
    encoded = json.dumps(example_data, indent=2)
    indentation = "  " if field == "station_name" else "    "
    marker = f'{indentation}"{field}":'
    position = encoded.index(marker)
    line_end = encoded.index("\n", position)
    original_line = encoded[position:line_end]
    duplicate_line = original_line.replace(
        f'"{field}":', f'"{field}": "shadowed", "{field}":', 1
    )
    path = tmp_path / "duplicate.json"
    path.write_text(encoded[:position] + duplicate_line + encoded[line_end:])
    with pytest.raises(ContractError, match=f"duplicate field: {field}"):
        DJJob.from_path(path)
