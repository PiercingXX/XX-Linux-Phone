"""M7 account logic: header/cURL parsing, secret store, cookie jar, OAuth.

All fixtures use invented cookies, accounts and tokens. Nothing here
touches the network, a real account or the real keyring: the ytmusicapi
client arrives through the ``client_factory`` seam, the token endpoint
through the ``post`` seam, and secrets live in :class:`FakeSecretStore`.
"""

import ast
import json
import os
from pathlib import Path

import pytest

from xx_music.engine import EngineError, ErrorKind, LibrarySection, Rating
from xx_radio.account import AccountManager, AccountState
from xx_radio.youtube import auth

FIXTURES = Path(__file__).parent / "fixtures"

#: Marker strings that must never land in any file under the test HOME.
SECRET_MARKERS = (
    "fake-SID-block-0001",
    "fake-3PAPISID-block-0007",
    "fake-login-info-block-0009",
    "fake-SID-brand-1001",
    "fake-SID-curl-2001",
    "fakehash0000curl",
    "fake-access-token-9001",
    "fake-refresh-token-9002",
    "fake-client-secret-7001",
)


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def assert_home_has_no_secrets(home: Path) -> None:
    found = []
    for path in sorted(home.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        for marker in SECRET_MARKERS:
            if marker in text:
                found.append(f"{path}: {marker}")
    assert not found, "secrets landed on disk:\n" + "\n".join(found)


class FakeYTMusic:
    """Stands in for ytmusicapi.YTMusic behind the client_factory seam."""

    def __init__(self, headers, user=None, error: Exception | None = None,
                 songs=None, history=None) -> None:
        self.headers = headers
        self.user = user
        self.error = error
        self.songs = songs if songs is not None else []
        self.history = history if history is not None else []
        self.calls: list = []
        self.rated: list = []

    def _check(self, name, *args):
        self.calls.append((name, *args))
        if self.error is not None:
            raise self.error
        return self.calls[-1]

    def get_library_playlists(self, limit=None):
        self._check("get_library_playlists", limit)
        return [{"playlistId": "fake-playlist-1", "title": "Fakes"}][:limit or 1]

    def get_library_songs(self, limit=25):
        self._check("get_library_songs", limit)
        return list(self.songs)[:limit]

    def get_library_albums(self, limit=25):
        self._check("get_library_albums", limit)
        return [{"browseId": "fake-album-0001", "title": "Tidewater"}]

    def get_library_artists(self, limit=25):
        self._check("get_library_artists", limit)
        return [{"browseId": "fake-artist-0001", "artist": "Lumen Drive"}]

    def get_liked_songs(self, limit=25):
        self._check("get_liked_songs", limit)
        return [s for s in self.songs if s.get("likeStatus") == "LIKE"][:limit]

    def get_history(self):
        self._check("get_history")
        return list(self.history)

    def rate_song(self, video_id, rating):
        self._check("rate_song", video_id, rating)
        self.rated.append((video_id, rating))


def _factory_for(fake: FakeYTMusic):
    seen = {}

    def factory(headers, user):
        seen["headers"] = headers
        seen["user"] = user
        fake.headers = headers
        fake.user = user
        return fake

    factory.seen = seen
    return factory


def _manager(tmp_path, fake: FakeYTMusic, **kwargs):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return AccountManager(auth.FakeSecretStore(), run_dir,
                          _factory_for(fake), **kwargs)


# ---- header-block parsing ----------------------------------------------------


def test_header_block_parses_and_defaults_authuser():
    parsed = auth.parse_header_block(_read_fixture("auth_headers_block.txt"))
    assert "cookie" in parsed
    assert ":authority" not in parsed
    assert not any(name.startswith(":") for name in parsed)
    assert "x-goog-authuser" not in parsed
    credentials = auth.build_browser_credentials(parsed)
    assert credentials["x-goog-authuser"] == "0"
    assert "fake-SID-block-0001" in credentials["cookie"]


def test_header_block_drops_hop_by_hop_headers():
    credentials = auth.build_browser_credentials(
        auth.parse_header_block(_read_fixture("auth_headers_block.txt")))
    assert "host" not in credentials
    assert "content-length" not in credentials
    assert "accept-encoding" not in credentials
    assert not any(name.startswith("sec-") for name in credentials)
    assert credentials["authorization"].startswith("SAPISIDHASH")


def test_brand_account_keeps_its_authuser_value():
    parsed = auth.parse_header_block(_read_fixture("auth_headers_brand.txt"))
    credentials = auth.build_browser_credentials(parsed)
    assert credentials["x-goog-authuser"] == "1"
    assert "fake-SID-brand-1001" in credentials["cookie"]


def test_header_block_rejects_garbage_and_missing_cookie():
    with pytest.raises(auth.CredentialsError):
        auth.parse_header_block("accept: */*\nnot a header at all\n")
    with pytest.raises(auth.CredentialsError):
        auth.build_browser_credentials({"accept": "*/*"})
    with pytest.raises(auth.CredentialsError):
        auth.build_browser_credentials({"cookie": "   "})
    with pytest.raises(auth.CredentialsError):
        auth.parse_pasted_headers("   \n  ")


def test_values_may_contain_colons():
    parsed = auth.parse_header_block("referer: https://example.invalid/a:b\n"
                                     "cookie: SID=fake-x; YSC=z\n")
    assert parsed["referer"] == "https://example.invalid/a:b"


# ---- "Copy as cURL" parsing ---------------------------------------------------


def test_curl_fixture_parses_to_the_same_credentials_shape():
    parsed = auth.parse_curl(_read_fixture("auth_curl.txt"))
    assert parsed["x-goog-authuser"] == "0"
    credentials = auth.build_browser_credentials(parsed)
    assert "fake-SID-curl-2001" in credentials["cookie"]
    assert "host" not in credentials
    assert not any(name.startswith("sec-") for name in credentials)


def test_curl_ignores_url_data_and_compression_flags():
    parsed = auth.parse_curl(
        "curl 'https://music.youtube.com/x' "
        "-H 'cookie: SID=fake-SID-curl-2001; YSC=q' "
        "--data-raw '{\"browseId\":\"x\"}' --compressed")
    assert parsed == {"cookie": "SID=fake-SID-curl-2001; YSC=q"}


def test_curl_cookie_flag_is_accepted():
    parsed = auth.parse_curl(
        "curl https://music.youtube.com/ -b 'SID=fake-SID-curl-2001; YSC=q'")
    assert parsed["cookie"] == "SID=fake-SID-curl-2001; YSC=q"


def test_parse_pasted_headers_detects_both_forms():
    block = _read_fixture("auth_headers_block.txt")
    assert auth.parse_pasted_headers(block)["cookie"].startswith("SID=")
    curl = _read_fixture("auth_curl.txt")
    assert "fake-SID-curl-2001" in auth.parse_pasted_headers(curl)["cookie"]
    with pytest.raises(auth.CredentialsError):
        auth.parse_curl(block)


# ---- validation seam ----------------------------------------------------------


def test_validation_makes_one_cheap_call_and_passes_brand_user():
    fake = FakeYTMusic(headers={}, user=None)
    factory = _factory_for(fake)
    credentials = auth.build_browser_credentials(
        auth.parse_header_block(_read_fixture("auth_headers_brand.txt")))
    auth.validate_browser_credentials(credentials, factory, user="fake-brand-42")
    assert fake.calls == [("get_library_playlists", 1)]
    assert factory.seen["user"] == "fake-brand-42"
    assert factory.seen["headers"]["x-goog-authuser"] == "1"


def test_validation_maps_rejection_to_expired():
    fake = FakeYTMusic(headers={}, error=Exception("401 Unauthorized: "
                                                   "invalid cookie"))
    with pytest.raises(auth.CredentialsExpired):
        auth.validate_browser_credentials(
            {"cookie": "SID=x"}, _factory_for(fake))


def test_validation_maps_other_failures_plainly():
    fake = FakeYTMusic(headers={}, error=RuntimeError("connection reset"))
    with pytest.raises(auth.ValidationError) as caught:
        auth.validate_browser_credentials(
            {"cookie": "SID=x"}, _factory_for(fake))
    assert not isinstance(caught.value, auth.CredentialsExpired)


def test_auth_module_needs_no_gi_or_ytmusicapi_to_import():
    tree = ast.parse((Path(auth.__file__)).read_text(encoding="utf-8"))
    top_imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_imports.add(node.module.split(".")[0])
    assert "gi" not in top_imports
    assert "ytmusicapi" not in top_imports


# ---- sign-in, secret store, HOME scan ------------------------------------------


def test_sign_in_stores_browser_secret_and_state(tmp_path):
    home = Path(os.environ["HOME"])
    fake = FakeYTMusic(headers={})
    manager = _manager(tmp_path, fake)
    assert manager.state() == AccountState(False)
    state = manager.sign_in_with_headers(
        _read_fixture("auth_headers_block.txt"), account_name="Main")
    assert state == AccountState(True, "browser", "Main", None)
    assert manager.state() == state
    stored = manager._secrets.lookup(auth.KIND_BROWSER, "Main")
    assert "fake-SID-block-0001" in stored
    assert_home_has_no_secrets(home)
    assert_home_has_no_secrets(tmp_path)


def test_sign_in_with_brand_user_records_it(tmp_path):
    fake = FakeYTMusic(headers={})
    manager = _manager(tmp_path, fake)
    state = manager.sign_in_with_headers(
        _read_fixture("auth_headers_brand.txt"),
        account_name="Band", brand_user="fake-brand-42")
    assert state.brand_user == "fake-brand-42"
    assert fake.user == "fake-brand-42"
    assert manager.state() == state


def test_sign_in_rejection_stores_nothing(tmp_path):
    fake = FakeYTMusic(headers={}, error=Exception("login failed: 403"))
    manager = _manager(tmp_path, fake)
    with pytest.raises(auth.CredentialsExpired):
        manager.sign_in_with_headers(_read_fixture("auth_headers_block.txt"))
    assert manager._secrets.accounts(auth.KIND_BROWSER) == []
    assert manager.state() == AccountState(False)


def test_sign_out_forgets_secrets_but_keeps_oauth_client_id(tmp_path):
    fake = FakeYTMusic(headers={})
    manager = _manager(tmp_path, fake)
    manager.sign_in_with_headers(_read_fixture("auth_headers_block.txt"))
    manager.remember_oauth_client("fake-client-id-7000",
                                  "fake-client-secret-7001")
    manager.ensure_cookie_jar()
    jar = auth.cookie_jar_path(tmp_path / "run")
    assert jar.is_file()
    assert manager.sign_out() == AccountState(False)
    assert manager.state() == AccountState(False)
    assert manager._secrets.accounts(auth.KIND_BROWSER) == []
    assert manager._secrets.accounts(auth.KIND_OAUTH) == []
    assert not jar.exists()
    kept = manager._secrets.lookup(auth.KIND_OAUTH_CLIENT, "YouTube Music")
    assert "fake-client-id-7000" in kept
    assert_home_has_no_secrets(Path(os.environ["HOME"]))
    assert_home_has_no_secrets(tmp_path)


# ---- cookie jar lifecycle ------------------------------------------------------


def _block_cookie() -> str:
    return auth.parse_header_block(
        _read_fixture("auth_headers_block.txt"))["cookie"]


def test_cookie_jar_drops_login_info_and_is_owner_only(tmp_path):
    jar = auth.write_cookie_jar(_block_cookie(), tmp_path / "run")
    assert oct(jar.stat().st_mode & 0o777) == "0o600"
    assert oct(jar.parent.stat().st_mode & 0o777) == "0o700"
    text = jar.read_text(encoding="utf-8")
    assert "LOGIN_INFO" not in text
    assert "fake-login-info-block-0009" not in text
    assert "fake-SID-block-0001" in text
    assert text.startswith("# Netscape HTTP Cookie File")


def test_cookie_jar_missing_is_fine_and_empty_cookie_refused(tmp_path):
    auth.clear_cookie_jar(tmp_path / "run")  # never written: no error
    with pytest.raises(auth.CredentialsError):
        auth.write_cookie_jar("   ", tmp_path / "run")


def test_manager_jar_only_while_signed_in_with_browser(tmp_path):
    fake = FakeYTMusic(headers={})
    manager = _manager(tmp_path, fake)
    assert manager.ensure_cookie_jar() is None
    manager.sign_in_with_headers(_read_fixture("auth_headers_block.txt"))
    jar = manager.ensure_cookie_jar()
    assert jar is not None and jar.is_file()
    assert oct(jar.stat().st_mode & 0o777) == "0o600"
    manager.sign_out()
    assert not jar.exists()


# ---- OAuth device flow against a fake token endpoint ----------------------------


class _TokenReply:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return dict(self._payload)


class FakeTokenEndpoint:
    """Stands in for Google's device-code and token endpoints."""

    def __init__(self, token_answers) -> None:
        self.token_answers = list(token_answers)
        self.calls: list = []

    def post(self, url, data, headers):
        self.calls.append((url, dict(data)))
        assert "User-Agent" in headers
        if url == auth.OAUTH_CODE_URL:
            assert data["client_id"] == "fake-client-id-7000"
            assert data["scope"] == auth.OAUTH_SCOPE
            return _TokenReply(200, {
                "device_code": "fake-device-code-8001",
                "user_code": "ABCD-EFGH",
                "verification_url": "https://example.invalid/activate",
                "expires_in": 1800, "interval": 1})
        assert data["code"] == "fake-device-code-8001"
        assert data["client_secret"] == "fake-client-secret-7001"
        answer = self.token_answers.pop(0)
        return _TokenReply(answer[0], answer[1])


def _oauth_manager(tmp_path, fake, endpoint):
    return AccountManager(auth.FakeSecretStore(), tmp_path / "run",
                          _factory_for(fake), token_post=endpoint.post)


def test_oauth_flow_pending_then_approved(tmp_path):
    home = Path(os.environ["HOME"])
    endpoint = FakeTokenEndpoint([
        (200, {"error": "authorization_pending"}),
        (200, {"access_token": "fake-access-token-9001",
               "refresh_token": "fake-refresh-token-9002",
               "expires_in": 3600, "token_type": "Bearer",
               "scope": auth.OAUTH_SCOPE})])
    manager = _oauth_manager(tmp_path, FakeYTMusic(headers={}), endpoint)
    sleeps = []
    code = manager.start_oauth("fake-client-id-7000",
                               "fake-client-secret-7001")
    assert code.user_code == "ABCD-EFGH"
    state = manager.finish_oauth("fake-client-id-7000",
                                 "fake-client-secret-7001", code,
                                 account_name="Tube", sleep=sleeps.append,
                                 timeout_s=60.0)
    assert state == AccountState(True, "oauth", "Tube", None, True)
    assert manager.state() == state
    assert sleeps == [1]
    assert manager.ensure_cookie_jar() is None  # OAuth streams anonymously
    assert_home_has_no_secrets(home)
    assert_home_has_no_secrets(tmp_path)


def test_oauth_denied_and_bad_client(tmp_path):
    denied = FakeTokenEndpoint([(200, {"error": "access_denied"})])
    manager = _oauth_manager(tmp_path, FakeYTMusic(headers={}), denied)
    code = manager.start_oauth("fake-client-id-7000",
                               "fake-client-secret-7001")
    with pytest.raises(auth.OAuthDenied):
        manager.finish_oauth("fake-client-id-7000",
                             "fake-client-secret-7001", code,
                             sleep=lambda _s: None)
    assert manager.state() == AccountState(False)

    bad = FakeTokenEndpoint([])
    bad.post = lambda url, data, headers: _TokenReply(
        401, {"error": "invalid_client"})
    manager = _oauth_manager(tmp_path, FakeYTMusic(headers={}), bad)
    with pytest.raises(auth.BadOAuthClient):
        manager.start_oauth("nope", "fake-client-secret-7001")


def test_oauth_needs_a_client_id_and_secret(tmp_path):
    manager = _oauth_manager(tmp_path, FakeYTMusic(headers={}),
                             FakeTokenEndpoint([]))
    with pytest.raises(auth.CredentialsError):
        manager.start_oauth("  ", "fake-client-secret-7001")
    with pytest.raises(auth.CredentialsError):
        manager.remember_oauth_client("fake-client-id-7000", " ")


# ---- library, history and ratings sync ------------------------------------------


def _library_manager(tmp_path):
    songs = json.loads(_read_fixture("auth_library_songs.json"))
    history = json.loads(_read_fixture("auth_history.json"))
    fake = FakeYTMusic(headers={}, songs=songs, history=history)
    manager = _manager(tmp_path, fake)
    manager.sign_in_with_headers(_read_fixture("auth_headers_block.txt"))
    return manager, fake


def test_library_songs_and_liked_come_from_the_engine_call(tmp_path):
    manager, fake = _library_manager(tmp_path)
    songs = manager.fetch_library(LibrarySection.SONGS)
    assert [s["videoId"] for s in songs] == ["fake-video-0001",
                                             "fake-video-0002"]
    liked = manager.fetch_library(LibrarySection.LIKED)
    assert [s["videoId"] for s in liked] == ["fake-video-0001"]
    assert ("get_library_songs", 25) in fake.calls


def test_history_comes_from_the_engine_call(tmp_path):
    manager, fake = _library_manager(tmp_path)
    history = manager.fetch_history()
    assert [h["videoId"] for h in history] == ["fake-video-0001",
                                               "fake-video-0004"]
    assert ("get_history",) in fake.calls
    playlists = manager.fetch_library(LibrarySection.PLAYLISTS)
    assert playlists[0]["playlistId"] == "fake-playlist-1"


def test_ratings_sync_maps_engine_ratings(tmp_path):
    manager, fake = _library_manager(tmp_path)
    manager.set_rating("fake-video-0002", Rating.LIKE)
    manager.set_rating("fake-video-0001", Rating.DISLIKE)
    manager.set_rating("fake-video-0001", Rating.NONE)
    assert fake.rated == [("fake-video-0002", "LIKE"),
                          ("fake-video-0001", "DISLIKE"),
                          ("fake-video-0001", "INDIFFERENT")]
    with pytest.raises(EngineError) as caught:
        manager.set_rating("", Rating.LIKE)
    assert caught.value.kind == ErrorKind.NOT_FOUND


def test_signed_out_sync_needs_sign_in(tmp_path):
    manager = _manager(tmp_path, FakeYTMusic(headers={}))
    with pytest.raises(EngineError) as caught:
        manager.fetch_library(LibrarySection.SONGS)
    assert caught.value.kind == ErrorKind.AUTH_REQUIRED
    with pytest.raises(EngineError) as caught:
        manager.set_rating("fake-video-0001", Rating.LIKE)
    assert caught.value.kind == ErrorKind.AUTH_REQUIRED


def test_expired_session_maps_to_auth_expired(tmp_path):
    fake = FakeYTMusic(headers={})
    manager = _manager(tmp_path, fake)
    manager.sign_in_with_headers(_read_fixture("auth_headers_block.txt"))
    fake.error = Exception("401 Unauthorized: SAPISID invalid")
    with pytest.raises(EngineError) as caught:
        manager.fetch_library(LibrarySection.SONGS)
    assert caught.value.kind == ErrorKind.AUTH_EXPIRED
    assert caught.value.user_text == "Your sign-in expired. Sign in again."
    fake.error = RuntimeError("connection reset")
    with pytest.raises(EngineError) as caught:
        manager.set_rating("fake-video-0001", Rating.LIKE)
    assert caught.value.kind == ErrorKind.OTHER


# ---- redaction -------------------------------------------------------------------


def test_redaction_masks_secrets_but_keeps_shape():
    credentials = auth.build_browser_credentials(
        auth.parse_header_block(_read_fixture("auth_headers_block.txt")))
    masked = auth.redact_headers(credentials)
    assert masked["cookie"] == "***"
    assert masked["authorization"] == "***"
    assert masked["x-goog-authuser"] == "0"
    assert "fake-SID-block-0001" not in json.dumps(masked)
    line = ("signed in with cookie: SID=fake-SID-block-0001; YSC=x "
            "auth SAPISIDHASH 1700000000_abcdef1234567890 done")
    scrubbed = auth.redact_text(line)
    assert "fake-SID-block-0001" not in scrubbed
    assert "abcdef1234567890" not in scrubbed
    assert "SAPISIDHASH" in scrubbed
