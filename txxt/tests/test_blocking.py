"""Port of the Android blocking tests.

Covers core/BlockingFilterTest.kt, core/BlocklistTest.kt,
core/BlockDecisionTest.kt, core/StarredBypassTest.kt,
core/UnknownSenderRuleTest.kt, block/MessageDispositionTest.kt,
block/InboundFilterTest.kt and block/BlockOverrideTest.kt.
"""

from txxt.blocking import (
    BlockDecision,
    Blocklist,
    BlockOverrideStore,
    BlockReason,
    BlockReasonType,
    BlockingFilter,
    DictKeyValueStore,
    InboundFilter,
    MessageDisposition,
    StarredBypass,
    UnknownSenderRule,
    live_apply,
    live_current,
    live_reset_for_test,
)


def make_filter(keywords=frozenset(), phrases=frozenset()):
    return BlockingFilter(keywords=set(keywords), phrases=set(phrases))


def make_list(keywords=frozenset(), phrases=frozenset()):
    return Blocklist(keywords=set(keywords), phrases=set(phrases))


def make_decision(keywords=frozenset(), phrases=frozenset(),
                  known=frozenset(), starred=frozenset()):
    return BlockDecision(
        BlockingFilter(keywords=set(keywords), phrases=set(phrases)),
        UnknownSenderRule(known_contacts=set(known)),
        StarredBypass(starred_contacts=set(starred)),
    )


def make_inbound(known=frozenset(), blocked=frozenset(), starred=frozenset(),
                 keywords=frozenset(), phrases=frozenset(), quarantine=False,
                 overrides=None, provider=None):
    return InboundFilter(
        known_contacts=set(known),
        blocked_addresses=set(blocked),
        starred_contacts=set(starred),
        content_keywords=set(keywords),
        content_phrases=set(phrases),
        block_override_store=overrides,
        quarantine_unknown_senders=quarantine,
        known_contacts_provider=provider,
    )


# ---- BlockingFilter: keywords ----

def test_keyword_matches_as_a_whole_word():
    f = make_filter(keywords={"loan"})
    assert f.matches("Get a loan today")
    assert f.matches("loan")


def test_keyword_does_not_match_inside_a_longer_word():
    f = make_filter(keywords={"loan"})
    assert not f.matches("Get loans today")
    assert not f.matches("I am a loaner")
    assert not f.matches("unloan")


def test_keyword_matching_is_case_insensitive():
    f = make_filter(keywords={"LOAN"})
    assert f.matches("get a Loan today")
    assert f.matches("LOAN")


def test_keyword_matches_at_punctuation_boundaries():
    f = make_filter(keywords={"loan"})
    assert f.matches("Get a loan!")
    assert f.matches("(loan) today")
    assert f.matches("loan, really")


def test_keyword_matches_surrounded_by_non_alphanumerics():
    f = make_filter(keywords={"win"})
    assert f.matches("You WIN a prize")
    assert f.matches("WIN!!!")


def test_no_match_when_no_keyword_present():
    assert not make_filter(keywords={"loan", "prize"}).matches("Hello, how are you?")


# ---- BlockingFilter: phrases ----

def test_phrase_matches_as_a_substring():
    f = make_filter(phrases={"free money"})
    assert f.matches("Get free money now")
    assert f.matches("free money")


def test_phrase_matching_is_case_insensitive():
    assert make_filter(phrases={"free money"}).matches("GET FREE MONEY NOW")


def test_phrase_does_not_match_when_absent():
    f = make_filter(phrases={"free money"})
    assert not f.matches("Get money now")
    assert not f.matches("free")


def test_matches_when_either_keyword_or_phrase_hits():
    f = make_filter(keywords={"loan"}, phrases={"free money"})
    assert f.matches("Get a loan")
    assert f.matches("free money today")
    assert not f.matches("Just checking in")


def test_matched_term_reports_the_rule_and_prefers_keywords():
    assert make_filter(keywords={"loan"}).matched_term("Get a loan today") == "loan"
    assert make_filter(phrases={"free money"}).matched_term("Get free money now") == "free money"
    both = make_filter(keywords={"loan"}, phrases={"loan"})
    assert both.matched_term("Get a loan") == "loan"
    assert make_filter(keywords={"loan"}).matched_term("Just checking in") is None


