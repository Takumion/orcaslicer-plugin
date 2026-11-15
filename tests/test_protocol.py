import base64

import pytest

from link.protocol import LinkError, check_key, parse_pair, parse_send


def body(**over):
    base = {"doc": {"key": "prj_1", "name": "Bracket"}, "mode": "update", "format": "3mf",
            "parts": [{"bodyKey": "body_1", "name": "Base", "data": base64.b64encode(b"solid").decode()}]}
    base.update(over)
    return base


def test_parse_send_decodes_parts():
    req = parse_send(body())
    assert (req.doc_key, req.doc_name, req.mode, req.format) == ("prj_1", "Bracket", "update", "3mf")
    assert req.parts[0].data == b"solid"


@pytest.mark.parametrize("over", [
    {"mode": "replace"},
    {"format": "obj"},
    {"parts": []},
    {"doc": {"key": "a/b"}},
    {"doc": {"key": "x" * 129}},
    {"parts": [{"bodyKey": "b", "name": "n", "data": "!!notbase64"}]},
    {"parts": [{"bodyKey": "b", "name": "n", "data": ""}]},
    {"parts": [{"bodyKey": "b c", "name": "n", "data": "AA=="}]},
    {"parts": [{"bodyKey": "b", "name": "n", "data": "AA=="}, {"bodyKey": "b", "name": "m", "data": "AA=="}]},
])
def test_parse_send_refuses_bad_requests(over):
    with pytest.raises(LinkError) as caught:
        parse_send(body(**over))
    assert caught.value.code == "bad-request" and caught.value.status == 400


def test_parse_send_refuses_non_objects():
    with pytest.raises(LinkError):
        parse_send([1, 2])


def test_errors_map_to_the_table():
    assert {c: LinkError(c, "m").status for c in ("unauthorized", "forbidden", "too-large", "bad-host", "orca-busy",
                                                   "internal")} == {
        "unauthorized": 401, "forbidden": 403, "too-large": 413, "bad-host": 421, "orca-busy": 503, "internal": 500}
    assert LinkError("forbidden", "x").to_json() == {"error": {"code": "forbidden", "message": "x"}}
    with pytest.raises(ValueError):
        LinkError("nope", "x")


def test_keys():
    assert check_key("A-z_09", "k") == "A-z_09"
    with pytest.raises(LinkError):
        check_key(None, "k")


def test_parse_pair_trims_and_defaults():
    assert parse_pair({"code": " 123456 "}) == ("123456", "Unnamed app")
    assert parse_pair({"code": "1", "clientName": "Takumion (Chrome)"}) == ("1", "Takumion (Chrome)")
