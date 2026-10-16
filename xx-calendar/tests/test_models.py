"""Ports of the Android pure-JVM suites (ref/xx-calendar .../test/...).

Covers RRuleModel round-trip/refusals, the ScopeResolver table, TimeMath,
AgendaGrouping, the four-tier SigilAssigner, ReminderPlanner, AlarmKeys,
and HeartbeatClock. Headless: no display, no GTK import.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from xx_calendar.models import (
    ALL_DAY_LEAD_MILLIS,
    AgendaGrouping,
    AlarmKey,
    AlarmKeys,
    AllDayNotification,
    ByDay,
    CalendarKey,
    DeleteInstanceUri,
    DeleteParentRow,
    EndCount,
    EndNever,
    EndUntil,
    EventFieldEdits,
    Frequency,
    HeartbeatClock,
    InsertExceptionRow,
    InstanceRef,
    InstanceSpan,
    PlannerInstance,
    RecurrenceScope,
    RecurringEventContext,
    Refusal,
    ReminderPlanner,
    RRuleModel,
    ScopeResolver,
    RuleParsed,
    RuleRefused,
    SetUntil,
    SigilAssigner,
    SigilTier,
    SplitParent,
    TimeMath,
    UpdateParentRow,
    Weekday,
)


def millis(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
               .timestamp() * 1000)


def utc_midnight(iso_date):
    year, month, day = (int(part) for part in iso_date.split("-"))
    return int(datetime(year, month, day, tzinfo=timezone.utc)
               .timestamp() * 1000)


# ------------------------------------------------------- RRuleModel ports


def test_daily_preset_serializes_to_freq_only():
    assert RRuleModel.daily().serialize() == "FREQ=DAILY"
    assert RRuleModel.daily() == RRuleModel(frequency=Frequency.DAILY)


def test_weekly_preset_serializes_by_days_in_order():
    rule = RRuleModel.weekly([Weekday.TU, Weekday.TH])
    assert rule == RRuleModel(
        Frequency.WEEKLY, by_day=(ByDay(None, Weekday.TU),
                                  ByDay(None, Weekday.TH)))
    assert rule.serialize() == "FREQ=WEEKLY;BYDAY=TU,TH"


def test_monthly_presets():
    assert RRuleModel.monthly_by_date(15).serialize() == \
        "FREQ=MONTHLY;BYMONTHDAY=15"
    assert RRuleModel.monthly_by_nth_weekday(
        ByDay(2, Weekday.TU)).serialize() == "FREQ=MONTHLY;BYDAY=2TU"
    assert RRuleModel.yearly().serialize() == "FREQ=YEARLY"


def test_weekdays_preset_is_weekly_mo_through_fr():
    expected = RRuleModel(
        Frequency.WEEKLY,
        by_day=tuple(ByDay(None, d) for d in
                     (Weekday.MO, Weekday.TU, Weekday.WE, Weekday.TH,
                      Weekday.FR)))
    assert RRuleModel.weekdays() == expected
    assert RRuleModel.weekdays().serialize() == \
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"


def _round_trip_cases():
    return [
        RRuleModel.daily(),
        RRuleModel.weekdays(),
        RRuleModel.yearly(),
        RRuleModel.monthly_by_date(31),
        RRuleModel.monthly_by_date(-1),
        RRuleModel.monthly_by_nth_weekday(ByDay(1, Weekday.MO)),
        RRuleModel.monthly_by_nth_weekday(ByDay(-1, Weekday.FR)),
        RRuleModel(Frequency.WEEKLY, interval=2,
                   by_day=(ByDay(None, Weekday.WE),)),
        RRuleModel(Frequency.MONTHLY, interval=3,
                   by_day=(ByDay(2, Weekday.TU),),
                   by_month_day=(1, -1)),
        RRuleModel(Frequency.YEARLY, by_month_day=(29,)),
        RRuleModel(Frequency.DAILY, end=EndCount(12)),
        RRuleModel(Frequency.DAILY,
                   end=EndUntil(millis("2026-12-31T23:59:59Z"))),
        RRuleModel(Frequency.DAILY,
                   end=EndUntil(utc_midnight("2026-08-31"))),
        RRuleModel(Frequency.WEEKLY, by_day=(ByDay(None, Weekday.MO),),
                   end=EndUntil(utc_midnight("2026-08-31"), date_only=True)),
        RRuleModel(Frequency.WEEKLY, interval=2,
                   by_day=(ByDay(None, Weekday.MO),
                           ByDay(None, Weekday.FR)),
                   end=EndCount(6)),
        RRuleModel(Frequency.YEARLY, week_start=Weekday.MO),
        RRuleModel(Frequency.YEARLY, by_month_day=(3,), by_month=(7,),
                   week_start=Weekday.MO),
        RRuleModel(Frequency.WEEKLY, interval=2,
                   by_day=(ByDay(None, Weekday.SU),),
                   week_start=Weekday.SU),
        RRuleModel(Frequency.MONTHLY, by_day=(ByDay(None, Weekday.MO),),
                   by_set_pos=(1,)),
    ]


def test_round_trip_preserves_every_model_shape():
    for model in _round_trip_cases():
        serialized = model.serialize()
        result = RRuleModel.parse(serialized)
        assert isinstance(result, RuleParsed), \
            f"expected Parsed for {serialized!r} but was {result!r}"
        assert result.rule == model


def test_case_varied_input_reparses_to_canonical():
    raw = "freq=Weekly;Interval=2;byday=Mo,We,Fr;count=10"
    result = RRuleModel.parse(raw)
    assert isinstance(result, RuleParsed)
    assert result.rule.serialize() == \
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR;COUNT=10"


def _refused(raw):
    result = RRuleModel.parse(raw)
    assert isinstance(result, RuleRefused), \
        f"expected Refused for {raw!r} but was {result!r}"
    return result.reason


def test_rrule_refusals():
    assert "empty" in _refused("")
    assert "FREQ" in _refused("INTERVAL=2")
    assert "HOURLY" in _refused("FREQ=HOURLY")
    assert "unknown frequency" in _refused("FREQ=FORTNIGHTLY")
    assert "INTERVAL" in _refused("FREQ=DAILY;INTERVAL=0")
    assert "BYDAY" in _refused("FREQ=WEEKLY;BYDAY=XX")
    assert "BYMONTHDAY" in _refused("FREQ=MONTHLY;BYMONTHDAY=32")
    assert "BYMONTH" in _refused("FREQ=YEARLY;BYMONTH=13")
    assert "BYSETPOS" in _refused("FREQ=MONTHLY;BYSETPOS=0")
    assert "WKST" in _refused("FREQ=WEEKLY;WKST=XX")
    assert "UNTIL" in _refused("FREQ=DAILY;UNTIL=tomorrow")
    assert "COUNT" in _refused("FREQ=DAILY;COUNT=0")
    assert "UNTIL and COUNT" in _refused(
        "FREQ=DAILY;UNTIL=20260831T000000Z;COUNT=3")
    assert "ordinal" in _refused("FREQ=WEEKLY;BYDAY=2MO")
    assert "unsupported rule part" in _refused("FREQ=DAILY;BYHOUR=9")
    assert "unsupported rule part" in _refused("FREQ=YEARLY;BYYEARDAY=100")
    assert "unsupported rule part" in _refused("FREQ=YEARLY;BYWEEKNO=20")


def test_until_date_only_round_trip():
    model = RRuleModel(Frequency.DAILY,
                       end=EndUntil(utc_midnight("2026-08-31"),
                                    date_only=True))
    assert model.serialize() == "FREQ=DAILY;UNTIL=20260831"
    result = RRuleModel.parse(model.serialize())
    assert isinstance(result, RuleParsed)
    assert result.rule == model


# --------------------------------------------------- ScopeResolver ports

PARENT_UID = "parent-1"
TIMED_INSTANCE = millis("2026-08-17T14:00:00Z")
ALL_DAY_INSTANCE = millis("2026-08-17T00:00:00Z")
WEEKLY_NEVER = RRuleModel(Frequency.WEEKLY,
                          by_day=(ByDay(None, Weekday.MO),))
COUNT_BOUNDED = RRuleModel(Frequency.DAILY, end=EndCount(10))
EDITS = EventFieldEdits(title="moved", location="annex")


def _ctx(rule, start=TIMED_INSTANCE, all_day=False):
    return RecurringEventContext(parent_uid=PARENT_UID, rule=rule,
                                 start_millis=start, all_day=all_day)


def _inst(start=TIMED_INSTANCE):
    return InstanceRef(PARENT_UID, start)


def test_non_recurring_edit_ignores_scope():
    result = ScopeResolver.resolve_edit(_ctx(None),
                                        RecurrenceScope.THIS_INSTANCE,
                                        _inst(), EDITS)
    assert isinstance(result, UpdateParentRow)
    assert result.parent_uid == PARENT_UID
    assert result.edits is EDITS


def test_non_recurring_delete_ignores_scope():
    result = ScopeResolver.resolve_delete(
        _ctx(None), RecurrenceScope.THIS_AND_FOLLOWING, _inst())
    assert result == DeleteParentRow(PARENT_UID)


def test_edit_this_instance_inserts_exception():
    result = ScopeResolver.resolve_edit(_ctx(WEEKLY_NEVER),
                                        RecurrenceScope.THIS_INSTANCE,
                                        _inst(), EDITS)
    assert isinstance(result, InsertExceptionRow)
    assert result.parent_uid == PARENT_UID
    assert result.original_instance_time_millis == TIMED_INSTANCE
    assert result.new_row_edits is EDITS


def test_exception_stamps_instance_time_not_series_start():
    series_start = TIMED_INSTANCE - 14 * 86400000
    result = ScopeResolver.resolve_edit(
        _ctx(WEEKLY_NEVER, start=series_start),
        RecurrenceScope.THIS_INSTANCE, InstanceRef(PARENT_UID,
                                                   TIMED_INSTANCE),
        EDITS)
    assert isinstance(result, InsertExceptionRow)
    assert result.original_instance_time_millis == TIMED_INSTANCE


def test_edit_this_and_following_splits_with_just_before_until():
    rule = RRuleModel(Frequency.MONTHLY, interval=2,
                      by_day=(ByDay(1, Weekday.WE), ByDay(-1, Weekday.FR)),
                      by_month_day=(1, -1))
    result = ScopeResolver.resolve_edit(_ctx(rule),
                                        RecurrenceScope.THIS_AND_FOLLOWING,
                                        _inst(), EDITS)
    assert isinstance(result, SplitParent)
    assert result.parent_uid == PARENT_UID
    assert result.new_until.until_millis_utc == TIMED_INSTANCE - 1
    assert result.new_until.date_only is False
    assert result.new_row_start_millis == TIMED_INSTANCE
    assert result.new_row_edits is EDITS
    assert result.remaining_rule == rule


def test_edit_this_and_following_all_day_uses_date_only_until():
    result = ScopeResolver.resolve_edit(
        _ctx(WEEKLY_NEVER, start=ALL_DAY_INSTANCE, all_day=True),
        RecurrenceScope.THIS_AND_FOLLOWING,
        InstanceRef(PARENT_UID, ALL_DAY_INSTANCE), EDITS)
    assert isinstance(result, SplitParent)
    assert result.new_until.until_millis_utc == ALL_DAY_INSTANCE - 1
    assert result.new_until.date_only is True


def test_edit_all_events_updates_parent():
    result = ScopeResolver.resolve_edit(_ctx(WEEKLY_NEVER),
                                        RecurrenceScope.ALL_EVENTS,
                                        _inst(), EDITS)
    assert result == UpdateParentRow(PARENT_UID, EDITS)


def test_count_bounded_splits_are_refused():
    edit = ScopeResolver.resolve_edit(
        _ctx(COUNT_BOUNDED), RecurrenceScope.THIS_AND_FOLLOWING,
        _inst(), EDITS)
    assert isinstance(edit, Refusal)
    delete = ScopeResolver.resolve_delete(
        _ctx(COUNT_BOUNDED), RecurrenceScope.THIS_AND_FOLLOWING, _inst())
    assert isinstance(delete, Refusal)


def test_delete_scopes():
    single = ScopeResolver.resolve_delete(_ctx(WEEKLY_NEVER),
                                          RecurrenceScope.THIS_INSTANCE,
                                          _inst())
    assert single == DeleteInstanceUri(PARENT_UID, TIMED_INSTANCE)
    following = ScopeResolver.resolve_delete(
        _ctx(WEEKLY_NEVER), RecurrenceScope.THIS_AND_FOLLOWING, _inst())
    assert following == SetUntil(
        PARENT_UID, EndUntil(TIMED_INSTANCE - 1, date_only=False))
    everything = ScopeResolver.resolve_delete(_ctx(WEEKLY_NEVER),
                                              RecurrenceScope.ALL_EVENTS,
                                              _inst())
    assert everything == DeleteParentRow(PARENT_UID)


# ------------------------------------------------------- TimeMath ports

NY = ZoneInfo("America/New_York")


def test_all_day_round_trip_across_zones():
    for zone_name in ("Etc/GMT+11", "UTC", "Pacific/Tongatapu"):
        zone = ZoneInfo(zone_name)
        seen = TimeMath.local_date_of(millis("2026-06-16T10:00:00Z"), zone)
        storage = TimeMath.all_day_date_to_storage(seen)
        assert storage == utc_midnight(seen.isoformat())
        assert TimeMath.storage_to_all_day_date(storage) == seen


def test_all_day_storage_is_utc_midnight():
    day = date(2026, 1, 31)
    storage = TimeMath.all_day_date_to_storage(day)
    assert storage == utc_midnight("2026-01-31")
    assert TimeMath.storage_to_all_day_date(storage) == day


def test_spring_forward_midnight_in_new_york():
    day = date(2026, 3, 8)
    start = TimeMath.local_day_start(day, NY)
    assert start == millis("2026-03-08T05:00:00Z")
    assert TimeMath.local_date_of(start, NY) == day


def test_fall_back_midnight_in_new_york():
    day = date(2026, 11, 1)
    start = TimeMath.local_day_start(day, NY)
    assert start == millis("2026-11-01T04:00:00Z")
    assert TimeMath.local_date_of(start, NY) == day


def test_timezone_visibility_folds_aliases():
    assert TimeMath.should_render_timezone("America/New_York",
                                           "America/New_York") is False
    assert TimeMath.should_render_timezone("US/Eastern",
                                           "America/New_York") is False
    assert TimeMath.should_render_timezone("America/New_York",
                                           "Pacific/Tongatapu") is True
    assert TimeMath.should_render_timezone("Not/A-Zone",
                                           "America/New_York") is True
    assert TimeMath.should_render_timezone("Not/A-Zone",
                                           "Also/Bogus") is False


# ------------------------------------------------- AgendaGrouping ports


def _timed(uid, start_iso, end_iso):
    return InstanceSpan(uid, millis(start_iso), millis(end_iso),
                        all_day=False)


def _all_day(uid, start_iso, end_iso):
    return InstanceSpan(uid, millis(start_iso), millis(end_iso),
                        all_day=True)


def test_single_day_event_buckets_under_local_date():
    span = _timed("1", "2026-08-17T14:00:00Z", "2026-08-17T15:00:00Z")
    buckets = AgendaGrouping.group([span], NY)
    assert [b.date for b in buckets] == [date(2026, 8, 17)]
    assert buckets[0].timed == [span]
    assert buckets[0].all_day == []


def test_empty_days_are_dropped():
    monday = _timed("1", "2026-08-17T14:00:00Z", "2026-08-17T15:00:00Z")
    wednesday = _timed("2", "2026-08-19T14:00:00Z", "2026-08-19T15:00:00Z")
    buckets = AgendaGrouping.group([monday, wednesday], NY)
    assert [b.date for b in buckets] == [date(2026, 8, 17),
                                         date(2026, 8, 19)]


def test_multi_day_span_covers_each_date_end_exclusive():
    span = _timed("1", "2026-08-18T09:00:00-04:00",
                  "2026-08-20T09:00:00-04:00")
    buckets = AgendaGrouping.group([span], NY)
    assert [b.date for b in buckets] == [date(2026, 8, 18),
                                         date(2026, 8, 19),
                                         date(2026, 8, 20)]
    assert all(b.timed == [span] for b in buckets)


def test_event_ending_at_midnight_does_not_bleed():
    span = _timed("1", "2026-08-18T09:00:00-04:00",
                  "2026-08-19T00:00:00-04:00")
    buckets = AgendaGrouping.group([span], NY)
    assert [b.date for b in buckets] == [date(2026, 8, 18)]


def test_zero_length_event_covers_start_date_only():
    span = _timed("1", "2026-08-18T09:00:00-04:00",
                  "2026-08-18T09:00:00-04:00")
    buckets = AgendaGrouping.group([span], NY)
    assert [b.date for b in buckets] == [date(2026, 8, 18)]


def test_all_day_pinned_ahead_and_ties_by_uid():
    late = _timed("1", "2026-08-18T09:00:00-04:00",
                  "2026-08-18T10:00:00-04:00")
    early = _timed("2", "2026-08-18T07:00:00-04:00",
                   "2026-08-18T08:00:00-04:00")
    all_day = _all_day("3", "2026-08-18T00:00:00Z", "2026-08-19T00:00:00Z")
    first = _timed("4", "2026-08-18T06:00:00-04:00",
                   "2026-08-18T06:30:00-04:00")
    bucket = AgendaGrouping.group([late, all_day, first, early], NY)[0]
    assert bucket.all_day == [all_day]
    assert bucket.timed == [first, early, late]


def test_pathological_span_is_capped_at_one_year():
    span = InstanceSpan("x", millis("2026-01-01T00:00:00Z"),
                        millis("2030-01-01T00:00:00Z"), all_day=False)
    buckets = AgendaGrouping.group([span], NY)
    assert len(buckets) == 366


# -------------------------------------------------- SigilAssigner ports


def _key(n):
    return CalendarKey(f"cal-{n:d}", "account")


def test_first_calendar_gets_tier_one():
    result = SigilAssigner.assign({}, [_key(1)])
    assert result.assignments[_key(1)] == SigilTier.TIER_1
    assert result.newly_assigned == [_key(1)]


def test_existing_assignments_are_immutable():
    first = SigilAssigner.assign({}, [_key(1), _key(2)])
    second = SigilAssigner.assign(first.assignments, [_key(1), _key(2),
                                                      _key(3)])
    assert second.assignments[_key(1)] == first.assignments[_key(1)]
    assert second.assignments[_key(2)] == first.assignments[_key(2)]
    assert second.newly_assigned == [_key(3)]


def test_freed_tiers_are_reused_lowest_first():
    first = SigilAssigner.assign({}, [_key(1), _key(2)])
    remaining = {k: v for k, v in first.assignments.items() if k != _key(1)}
    result = SigilAssigner.assign(remaining, [_key(1), _key(2)])
    assert result.assignments[_key(1)] == SigilTier.TIER_1
    assert result.assignments == first.assignments


def test_override_wins_and_pool_skips_overridden_tier():
    pinned, fresh = _key(1), _key(2)
    result = SigilAssigner.assign({}, [pinned, fresh],
                                  {pinned: SigilTier.TIER_4})
    assert result.assignments[pinned] == SigilTier.TIER_4
    assert result.assignments[fresh] == SigilTier.TIER_1


def test_override_sticks_and_replaces_existing():
    pinned = _key(1)
    first = SigilAssigner.assign({}, [pinned],
                                 {pinned: SigilTier.TIER_4})
    second = SigilAssigner.assign(first.assignments, [pinned],
                                  {pinned: SigilTier.TIER_4})
    assert second.assignments[pinned] == SigilTier.TIER_4
    assert second.newly_assigned == []
    replaced = SigilAssigner.assign({_key(1): SigilTier.TIER_3}, [_key(1)],
                                    {_key(1): SigilTier.TIER_2})
    assert replaced.assignments == {_key(1): SigilTier.TIER_2}


def test_fifth_calendar_falls_back_to_tier_one():
    candidates = [_key(n) for n in range(1, 7)]
    result = SigilAssigner.assign({}, candidates)
    assert [result.assignments[c] for c in candidates[:4]] == list(SigilTier)
    assert result.assignments[_key(5)] == SigilTier.TIER_1
    assert result.assignments[_key(6)] == SigilTier.TIER_1


def test_four_tiers_have_glyphs_and_ramps():
    assert [t.glyph for t in SigilTier] == ["▌", "▏", "░", "▒"]
    assert {t.ramp_name for t in SigilTier} == {"text", "strong", "muted",
                                               "shade"}


# --------------------------------------------- ReminderPlanner ports

NOW = millis("2026-08-20T12:00:00Z")
MINUTE = 60000
HOUR = 3600000


def _instance(uid="e1", start="2026-08-21T14:00:00Z", all_day=False,
              declined=False, minutes=None):
    return PlannerInstance(event_uid=uid, start_millis=millis(start),
                           all_day=all_day, declined=declined,
                           reminder_minutes=list(minutes if minutes is not
                                                 None else [30]))


def test_standard_reminder_triggers_minutes_before_start():
    inst = _instance(minutes=[30])
    assert ReminderPlanner.plan([inst], NOW) == {
        AlarmKey(inst.event_uid, inst.start_millis,
                 millis("2026-08-21T13:30:00Z"))}


def test_zero_minute_reminder_fires_at_start():
    inst = _instance(minutes=[0])
    assert ReminderPlanner.plan([inst], NOW) == {
        AlarmKey(inst.event_uid, inst.start_millis, inst.start_millis)}


def test_multiple_and_duplicate_reminder_rows():
    inst = _instance(minutes=[30, 10, 60])
    keys = ReminderPlanner.plan([inst], NOW)
    assert keys == {AlarmKey(inst.event_uid, inst.start_millis,
                             inst.start_millis - m * MINUTE)
                    for m in (60, 30, 10)}
    assert len(ReminderPlanner.plan([_instance(minutes=[30, 30])],
                                    NOW)) == 1


def test_declined_never_notifies():
    declined = _instance(uid="d", declined=True)
    accepted = _instance(uid="a")
    assert ReminderPlanner.plan([declined, accepted], NOW) == \
        ReminderPlanner.plan([accepted], NOW)


def test_past_triggers_skipped_without_grace():
    start = NOW + 10 * MINUTE
    inst = PlannerInstance("e1", start, False, False, [30])
    assert ReminderPlanner.plan([inst], NOW) == set()
    edge = PlannerInstance("e1", NOW + 30 * MINUTE, False, False, [30])
    assert ReminderPlanner.plan([edge], NOW) == set()
    future = PlannerInstance("e1", NOW + 1 + 30 * MINUTE, False, False,
                             [30])
    assert ReminderPlanner.plan([future], NOW) == {
        AlarmKey("e1", future.start_millis, NOW + 1)}


def test_all_day_uses_canonical_18h_lead():
    start = millis("2026-08-22T00:00:00Z")
    inst = PlannerInstance("e7", start, True, False, [30])
    keys = ReminderPlanner.plan([inst], NOW)
    assert keys == {AlarmKey("e7", start, start - ALL_DAY_LEAD_MILLIS)}
    assert keys.pop().trigger_at_millis == start - 18 * HOUR


def test_all_day_minutes_ignored_and_empty_means_silent():
    start = millis("2026-08-22T00:00:00Z")
    inst = PlannerInstance("e7", start, True, False, [1440, 30, 0])
    assert len(ReminderPlanner.plan([inst], NOW)) == 1
    assert ReminderPlanner.plan(
        [_instance(minutes=[]),
         PlannerInstance("e9", NOW + HOUR, True, False, [])], NOW) == set()


def test_all_day_policy_anchor():
    start = millis("2026-08-22T00:00:00Z")
    policy = AllDayNotification(hour_of_day=18, days_before=1)
    zone = ZoneInfo("Europe/Berlin")
    inst = PlannerInstance("e7", start, True, False, [30])
    keys = ReminderPlanner.plan([inst], NOW, policy, zone)
    expected = int(datetime(2026, 8, 21, 18, 0, tzinfo=zone)
                   .timestamp() * 1000)
    assert keys == {AlarmKey("e7", start, expected)}


def test_compute_plan_diffs_only_what_differs():
    first = AlarmKey("1", 100, 50)
    second = AlarmKey("2", 200, 150)
    third = AlarmKey("3", 300, 250)
    plan = ReminderPlanner.compute_plan({second, third}, {first, third})
    assert set(plan.to_set) == {second}
    assert set(plan.to_cancel) == {first}
    settled = ReminderPlanner.compute_plan({second, third},
                                           {second, third})
    assert settled.to_set == () and settled.to_cancel == ()


def test_plan_output_is_deterministic():
    instances = [_instance(uid="e5", minutes=[15]),
                 _instance(uid="e2", minutes=[45, 10]),
                 PlannerInstance("e9", NOW + 25 * HOUR, True, False,
                                 [120])]
    first = sorted(ReminderPlanner.plan(instances, NOW))
    second = sorted(ReminderPlanner.plan(list(reversed(instances)), NOW))
    assert first == second


# ------------------------------------------- AlarmKeys + heartbeat ports


def test_alarm_keys_codec_round_trip():
    keys = {AlarmKey("a1", 100, 50), AlarmKey("b2", -5, 0),
            AlarmKey("c3", 2 ** 62, 2 ** 62)}
    assert AlarmKeys.decode_all(AlarmKeys.encode_all(keys)) == keys


def test_alarm_keys_codec_drops_malformed_lines():
    blob = "a:1:2\n\nnot-a-key\nb:4\nc:6:7:8:9\n7:8:9"
    assert AlarmKeys.decode_all(blob) == {AlarmKey("a", 1, 2),
                                          AlarmKey("7", 8, 9)}
    assert AlarmKeys.decode("x:y:z") is None


def test_stable_request_code_is_deterministic_and_spread():
    first = AlarmKey("e1", 100, 50)
    assert AlarmKeys.stable_request_code(first) == \
        AlarmKeys.stable_request_code(first)
    codes = {AlarmKeys.stable_request_code(AlarmKey(f"e{n:d}", n * 1000,
                                                    n))
             for n in range(1, 201)}
    assert len(codes) == 200


def _berlin_wall(year, month, day, hour, minute):
    zone = ZoneInfo("Europe/Berlin")
    return int(datetime(year, month, day, hour, minute, tzinfo=zone)
               .timestamp() * 1000)


def test_heartbeat_before_0300_lands_same_day():
    zone = ZoneInfo("Europe/Berlin")
    now = _berlin_wall(2026, 8, 20, 1, 0)
    assert HeartbeatClock.next_after(now, zone) == \
        _berlin_wall(2026, 8, 20, 3, 0)


def test_heartbeat_after_0300_rolls_to_tomorrow():
    zone = ZoneInfo("Europe/Berlin")
    now = millis("2026-08-20T14:00:00Z")
    assert HeartbeatClock.next_after(now, zone) == \
        _berlin_wall(2026, 8, 21, 3, 0)


def test_heartbeat_exactly_at_0300_is_strictly_after():
    zone = ZoneInfo("Europe/Berlin")
    now = _berlin_wall(2026, 8, 20, 3, 0)
    assert HeartbeatClock.next_after(now, zone) == \
        _berlin_wall(2026, 8, 21, 3, 0)
    assert HeartbeatClock.next_after(now, zone) > now


def test_end_until_date_only_boundary_semantics():
    until = EndUntil(utc_midnight("2026-08-31"), date_only=True)
    assert until.date_only is True
    assert EndNever() != until
