import os
import time

import pytest

from link.handoff import Handoff, HandoffUnavailable, escape_strings_cstyle, instance_hashes, orca_message
from link.handoff_linux import NAME_PREFIX, pick_name


def unescape_strings_cstyle(text: str) -> list[str]:
    """Orca's unescape_strings_cstyle (libslic3r/Config.cpp), transcribed for the test."""
    out, i = [], 0
    if not text:
        return out
    while True:
        while i < len(text) and text[i] in " \t":
            i += 1
        if i == len(text):
            return out
        buf = []
        if text[i] == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                c = text[i]
                if c == "\\":
                    i += 1
                    c = {"r": "\r", "n": "\n"}.get(text[i], text[i])
                buf.append(c)
                i += 1
            assert i < len(text), "unterminated quote"
            i += 1
        else:
            while i < len(text) and text[i] != ";":
                buf.append(text[i])
                i += 1
        out.append("".join(buf))
        while i < len(text) and text[i] in " \t":
            i += 1
        if i == len(text):
            return out
        assert text[i] == ";"
        i += 1


@pytest.mark.parametrize("path", [
    "/home/u/.var/app/com.orcaslicer.OrcaSlicer/config/OrcaSlicer/takumion/links/p/b/Base.3mf",
    "/tmp/with space/a;b.stl",
    'C:\\Users\\me\\"quoted"\\x.stl',
    "/odd\nname\r.stl",
])
def test_message_round_trips_through_orcas_parser(path):
    assert unescape_strings_cstyle(orca_message(path)) == ["takumion-link", path]


def test_simple_values_stay_unquoted():
    assert escape_strings_cstyle(["a", "/b/c.stl"]) == "a;/b/c.stl"
    assert escape_strings_cstyle([""]) == '""'


def test_handoff_retries_until_orca_answers(tmp_path):
    calls = []

    def deliver(message, data_dir):
        calls.append(message)
        if len(calls) < 3:
            raise HandoffUnavailable("not yet")

    Handoff(tmp_path, deliver, retry_for=5, retry_every=0, sleep=lambda s: None).deliver("/x.stl")
    assert len(calls) == 3


def test_handoff_gives_up(tmp_path):
    def deliver(message, data_dir):
        raise ConnectionRefusedError("no bus")

    with pytest.raises(HandoffUnavailable):
        Handoff(tmp_path, deliver, retry_for=0.05, retry_every=0.01).deliver("/x.stl")


def test_pick_name_prefers_the_newest_lock():
    names = ["org.freedesktop.DBus", NAME_PREFIX + "111", NAME_PREFIX + "222", ":1.5"]
    assert pick_name(names, ["222", "111"]) == NAME_PREFIX + "222"
    assert pick_name(names, ["999"]) == NAME_PREFIX + "111"
    assert pick_name(["org.x"], []) is None


def test_instance_hashes_newest_first(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    for name, age in (("111.lock", 100), ("222.lock", 10), ("cookies.db", 0)):
        (cache / name).write_text("")
        os.utime(cache / name, (time.time() - age, time.time() - age))
    assert instance_hashes(tmp_path) == ["222", "111"]
    assert instance_hashes(tmp_path / "nope") == []
