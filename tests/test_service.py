"""Send behaviour against a simulated Orca (conftest.FakeOrca)."""

import base64
from pathlib import Path

import pytest

from link.protocol import LinkError, parse_send


def send(world, mode="update", fmt="stl", parts=(("body_1", "Base", b"v1"),)):
    req = parse_send({"doc": {"key": "prj_1", "name": "Bracket"}, "mode": mode, "format": fmt,
                      "parts": [{"bodyKey": k, "name": n, "data": base64.b64encode(d).decode()} for k, n, d in parts]})
    return world.service.send(req)


def only(response):
    assert len(response["results"]) == 1
    return response["results"][0]


def test_first_update_adds_and_links(world):
    result = only(send(world))
    assert result == {"bodyKey": "body_1", "action": "added", "onPlate": True, "reloadNeeded": False}
    link = world.store.get("prj_1", "body_1")
    assert Path(link.path).read_bytes() == b"v1"
    assert link.path == str(world.files / "Bracket - Base.stl"), "flat and readable, in the chosen folder"
    assert world.orca.delivered == [link.path]


def test_one_hand_off_per_file(world):
    response = send(world, parts=(("body_1", "Base", b"a"), ("body_2", "Lid", b"b")))
    assert [r["action"] for r in response["results"]] == ["added", "added"]
    assert len(world.orca.delivered) == 2 and world.orca.delivered[0] != world.orca.delivered[1]


def test_update_unchanged_body_needs_no_reload(world):
    send(world)
    result = only(send(world))
    assert result == {"bodyKey": "body_1", "action": "updated", "onPlate": None, "reloadNeeded": False}
    assert len(world.orca.delivered) == 1
    assert world.notes.items == []


def test_update_changed_body_overwrites_and_asks_for_reload(world):
    send(world)
    path = world.store.get("prj_1", "body_1").path
    result = only(send(world, parts=(("body_1", "Base v2", b"v2"),)))
    assert result == {"bodyKey": "body_1", "action": "updated", "onPlate": None, "reloadNeeded": True}
    assert Path(path).read_bytes() == b"v2", "same file, overwritten in place"
    assert world.store.get("prj_1", "body_1").path == path, "renaming the body keeps its path"
    assert len(world.orca.delivered) == 1
    assert "Reload from disk" in world.notes.items[-1][1]
    links = world.service.links("prj_1")
    assert links["bodies"][0]["reloadNeeded"] is True and links["bodies"][0]["onPlate"] is True


def test_reload_from_disk_clears_the_badge(world):
    send(world)
    send(world, parts=(("body_1", "Base", b"v2"),))
    path = world.store.get("prj_1", "body_1").path
    world.orca.plate.refresh(world.orca.objects, "", "ObjectTransformed")  # a move is not a reload
    assert world.store.get("prj_1", "body_1").reload_needed is True
    world.orca.reload(path, (24.0, 2000.0))
    assert world.store.get("prj_1", "body_1").reload_needed is False
    assert only(send(world, parts=(("body_1", "Base", b"v2"),)))["reloadNeeded"] is False


def test_update_after_delete_puts_it_back(world):
    send(world)
    path = world.store.get("prj_1", "body_1").path
    world.orca.delete(path)
    result = only(send(world, parts=(("body_1", "Base", b"v1"),)))
    assert result["action"] == "added" and result["onPlate"] is True
    assert world.orca.delivered == [path, path]


def test_add_writes_a_new_copy_each_time(world):
    one = only(send(world, mode="add"))
    two = only(send(world, mode="add"))
    assert one["action"] == two["action"] == "added"
    assert len(set(world.orca.delivered)) == 2
    assert all(p.startswith(str(world.files / "Bracket - Base 2026-11-15 105030")) for p in world.orca.delivered)
    assert world.store.get("prj_1", "body_1") is None, "add mode is never linked"


def test_format_change_gets_its_own_file(world):
    send(world, fmt="stl")
    result = only(send(world, fmt="3mf"))
    assert result["action"] == "added"
    assert world.store.get("prj_1", "body_1").path.endswith("Base.3mf")


def test_3mf_that_lands_confirms_setup(world):
    response = send(world, fmt="3mf")
    assert response["needsOrcaSetup"] is False
    assert world.service.hello(False)["setup"]["loadGeometryOnly"] == "confirmed"
    assert world.service.setup()["showCard"] is False


def test_3mf_that_does_not_land_asks_for_setup(world):
    world.orca.behaviour = "ignore"  # Orca's "project or geometry" dialog is up
    response = send(world, fmt="3mf")
    assert response["needsOrcaSetup"] is True
    assert only(response)["onPlate"] is False
    assert world.service.hello(False)["setup"]["loadGeometryOnly"] == "missing"
    assert world.service.setup()["showCard"] is True
    assert world.notes.items[-1][0] == "warning"