def test_empty_filter_matches_nothing():
    f = make_filter()
    assert not f.matches("Get free money loan offer")
    assert f.matched_term("Get free money loan offer") is None


# ---- Blocklist value type ----

def test_added_keyword_and_phrase_are_blocked():
    assert make_list().add_keyword("loan").is_blocked("loan")
    assert make_list().add_phrase("free money").is_blocked("free money")


def test_never_added_rule_is_not_blocked():
    lst = make_list()
    assert not lst.is_blocked("loan")
    assert not lst.is_blocked("free money")


def test_keyword_and_phrase_rules_are_tracked_independently():
    lst = make_list().add_keyword("loan").add_phrase("free money")
    assert lst.is_blocked("loan")
    assert lst.is_blocked("free money")


def test_adding_the_same_rule_twice_is_idempotent():
    lst = make_list().add_keyword("loan").add_keyword("loan")
    assert lst.is_blocked("loan")
    assert lst.keywords() == {"loan"}


def test_adding_then_removing_leaves_the_term_unblocked():
    assert not make_list().add_keyword("loan").remove_keyword("loan").is_blocked("loan")
    assert not make_list().add_phrase("free money").remove_phrase("free money").is_blocked(
        "free money")


def test_removing_a_never_added_rule_is_a_no_op():
    lst = make_list().remove_keyword("loan").remove_phrase("free money")
    assert not lst.is_blocked("loan")
    assert not lst.is_blocked("free money")


def test_removing_a_keyword_leaves_other_rules():
    lst = make_list().add_keyword("loan").add_keyword("prize").remove_keyword("loan")
    assert not lst.is_blocked("loan")
    assert lst.is_blocked("prize")


def test_rule_lists_reflect_adds_and_removes():
    lst = make_list().add_keyword("loan").add_keyword("prize").add_phrase("free money")
    assert lst.keywords() == {"loan", "prize"}
    assert lst.phrases() == {"free money"}
    assert make_list().keywords() == set()
    assert make_list().phrases() == set()
    assert make_list(keywords={"loan"}, phrases={"free money"}).is_blocked("loan")


def test_listed_rules_are_a_snapshot_not_a_live_view():
    lst = make_list().add_keyword("loan")
    snapshot = lst.keywords()
    lst.add_keyword("prize")
    assert snapshot == {"loan"}


# ---- BlockDecision ----

def test_unstarred_sender_blocked_on_keyword_phrase_or_unknown():
    assert make_decision(keywords={"loan"}).should_block("+1 555 2000", "Get a loan today")
    assert make_decision(phrases={"free money"}).should_block("+1 555 2000", "Get free money now")
    assert make_decision(known={"+1 555 1000"}).should_block("+1 555 2000", "Hello")
    assert not make_decision(keywords={"loan"}, known={"+1 555 1000"}).should_block(
        "+1 555 1000", "Just checking in")


def test_starred_contact_is_never_blocked():
    assert not make_decision(
        keywords={"loan"}, starred={"+1 555 1000"}).should_block("+1 555 1000", "Get a loan today")
    assert not make_decision(
        phrases={"free money"}, starred={"+1 555 1000"}).should_block(
        "+1 555 1000", "Get free money now")
    assert not make_decision(
        known={"+1 555 1000"}, starred={"+1 555 2000"}).should_block("+1 555 2000", "Hello")


def test_decision_reason_for_unstarred_sender():
    assert make_decision(keywords={"loan"}).reason("+1 555 2000", "Get a loan today") == \
        "Blocked term: loan"
    assert make_decision(known={"+1 555 1000"}).reason("+1 555 2000", "Hello") == \
        "Sender is not a known contact"
    assert make_decision(keywords={"loan"}, known={"+1 555 1000"}).reason(
        "+1 555 1000", "Just checking in") is None


def test_decision_reason_surfaced_for_starred_contact():
    assert make_decision(keywords={"loan"}, starred={"+1 555 1000"}).reason(
        "+1 555 1000", "Get a loan today") == \
        "Block would match starred contact: Blocked term: loan"
    assert make_decision(phrases={"free money"}, starred={"+1 555 1000"}).reason(
        "+1 555 1000", "Get free money now") == \
        "Block would match starred contact: Blocked term: free money"
    assert make_decision(known={"+1 555 1000"}, starred={"+1 555 2000"}).reason(
        "+1 555 2000", "Hello") == \
        "Block would match starred contact: Sender is not a known contact"


