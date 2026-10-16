"""Manual weight + activity entry. Thin Adw dialogs over store/entries.

Validation mirrors WeightEntryModel/ActivityEntryModel: storage is always
kg/metres, queue kinds stay wire-compatible (`weight`, `weight.update`,
`weight.delete`, `activity`, `daily`). Submits write Room first and enqueue
in the same step, so offline entry round-trips on the next sync.
"""

from __future__ import annotations

from xx_vitals.store import entries

WEIGHT_KG_MIN = 20.0
WEIGHT_KG_MAX = 300.0


def validate_weight_input(raw_value: str, units: str) -> tuple[float | None, str | None]:
    try:
        typed = float(raw_value.strip())
    except (ValueError, AttributeError):
        return None, "Enter a number"
    kg = entries.to_kg(typed, entries.unit_for(units))
    if not WEIGHT_KG_MIN <= kg <= WEIGHT_KG_MAX:
        return None, "Enter a plausible weight"
    return entries.round_for_storage(kg), None


def validate_activity_input(
    type_name: str, duration_raw: str, steps_raw: str,
) -> tuple[dict | None, str | None]:
    cleaned = (type_name or "").strip().lower()
    if not cleaned:
        return None, "Pick an activity type"
    duration = entries.parse_duration_minutes(duration_raw or "")
    if duration is None:
        return None, "Duration must be 1–1440 minutes"
    try:
        steps = entries.parse_optional_steps(steps_raw or "")
    except AttributeError:
        steps = None
    if steps_raw and (steps_raw or "").strip() and steps is None:
        return None, "Steps must be a whole number"
    return {"type": cleaned, "duration_min": duration, "steps": steps}, None


def submit_weight(db, units: str, raw_value: str, measured_at: int) -> tuple[int | None, str | None]:
    from xx_vitals.store.db import WeightSample

    kg, error = validate_weight_input(raw_value, units)
    if error is not None:
        return None, error
    assert kg is not None
    repo = entries.WeightRepository(db)
    row_id = repo.add_weight(
        WeightSample(source_id=entries.SOURCE_MANUAL, measured_at=measured_at, weight_kg=kg),
        entries.weight_payload(entries.SOURCE_MANUAL, measured_at, kg),
    )
    return row_id, None


def submit_activity(
    db, type_name: str, duration_raw: str, steps_raw: str,
    now_ms: int, height_metres: float, weight_kg: float | None,
) -> tuple[int | None, str | None]:
    fields, error = validate_activity_input(type_name, duration_raw, steps_raw)
    if error is not None:
        return None, error
    assert fields is not None
    derived = entries.derive_activity(fields["steps"], fields["duration_min"], height_metres, weight_kg)
    started, ended = entries.activity_window(now_ms, fields["duration_min"])
    repo = entries.ActivityRepository(db)
    row_id = repo.log_activity(
        fields["type"], started, ended,
        derived.distance_m, derived.calories_kcal,
        derived.effort_points, derived.active_min, fields["steps"],
    )
    return row_id, None


def build_weight_dialog(app_window, db, units: str, measured_at: int, on_saved=None):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    dialog = Adw.Dialog(title="Log weight")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(16)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_bottom(16)
    unit = entries.unit_for(units)
    hint = "kg" if unit == entries.WeightUnit.KG else "lb"
    field = Gtk.Entry(placeholder_text=f"Weight ({hint})")
    field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    box.append(field)
    error_label = Gtk.Label(label="")
    error_label.set_wrap(True)
    error_label.add_css_class("error")
    box.append(error_label)
    save = Gtk.Button(label="Save")
    save.add_css_class("suggested-action")

    def on_save(_button) -> None:
        row_id, error = submit_weight(db, units, field.get_text(), measured_at)
        if error is not None:
            error_label.set_label(error)
            return
        dialog.close()
        if on_saved is not None:
            on_saved(row_id)

    save.connect("clicked", on_save)
    box.append(save)
    clamp = Adw.Clamp(maximum_size=480)
    clamp.set_child(box)
    dialog.set_child(clamp)
    dialog.present(app_window)
    return dialog


def build_activity_dialog(app_window, db, now_ms: int, height_metres: float,
                           weight_kg: float | None, on_saved=None):
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    dialog = Adw.Dialog(title="Log activity")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(16)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_bottom(16)
    type_field = Gtk.Entry(placeholder_text="Type (walk, run, cycle, other)")
    duration_field = Gtk.Entry(placeholder_text="Duration (minutes)")
    duration_field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    steps_field = Gtk.Entry(placeholder_text="Steps (optional)")
    steps_field.set_input_purpose(Gtk.InputPurpose.NUMBER)
    for widget in (type_field, duration_field, steps_field):
        box.append(widget)
    error_label = Gtk.Label(label="")
    error_label.set_wrap(True)
    error_label.add_css_class("error")
    box.append(error_label)
    save = Gtk.Button(label="Save")
    save.add_css_class("suggested-action")

    def on_save(_button) -> None:
        row_id, error = submit_activity(
            db, type_field.get_text(), duration_field.get_text(),
            steps_field.get_text(), now_ms, height_metres, weight_kg,
        )
        if error is not None:
            error_label.set_label(error)
            return
        dialog.close()
        if on_saved is not None:
            on_saved(row_id)

    save.connect("clicked", on_save)
    box.append(save)
    clamp = Adw.Clamp(maximum_size=480)
    clamp.set_child(box)
    dialog.set_child(clamp)
    dialog.present(app_window)
    return dialog
