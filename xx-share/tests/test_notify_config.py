from notify import (
    ACTION_ACCEPT,
    ACTION_DECLINE,
    action_for,
    format_size,
    prompt_actions,
    prompt_text,
)


def test_prompt_text_truncates_names():
    summary, body = prompt_text("phone", ["a.jpg", "b.jpg"], 100)
    assert "phone" in summary
    assert "a.jpg" in body
    summary, body = prompt_text("phone", [f"{n}.jpg" for n in range(6)], 100)
    assert "+3 more" in body


def test_format_size_units():
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"
    assert format_size(3 * 1024**3) == "3.0 GB"


def test_actions_roundtrip():
    actions = prompt_actions()
    assert actions[0] == ACTION_ACCEPT
    assert actions[2] == ACTION_DECLINE
    assert action_for(1, ACTION_ACCEPT) == ACTION_ACCEPT
    assert action_for(1, ACTION_DECLINE) == ACTION_DECLINE
    assert action_for(1, "nope") is None


def test_config_roundtrip_and_theme(isolated_home):
    from config import ShareConfig, shell_theme_key

    config = ShareConfig(isolated_home / ".config" / "xx-share")
    config.data["alias"] = "pocket"
    config.save()
    assert (isolated_home / ".config" / "xx-share" / "config.json").exists()
    again = ShareConfig(isolated_home / ".config" / "xx-share")
    assert again.data["alias"] == "pocket"
    assert again.data["max_transfer_mb"] == 500
    assert again.max_transfer_bytes == 500 * 1024 * 1024
    assert shell_theme_key(isolated_home / "missing.json") == "amoled"
    config.data["theme"] = "paper"
    assert config.effective_theme().key == "paper"
    config.data["theme"] = "bogus"
    assert config.effective_theme().key == "amoled"


def test_cert_fingerprint_matches_der(cert_pair):
    from certs import cert_fingerprint

    import hashlib
    import ssl

    cert_path, _ = cert_pair
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="utf-8"))
    assert cert_fingerprint(cert_path) == hashlib.sha256(der).hexdigest()
