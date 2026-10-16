"""Plain-text body tests: sanitizer, HTML conversion, render choice.

Ports the thread-body contract (§2.9): text/plain wins, HTML converts
without scripts or remote fetches, the snippet is the last resort.
"""

from xx_email import bodies


def test_sanitize_removes_scripts_and_handlers():
    dirty = ("<p onclick=\"steal()\">Hi</p>"
             "<script>alert(1)</script>"
             "<style>p{color:red}</style>"
             "<a href=\"javascript:evil()\">x</a>")
    clean = bodies.sanitize_html(dirty)
    assert "<script" not in clean
    assert "onclick" not in clean
    assert "javascript:" not in clean
    assert "Hi" in clean


def test_sanitize_drops_images_unless_allowed():
    dirty = '<p>Hi</p><img src="https://x/y.png">'
    assert "<img" not in bodies.sanitize_html(dirty)
    assert "<img" in bodies.sanitize_html(dirty, allow_remote_images=True)


def test_html_to_text_reads_blocks_and_breaks():
    html = ("<div>Hello</div><p>World<br>again</p>"
            "<script>var x = 1;</script>")
    text = bodies.html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "var x" not in text
    assert bodies.html_to_text("") == ""
    assert bodies.html_to_text("   ") == ""


def test_html_to_text_unescapes_entities():
    assert "fish & chips" in bodies.html_to_text("<p>fish &amp; chips</p>")


def test_render_body_prefers_plain():
    assert bodies.render_body("<p>Html</p>", "Plain text") == "Plain text"
    assert "Html" in bodies.render_body("<p>Html</p>", None)
    assert bodies.render_body(None, None, snippet="fallback") == "fallback"
    assert bodies.render_body(None, "  ", snippet="fallback") == "fallback"
    assert bodies.render_body(None, None) == ""


def test_render_body_never_keeps_scripts():
    rendered = bodies.render_body(
        "<script>evil()</script><p>Real content</p>", None)
    assert "evil" not in rendered
    assert "Real content" in rendered


def test_decode_base64url_with_fallback():
    import base64
    raw = base64.urlsafe_b64encode("héllo".encode()).decode()
    assert bodies.decode_base64url(raw) == "héllo"
    assert bodies.decode_base64url(None) is None
    assert bodies.decode_base64url("!!!not-base64!!!") is None


def test_decode_attachments_tolerates_corruption():
    import json
    payload = json.dumps([{"messageId": "m", "attachmentId": "a",
                           "filename": "bill.pdf", "mimeType": "application/pdf",
                           "size": 12}])
    found = bodies.decode_attachments(payload)
    assert [(a.filename, a.size) for a in found] == [("bill.pdf", 12)]
    assert bodies.decode_attachments(None) == []
    assert bodies.decode_attachments("not json") == []
    assert bodies.decode_attachments(json.dumps({"x": 1})) == []
    assert bodies.decode_attachments(
        json.dumps([{"messageId": "m"}])) == []