def test_3mf_opened_as_project_drops_the_link(world):
    world.orca.behaviour = "project"
    response = send(world, fmt="3mf")
    assert response["needsOrcaSetup"] is True
    assert world.store.get("prj_1", "body_1") is None, "the next send writes a fresh file"
    assert any("as its project" in text for _, text in world.notes.items)


def test_stl_never_touches_setup(world):
    world.orca.behaviour = "ignore"
    response = send(world, fmt="stl")
    assert response["needsOrcaSetup"] is False
    assert world.service.hello(False)["setup"]["loadGeometryOnly"] == "unknown"


def test_orca_not_ready_is_busy(world):
    world.orca.behaviour = "unavailable"
    with pytest.raises(LinkError) as caught:
        send(world)
    assert caught.value.code == "orca-busy"


def test_setup_card_dismiss_persists(world, data_dir):
    world.service.dismiss_setup_card()
    assert world.service.setup()["showCard"] is False
    assert '"setupCardDismissed": true' in (data_dir / "takumion" / "state.json").read_text()


def test_links_and_docs(world):
    send(world, parts=(("body_1", "Base", b"a"), ("body_2", "Lid", b"b")))
    links = world.service.links("prj_1")
    assert links["doc"] == {"key": "prj_1", "name": "Bracket"}
    assert [b["bodyKey"] for b in links["bodies"]] == ["body_1", "body_2"]
    assert links["bodies"][0]["lastSent"].endswith("Z")
    assert world.service.links("other")["bodies"] == []
    assert world.service.docs()[0]["key"] == "prj_1"
    with pytest.raises(LinkError):
        world.service.links(None)


def test_no_folder_yet_refuses_the_send_and_says_where_to_set_it(world):
    from link.protocol import LinkError

    world.location.active = world.location.saved = None
    try:
        send(world)
    except LinkError as error:
        assert error.code == "setup-needed" and "Open Takumion Link" in error.message
    else:
        raise AssertionError("a send with no folder must be refused")
    assert world.orca.delivered == [] and "choose where" in world.notes.items[-1][1].lower()
    world.location.saved = world.files / "elsewhere"
    try:
        send(world)
    except LinkError as error:
        assert error.code == "setup-needed" and "Restart OrcaSlicer" in error.message
    assert world.service.hello(False).get("files") is None, "an unpaired caller never sees the folder (it names the user)"
    assert world.service.hello(True)["files"] == {"folder": None, "ready": False, "needsRestart": True}


def test_a_body_linked_before_the_folder_changed_gets_a_file_in_the_new_folder(world, tmp_path):
    send(world)
    old = world.store.get("prj_1", "body_1").path
    moved = tmp_path / "New place"
    moved.mkdir()
    world.location.active = world.location.saved = moved
    result = only(send(world, parts=(("body_1", "Base", b"v2"),)))
    link = world.store.get("prj_1", "body_1")
    assert result["action"] == "added" and link.path == str(moved / "Bracket - Base.stl") and link.path != old


def send_with_project(world, cad, doc=("prj_1", "Bracket")):
    req = parse_send({"doc": {"key": doc[0], "name": doc[1]}, "mode": "update", "format": "stl",
                      "parts": [{"bodyKey": "body_1", "name": "Base", "data": base64.b64encode(b"v1").decode()}],
                      "project": {"data": base64.b64encode(cad).decode()}})
    return world.service.send(req)


def test_the_project_file_lands_beside_its_bodies_and_is_never_handed_to_orca(world):
    first = send_with_project(world, b"cad-1")
    cad = world.files / "Bracket.cad"
    assert first["project"] == {"action": "added", "file": "Bracket.cad"} and cad.read_bytes() == b"cad-1"
    assert all(not p.endswith(".cad") for p in world.orca.delivered), "Orca gets meshes only"
    assert send_with_project(world, b"cad-1")["project"]["action"] == "unchanged"
    assert send_with_project(world, b"cad-2")["project"]["action"] == "updated" and cad.read_bytes() == b"cad-2"
    other = send_with_project(world, b"other", doc=("prj_2xxxxxxx", "Bracket"))
    assert other["project"]["file"] == "Bracket (prj_2xxx).cad", "another project of the same name gets its key"
    assert "project" not in send(world), "no project sent, none written or reported"


def test_a_malformed_project_is_a_bad_request():
    for project in ("x", {"data": ""}, {"data": "not base64!"}):
        with pytest.raises(LinkError) as caught:
            parse_send({"doc": {"key": "p", "name": "P"}, "mode": "add", "format": "stl",
                        "parts": [{"bodyKey": "b", "name": "B", "data": "AA=="}], "project": project})
        assert caught.value.code == "bad-request"
