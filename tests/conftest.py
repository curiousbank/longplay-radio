from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest


@pytest.fixture
def example_data() -> dict:
    path = Path(__file__).parents[1] / "examples" / "dj_job.json"
    return json.loads(path.read_text())


@pytest.fixture
def changed(example_data):
    def change(**values):
        result = copy.deepcopy(example_data)
        result.update(values)
        return result

    return change
