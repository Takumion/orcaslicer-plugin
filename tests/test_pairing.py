import json

import pytest

from link.pairing import CODE_TRIES, CODE_TTL_S, PairingBook
from link.protocol import LinkError


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def book(tmp_path):
    clock = Clock()
    return PairingBook(tmp_path / "pairings.json", clock), clock, tmp_path / "pairings.json"


def test_no_code_until_pair_an_app(book):
    pairing, _, _ = book
    assert pairing.live_code() is None
    with pytest.raises(LinkError) as caught:
        pairing.pair("000000", "https://app.takumion.com", "Chrome")
    assert caught.value.code == "forbidden"


def test_code_is_six_digits_and_single_use(book):
    pairing, _, _ = book
    code = pairing.start_code()
    assert len(code) == 6 and code.isdigit()
    token = pairing.pair(code, "https://app.takumion.com", "Chrome")
    assert pairing.live_code() is None
    with pytest.raises(LinkError):
        pairing.pair(code, "https://app.takumion.com", "Chrome")
    assert pairing.check(token, "https://app.takumion.com")


def test_code_expires_after_120_s(book):
    pairing, clock, _ = book
    code = pairing.start_code()
    clock.now += CODE_TTL_S + 0.1
    assert pairing.live_code() is None
    with pytest.raises(LinkError):
        pairing.pair(code, "https://a", "x")


def test_code_dies_after_five_wrong_tries(book):
    pairing, _, _ = book
    code = pairing.start_code()
    wrong = "999999" if code != "999999" else "000000"
    for _ in range(CODE_TRIES):
        with pytest.raises(LinkError):
            pairing.pair(wrong, "https://a", "x")
    with pytest.raises(LinkError):
        pairing.pair(code, "https://a", "x")


def test_token_is_bound_to_its_origin(book):
    pairing, _, _ = book
    token = pairing.pair(pairing.start_code(), "https://app.takumion.com", "Chrome")
    assert not pairing.check(token, "https://evil.example")
    assert not pairing.check(token, None)
    assert pairing.is_paired_origin("https://app.takumion.com")
    assert not pairing.is_paired_origin("https://evil.example")


def test_native_clients_bind_to_their_name(book):
    pairing, _, _ = book
    token = pairing.pair(pairing.start_code(), None, "Takumion desktop")
    assert pairing.check(token, None)
    assert not pairing.check(token, "https://app.takumion.com")
    assert pairing.pairings()[0]["origin"] == "native:Takumion desktop"


def test_only_the_hash_is_stored_and_reloads(book):
    pairing, clock, path = book
    token = pairing.pair(pairing.start_code(), "https://a", "x")
    stored = path.read_text()
    assert token not in stored
    assert len(json.loads(stored)[0]["token_hash"]) == 64
    again = PairingBook(path, clock)
    assert again.check(token, "https://a")


def test_revoke_by_token_and_by_id(book):
    pairing, _, _ = book
    one = pairing.pair(pairing.start_code(), "https://a", "x")
    two = pairing.pair(pairing.start_code(), "https://b", "y")
    pairing.revoke_token(one)
    assert not pairing.check(one, "https://a")
    remaining = pairing.pairings()[0]["id"]
    pairing.revoke_id(remaining[:1])
    pairing.revoke_id(remaining[:8])
    assert pairing.check(two, "https://b"), "a prefix of an id revokes nothing"
    pairing.revoke_id(remaining)
    assert not pairing.check(two, "https://b")
    assert pairing.pairings() == []


def test_listeners_hear_changes(book):
    pairing, _, _ = book
    heard = []
    pairing.subscribe(lambda: heard.append(1))
    pairing.start_code()
    pairing.cancel_code()
    assert len(heard) == 2
