from datetime import datetime

from link.atomic import read_json, write_bytes_atomic
from link.store import Link, LinkStore, folder_name, safe_name


def link(root, body="body_1", **over):
    base = dict(doc_key="prj_1", body_key=body, name="Base", format="3mf",
                path=str(root / "links" / "prj_1" / body / "Base.3mf"), sha256="ab", last_sent="2026-11-15T10:50:30Z")
    base.update(over)
    return Link(**base)


def test_layout(tmp_path):
    store = LinkStore(tmp_path)
    assert store.link_path("prj_1", "body_1", "Base plate", "3mf") == tmp_path / "links/prj_1/body_1/Base_plate.3mf"
    now = datetime(2026, 11, 15, 10, 50, 30)
    first = store.copy_path("prj_1", "Lid", "stl", now)
    assert first == tmp_path / "copies/prj_1/Lid-20261115-105030.stl"
    write_bytes_atomic(first, b"x")
    assert store.copy_path("prj_1", "Lid", "stl", now).name == "Lid-20261115-105030-2.stl"


def test_names_are_safe_for_orca():
    assert safe_name("../../etc/passwd") == "etc_passwd"
    assert safe_name("Деталь") == "body"
    assert safe_name(".hidden") == "hidden"
    assert len(safe_name("x" * 200)) == 60
    assert folder_name("config") == "_config" and folder_name("Secret") == "_Secret"
    assert folder_name("body_1") == "body_1"


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
