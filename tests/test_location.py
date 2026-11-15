import os
import sys
from pathlib import Path

import pytest

from link.atomic import write_bytes_atomic
from link.location import FilesLocation, normalize_folder, saved_folder, suggestions


def test_the_typed_folder_becomes_an_absolute_path_or_a_message(tmp_path):
    home = tmp_path / "home"
    assert normalize_folder("~/Documents/Takumion", home) == home / "Documents" / "Takumion"
    assert normalize_folder(f"  {home}/a/../b  ", home) == home / "b"
    for bad, says in (("", "Type a folder"), ("relative/dir", "full path"), (os.sep, "top of the disk"),
                      (f"{home}/config/Takumion", '"config"'), (f"{home}/Secrets/x", '"Secrets"')):
        with pytest.raises(ValueError, match=says):
            normalize_folder(bad, home)
    (home).mkdir(parents=True)
    (home / "file.txt").write_text("x")
    with pytest.raises(ValueError, match="is a file"):
        normalize_folder(home / "file.txt", home)


def test_a_saved_folder_is_used_from_the_next_start(tmp_path):
    data, home = tmp_path / "data", tmp_path / "home"
    location = FilesLocation(data, home)
    assert location.active is None and location.state()["suggestions"] == suggestions(home)
    folder = location.choose("~/Documents/Takumion")
    assert folder.is_dir(), "a missing folder is created once, when saved"
    assert location.active is None and location.needs_restart, "Orca allows a folder only at load"
    assert saved_folder(data) == folder
    again = FilesLocation(data, home)
    assert again.active == folder and not again.needs_restart, "after a restart it is the folder in use"
    assert again.state() == {"active": str(folder), "saved": str(folder), "needsRestart": False,
                             "suggestions": suggestions(home)}


def test_load_asks_orca_for_the_whole_chosen_folder_once():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import takumion_link

    assert "fs_readwrite" not in takumion_link.permissions(None)
    wanted = takumion_link.permissions(Path("/home/u/Documents/Takumion"))
    assert wanted["fs_readwrite"] == [os.path.join("/home/u/Documents/Takumion", "*")]
    assert wanted["threading"] is True and len(wanted["network_socket"]) == 5


def test_writing_into_an_existing_folder_creates_no_folder_and_no_temp_via_mkstemp(tmp_path):
    """Orca asks about every folder or mkstemp file a plugin creates outside its
    data folder, and never remembers: a send must raise neither event."""
    folder = tmp_path / "Takumion"
    folder.mkdir()
    seen = []
    recording = [True]

    def hook(event, args):
        if recording[0] and event in ("os.mkdir", "tempfile.mkstemp", "tempfile.mkdtemp"):
            seen.append(event)

    sys.addaudithook(hook)
    try:
        write_bytes_atomic(folder / "Bracket - Base.stl", b"v1")
        write_bytes_atomic(folder / "Bracket - Base.stl", b"v2")
    finally:
        recording[0] = False
    assert seen == [] and (folder / "Bracket - Base.stl").read_bytes() == b"v2"
    assert [p.name for p in folder.iterdir()] == ["Bracket - Base.stl"], "no temp file left behind"
