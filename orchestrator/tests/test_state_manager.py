"""
Atomic file ops.
"""

import json

from state_manager import atomic_write, atomic_append, atomic_save_json, load_json


def test_atomic_write_replaces(tmp_path):
    p = tmp_path / "foo.txt"
    atomic_write(p, "v1")
    atomic_write(p, "v2")
    assert p.read_text() == "v2"


def test_atomic_write_creates_parents(tmp_path):
    p = tmp_path / "nested" / "deep" / "file.txt"
    atomic_write(p, "hello")
    assert p.read_text() == "hello"


def test_atomic_append(tmp_path):
    p = tmp_path / "log.txt"
    atomic_append(p, "line1\n")
    atomic_append(p, "line2\n")
    assert p.read_text() == "line1\nline2\n"


def test_json_roundtrip(tmp_path):
    p = tmp_path / "data.json"
    data = {"a": 1, "b": [2, 3]}
    atomic_save_json(p, data)
    assert load_json(p) == data
    # Missing file returns default
    assert load_json(tmp_path / "missing.json", default={}) == {}
