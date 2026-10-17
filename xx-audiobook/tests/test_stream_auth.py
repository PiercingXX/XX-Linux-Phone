"""StreamAuth: the bearer goes to the estate host only."""

from xx_audiobook.stream_auth import StreamAuth

BASE = "https://server.example.ts.net/audiobook/"
TOKEN = "tok-1"


def test_estate_host_gets_bearer():
    auth = StreamAuth()
    auth.set(BASE, TOKEN)
    assert auth.headers_for(
        "https://server.example.ts.net/audiobook/api/items/x/files/f"
    ) == {"Authorization": "Bearer tok-1"}


def test_file_and_foreign_hosts_get_nothing():
    auth = StreamAuth()
    auth.set(BASE, TOKEN)
    assert auth.headers_for("file:///data/books/it/f-1.m4b") == {}
    assert auth.headers_for("https://cdn.example.com/file.m4b") == {}
    assert auth.headers_for("https://server.example.ts.net.evil.com/x") == {}


def test_origin_key_is_scheme_host_port():
    auth = StreamAuth()
    auth.set("http://127.0.0.1:8750/audiobook/", TOKEN)
    assert auth.headers_for(
        "http://127.0.0.1:8750/audiobook/api/x") != {}
    assert auth.headers_for(
        "http://127.0.0.1:9999/audiobook/api/x") == {}
    assert auth.headers_for("garbage:::") == {}


def test_clear_and_blank_token_send_nothing():
    auth = StreamAuth()
    assert auth.headers_for("https://h/audiobook/x") == {}
    auth.set(BASE, "   ")
    assert auth.headers_for(BASE + "api/x") == {}
    auth.set(BASE, TOKEN)
    auth.clear()
    assert auth.headers_for(BASE + "api/x") == {}
