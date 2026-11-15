from datetime import datetime

from link.atomic import read_json, write_bytes_atomic
from link.store import Link, LinkStore, display_name


def link(root, body="body_1", **over):
    base = dict(doc_key="prj_1", body_key=body, name="Base", format="3mf",
                path=str(root / "links" / "prj_1" / body / "Base.3mf"), sha256="ab", last_sent="2026-11-15T10:50:30Z")
    base.update(over)
    return Link(**base)


def test_flat_readable_layout_in_the_chosen_folder(tmp_path):
    store, files = LinkStore(tmp_path / "meta"), tmp_path / "Takumion"
    assert store.link_path(files, "prj_1", "body_1", "Bracket v2", "Base plate", "3mf") == files / "Bracket v2 - Base plate.3mf"
    now = datetime(2026, 11, 15, 10, 50, 30)
    first = store.copy_path(files, "Bracket", "Lid", "stl", now)
    assert first == files / "Bracket - Lid 2026-11-15 105030.stl"
    write_bytes_atomic(first, b"x")
    assert store.copy_path(files, "Bracket", "Lid", "stl", now).name == "Bracket - Lid 2026-11-15 105030 2.stl"


def test_a_name_another_body_owns_gets_its_key(tmp_path):
    store, files = LinkStore(tmp_path / "meta"), tmp_path / "Takumion"
    store.put(link(tmp_path, "body_aaaaaaaa1", path=str(files / "Untitled - Body 1.stl")))
    assert store.link_path(files, "prj_1", "body_aaaaaaaa1", "Untitled", "Body 1", "stl") == files / "Untitled - Body 1.stl"
    assert store.link_path(files, "prj_2", "body_bbbbbbbb2", "Untitled", "Body 1", "stl") == files / "Untitled - Body 1 (body_bbb).stl"
    assert store.link_path(files, "prj_2", "body_c", "untitled", "body 1", "stl").name == "untitled - body 1 (body_c).stl", \
        "case-only differences collide on Windows and macOS"


def test_names_read_well_and_stay_safe():
    assert display_name("../../etc/passwd", "Body") == "etc_passwd"
    assert display_name("Деталь 1", "Body") == "Деталь 1", "any script stays readable"
    assert display_name(".hidden", "Body") == "hidden"
    assert display_name("a/b:c*d", "Body") == "a_b_c_d"
    assert display_name("   ", "Body") == "Body"
    assert len(display_name("x" * 200, "Body")) == 60
    assert display_name("CON", "Body") == "_CON" and display_name("com1.part", "Body") == "_com1.part"


def test_put_get_persist(tmp_path):
    store = LinkStore(tmp_path)
    store.put(link(tmp_path, mesh_sig=(12.0, 1000.0)), "Bracket")
    again = LinkStore(tmp_path)
    got = again.get("prj_1", "body_1")
    assert got == link(tmp_path, mesh_sig=(12.0, 1000.0))
    assert again.doc_name("prj_1") == "Bracket"
    assert [b.body_key for b in again.bodies("prj_1")] == ["body_1"]


def test_drop_path(tmp_path):
    store = LinkStore(tmp_path)
    one, two = link(tmp_path), link(tmp_path, "body_2", path=str(tmp_path / "other.3mf"))
    store.put(one)
    store.put(two)
    assert store.drop_path(one.path) == [one]
    assert store.get("prj_1", "body_1") is None and store.get("prj_1", "body_2") == two


def test_corrupt_file_is_kept_aside(tmp_path):
    (tmp_path / "links.json").write_text("{not json")
    store = LinkStore(tmp_path)
    assert store.all_links() == []
    assert (tmp_path / "links.json.corrupt").exists()
    assert read_json(tmp_path / "missing.json", 7) == 7


def test_atomic_write_leaves_no_temp(tmp_path):
    target = tmp_path / "a" / "b.stl"
    write_bytes_atomic(target, b"one")
    write_bytes_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["b.stl"]


def test_atomic_write_opens_only_paths(tmp_path):
    """Orca's sandbox asks the user about an open() of a bare file descriptor
    ("FsReadWrite:open"); a path in the data folder needs no question."""
    import sys

    seen = []
    recording = [True]

    def hook(event, args):
        if recording[0] and event == "open":
            seen.append(args[0])

    sys.addaudithook(hook)
    try:
        write_bytes_atomic(tmp_path / "a" / "x.stl", b"data")
    finally:
        recording[0] = False
    assert seen and not any(isinstance(target, int) for target in seen), seen
