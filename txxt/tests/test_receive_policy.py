"""L5 receive-policy tests: audio-drop + optional auto-reply.

Ports ``service/ReceivePolicy``: audio attachments drop unstored at the
inbox boundary, everything else stores; the auto-reply SMS is off by
default and per-contact overridable.
"""

from txxt import receive_policy as policy
from txxt.receive_policy import (
    AttachmentDecision,
    AutoReplyOverride,
    decide_attachment,
    should_auto_reply,
)


class TestAudioDrop:
    def test_audio_content_types_drop_unstored(self):
        assert decide_attachment("audio/amr") is AttachmentDecision.DROP_UNSTORED
        assert decide_attachment("audio/mpeg") is AttachmentDecision.DROP_UNSTORED
        assert decide_attachment("Audio/AMR") is AttachmentDecision.DROP_UNSTORED

    def test_image_text_smil_store(self):
        assert decide_attachment("image/jpeg") is AttachmentDecision.STORE
        assert decide_attachment("text/plain") is AttachmentDecision.STORE
        assert decide_attachment("application/smil") is AttachmentDecision.STORE
        assert decide_attachment(None) is AttachmentDecision.STORE


class TestAutoReply:
    def test_off_by_default_regardless_of_overrides(self):
        assert should_auto_reply(False, "+1555") is False
        assert should_auto_reply(
            False, "+1555", {"+1555": AutoReplyOverride.ON}) is False

    def test_on_without_overrides_replies(self):
        assert should_auto_reply(True, "+1555") is True

    def test_per_contact_off_suppresses(self):
        assert should_auto_reply(
            True, "+1555", {"+1555": AutoReplyOverride.OFF}) is False

    def test_per_contact_on_keeps_reply(self):
        assert should_auto_reply(
            True, "+1555", {"+1555": AutoReplyOverride.ON}) is True

    def test_other_senders_unaffected_by_one_override(self):
        assert should_auto_reply(
            True, "+1999", {"+1555": AutoReplyOverride.OFF}) is True

    def test_reply_body_names_no_audio(self):
        assert "photo" in policy.AUTO_REPLY_BODY.lower()