def test_decision_reason_is_deterministic():
    d = make_decision(keywords={"loan"}, starred={"+1 555 1000"})
    assert d.reason("+1 555 1000", "Get a loan today") == d.reason("+1 555 1000", "Get a loan today")


# ---- StarredBypass ----

def test_starred_contact_recognised_and_bypasses():
    b = StarredBypass(starred_contacts={"+1 555 1000"})
    assert b.is_starred("+1 555 1000")
    assert b.bypasses("+1 555 1000")
    assert not b.is_starred("+1 555 2000")
    assert not b.bypasses("+1 555 2000")
    assert b.is_starred("+1 555 1000") == b.bypasses("+1 555 1000")
    assert not StarredBypass().is_starred("+1 555 1000")


def test_starred_matching_is_case_insensitive_trimmed_and_format_tolerant():
    assert StarredBypass(starred_contacts={"ALICE@EXAMPLE.COM"}).is_starred("alice@example.com")
    assert StarredBypass(starred_contacts={"+1 555 1000"}).is_starred("  +1 555 1000  ")
    assert StarredBypass(starred_contacts={"  +1 555 1000  "}).is_starred("+1 555 1000")
    b = StarredBypass(starred_contacts={"+15551234567"})
    assert b.is_starred("555-123-4567")
    assert b.is_starred("(555) 123 4567")
    assert b.bypasses("+1 555 1234567")


def test_starred_reason_present_only_for_starred():
    b = StarredBypass(starred_contacts={"+1 555 1000"})
    assert b.reason("+1 555 1000") == "Starred contacts bypass every suppression"
    assert b.reason("+1 555 2000") is None
    assert b.reason("+1 555 1000") == b.reason("+1 555 1000")


# ---- UnknownSenderRule ----

def test_unknown_detection_and_reason():
    r = UnknownSenderRule(known_contacts={"+1 555 1000"})
    assert r.is_unknown("+1 555 2000")
    assert not r.is_unknown("+1 555 1000")
    assert r.is_known("+1 555 1000")
    assert not r.is_known("+1 555 2000")
    assert r.reason("+1 555 2000") == "Sender is not a known contact"
    assert r.reason("+1 555 1000") is None
    assert r.reason("+1 555 2000") == r.reason("+1 555 2000")
    assert UnknownSenderRule().is_unknown("+1 555 1000")


def test_known_matching_is_case_insensitive_trimmed_and_format_tolerant():
    assert UnknownSenderRule(known_contacts={"ALICE@EXAMPLE.COM"}).is_known("alice@example.com")
    assert UnknownSenderRule(known_contacts={"+1 555 1000"}).is_known("  +1 555 1000  ")
    assert UnknownSenderRule(known_contacts={"  +1 555 1000  "}).is_known("+1 555 1000")
    r = UnknownSenderRule(known_contacts={"+15551234567"})
    assert r.is_known("555-123-4567")
    assert r.is_known("(555) 123 4567")
    assert r.is_known("+1 (555) 123-4567")


# ---- MessageDisposition / BlockReason ----

def test_disposition_and_reason_shapes():
    assert {d for d in MessageDisposition} == {
        MessageDisposition.DELIVER, MessageDisposition.QUARANTINE, MessageDisposition.BLOCK}
    assert {t for t in BlockReasonType} == {
        BlockReasonType.UNKNOWN_SENDER, BlockReasonType.BLOCKED_LIST,
        BlockReasonType.STARRED_CONTACT_RULE}
    assert MessageDisposition("DELIVER") is MessageDisposition.DELIVER
    unknown = BlockReason.unknown_sender()
    assert unknown.type is BlockReasonType.UNKNOWN_SENDER
    assert unknown.message == "Sender is not in your contacts"
    assert not unknown.can_override
    blocked = BlockReason.blocked_list()
    assert blocked.type is BlockReasonType.BLOCKED_LIST
    assert blocked.message == "Sender is on your block list"
    assert not blocked.can_override
    starred = BlockReason.starred_contact_rule("content-filter")
    assert starred.type is BlockReasonType.STARRED_CONTACT_RULE
    assert starred.can_override
    assert "content-filter" in starred.message


# ---- InboundFilter ----

def test_known_sender_with_clean_message_is_delivered():
    disposition, reason = make_inbound(known={"+1 555 1000"}).evaluate("+1 555 1000", "Hey there")
    assert disposition is MessageDisposition.DELIVER
    assert reason is None


def test_unknown_sender_quarantined_only_when_opted_in():
    quarantining = make_inbound(known={"+1 555 1000"}, quarantine=True)
    disposition, reason = quarantining.evaluate("+1 555 9999", "Hello")
    assert disposition is MessageDisposition.QUARANTINE
    assert reason is not None
    assert reason.type is BlockReasonType.UNKNOWN_SENDER
    assert reason.message == "Sender is not in your contacts"
    assert not reason.can_override


def test_blocked_address_is_blocked_with_list_reason():
    disposition, reason = make_inbound(blocked={"+1 555 8888"}).evaluate("+1 555 8888", "Hello")
    assert disposition is MessageDisposition.BLOCK
    assert reason is not None
    assert reason.type is BlockReasonType.BLOCKED_LIST
    assert reason.message == "Sender is on your block list"
    assert not reason.can_override


def test_keyword_and_phrase_matches_block():
    f = make_inbound(known={"+1 555 1000"}, keywords={"loan"})
    disposition, reason = f.evaluate("+1 555 1000", "Get a loan today")
    assert disposition is MessageDisposition.BLOCK
    assert reason is not None and "loan" in reason.message
    f = make_inbound(known={"+1 555 1000"}, phrases={"free money"})
    disposition, reason = f.evaluate("+1 555 1000", "Get free money now")
    assert disposition is MessageDisposition.BLOCK
    assert reason is not None and "free money" in reason.message


def test_starred_contact_bypasses_block_content_and_quarantine():
    f = make_inbound(blocked={"+1 555 1000"}, starred={"+1 555 1000"})
    disposition, reason = f.evaluate("+1 555 1000", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is not None and reason.can_override
    assert reason.type is BlockReasonType.STARRED_CONTACT_RULE

    f = make_inbound(known={"+1 555 1000"}, keywords={"loan"}, starred={"+1 555 1000"})
    disposition, reason = f.evaluate("+1 555 1000", "Get a loan today")
    assert disposition is MessageDisposition.DELIVER
    assert reason is not None and reason.can_override

    f = make_inbound(known={"+1 555 1000"}, starred={"+1 555 2000"}, quarantine=True)
    disposition, reason = f.evaluate("+1 555 2000", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is not None and reason.can_override
    assert reason.type is BlockReasonType.STARRED_CONTACT_RULE


def test_matching_is_case_insensitive_trimmed_and_format_tolerant():
    f = make_inbound(blocked={"  +1 555 8888  "})
    assert f.evaluate("+1 555 8888", "Hello")[0] is MessageDisposition.BLOCK
    f = make_inbound(known={"  +1 555 1000  "})
    assert f.evaluate("+1 555 1000", "Hello")[0] is MessageDisposition.DELIVER
    f = make_inbound(known={"+1 555 1000"}, starred={"  +1 555 2000  "})
    assert f.evaluate("+1 555 2000", "Hello")[0] is MessageDisposition.DELIVER
    f = make_inbound(blocked={"+1 555 8888"})
    for variant in ("+15558888", "555-8888", "(555) 8888", "+1 (555) 8888"):
        assert f.evaluate(variant, "Hello")[0] is MessageDisposition.BLOCK, variant


def test_blocked_address_takes_priority_over_unknown_sender():
    f = make_inbound(known={"+1 555 1000"}, blocked={"+1 555 9999"})
    disposition, reason = f.evaluate("+1 555 9999", "Hello")
    assert disposition is MessageDisposition.BLOCK
    assert reason.type is BlockReasonType.BLOCKED_LIST


def test_empty_filter_quarantines_only_when_opted_in():
    disposition, reason = make_inbound(quarantine=True).evaluate("+1 555 0000", "Hello")
    assert disposition is MessageDisposition.QUARANTINE
    assert reason.type is BlockReasonType.UNKNOWN_SENDER


def test_factory_default_is_fail_open_delivery():
    f = InboundFilter()
    assert not f.quarantine_unknown_senders
    disposition, reason = f.evaluate("+1 555 0000", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is None
    disposition, reason = make_inbound(known={"+1 555 0000"}).evaluate("+1 555 0000", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is None


def test_known_contacts_provider_is_live_not_a_snapshot():
    contacts = {"+1 555 1000"}
    f = make_inbound(quarantine=True, provider=lambda: set(contacts))
    assert f.evaluate("+1 555 2000", "Hello")[0] is MessageDisposition.QUARANTINE
    contacts.add("+1 555 2000")
    disposition, reason = f.evaluate("+1 555 2000", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is None


# ---- BlockOverrideStore + filter integration ----

def make_override_store():
    return BlockOverrideStore(DictKeyValueStore())


def test_no_override_by_default_and_add_survives_across_instances():
    kv = DictKeyValueStore()
    assert not BlockOverrideStore(kv).has_override("+1 555 1000")
    BlockOverrideStore(kv).add_override("+1 555 1000")
    assert BlockOverrideStore(kv).has_override("+1 555 1000")


def test_remove_override_clears_it():
    kv = DictKeyValueStore()
    store = BlockOverrideStore(kv)
    store.add_override("+1 555 1000")
    store.remove_override("+1 555 1000")
    assert not store.has_override("+1 555 1000")


def test_override_matching_is_case_insensitive_trimmed_and_format_tolerant():
    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("  +1 555 1000  ")
    assert BlockOverrideStore(kv).has_override("+1 555 1000")
    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("+15551234567")
    store = BlockOverrideStore(kv)
    assert store.has_override("555-123-4567")
    assert store.has_override("(555) 123 4567")
    assert store.has_override("+1 555 1234567")
    assert not store.has_override("+1 555 9999")


def test_remove_override_clears_every_format_variant():
    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("+15551234567")
    BlockOverrideStore(kv).remove_override("555-123-4567")
    assert not BlockOverrideStore(kv).has_override("+15551234567")


def test_overridden_senders_are_delivered_without_a_reason():
    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("+1 555 8888")
    f = make_inbound(blocked={"+1 555 8888"}, overrides=BlockOverrideStore(kv))
    disposition, reason = f.evaluate("+1 555 8888", "Hello")
    assert disposition is MessageDisposition.DELIVER
    assert reason is None

    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("+1 555 9999")
    f = make_inbound(known={"+1 555 1000"}, overrides=BlockOverrideStore(kv))
    assert f.evaluate("+1 555 9999", "Hello")[0] is MessageDisposition.DELIVER

    kv = DictKeyValueStore()
    BlockOverrideStore(kv).add_override("+1 555 1000")
    f = make_inbound(known={"+1 555 1000"}, keywords={"loan"}, overrides=BlockOverrideStore(kv))
    assert f.evaluate("+1 555 1000", "Get a loan today")[0] is MessageDisposition.DELIVER


def test_without_an_override_the_sender_is_still_blocked():
    f = make_inbound(blocked={"+1 555 8888"}, overrides=make_override_store())
    assert f.evaluate("+1 555 8888", "Hello")[0] is MessageDisposition.BLOCK


# ---- process-wide live filter ----

def test_live_apply_reaches_the_running_filter_and_reset_restores_default():
    try:
        live_apply(blocked_addresses={"+1 555 8888"})
        assert live_current().evaluate("+1 555 8888", "Hello")[0] is MessageDisposition.BLOCK
        assert live_current().evaluate("+1 555 9999", "Hello")[0] is MessageDisposition.DELIVER
        live_apply(
            blocked_addresses={"+1 555 1000"}, starred_contacts={"+1 555 1000"})
        assert live_current().evaluate("+1 555 1000", "Hello")[0] is MessageDisposition.DELIVER
        live_apply(quarantine_unknown_senders=True)
        assert live_current().evaluate("+1 555 0000", "Hello")[0] is MessageDisposition.QUARANTINE
    finally:
        live_reset_for_test()
    assert live_current().evaluate("+1 555 8888", "Hello")[0] is MessageDisposition.DELIVER
