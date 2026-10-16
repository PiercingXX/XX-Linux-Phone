"""L5 app shell: Adw.Application + NavigationView with Schedule and Month.

Reads instances through the service's ``ListInstances`` operation running
in-process over the GUI's store handle (same code, identical results by
construction, no D-Bus round trip on the hot path). Every write goes
through the service — over D-Bus when the daemon owns the bus, otherwise
through the in-process handle, which is the single writer exactly when no
daemon runs. ``Changed`` (sender ``io.piercingxx.Calendar``) refreshes
both pages after a 2 s debounce, like Android's ProviderObserver.
"""
from __future__ import annotations

import datetime as _datetime
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from xx_calendar import views  # noqa: E402
from xx_calendar.app_config import load_settings  # noqa: E402
from xx_calendar.models import CalendarKey, SigilAssigner  # noqa: E402
from xx_calendar.service import (  # noqa: E402
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    CalendarService,
)
from xx_calendar.store import CalendarStore, default_db_path  # noqa: E402
from xx_calendar.theme import (  # noqa: E402
    APP_ID,
    css_for_theme,
    load_xxwm_config,
    theme_from_xxwm_config,
)
from xx_calendar.theme import ThemeWatcher  # noqa: E402

SWIPE_VELOCITY_PX_S = 400.0
CHANGED_DEBOUNCE_S = 2
RELOAD_MARGIN_DAYS = 14


def parse_launch_args(argv: list[str]) -> dict:
    """Split our flags from *argv*; unparseable input opens Schedule."""
    opts: dict = {"date": None, "today": False, "new_event": False,
                  "opens": []}
    rest = list(argv)
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("--date="):
            opts["date"] = views.parse_date_arg(arg[len("--date="):])
            rest.pop(i)
        elif arg == "--date" and i + 1 < len(rest):
            opts["date"] = views.parse_date_arg(rest[i + 1])
            rest.pop(i)
            rest.pop(i)
        elif arg in ("--today", "-t"):
            opts["today"] = True
            rest.pop(i)
        elif arg in ("--new-event", "--new_event"):
            opts["new_event"] = True
            rest.pop(i)
        else:
            if not arg.startswith("-"):
                parsed = views.parse_app_uri(arg)
                if parsed is not None:
                    opts["date"] = parsed
                    rest.pop(i)
                    continue
                opts["opens"].append(arg)
                rest.pop(i)
                continue
            i += 1
    opts["rest"] = rest
    return opts


def local_zone():
    """System local zone as an IANA ZoneInfo (per-date DST), else fixed."""
    from zoneinfo import ZoneInfo

    names = [os.environ.get("TZ", "").lstrip(":").strip()]
    try:
        with open("/etc/timezone", encoding="utf-8") as handle:
            names.append(handle.readline().strip())
    except OSError:
        pass
    try:
        names.append(os.readlink("/etc/localtime").partition("zoneinfo/")[2])
    except OSError:
        pass
    for name in filter(None, names):
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return _datetime.datetime.now().astimezone().tzinfo or _datetime.timezone.utc


def parse_sheet_datetime(text: str, all_day: bool,
                         zone) -> int | None:
    """Parse create-sheet ``YYYY-MM-DD[ HH:MM]`` to UTC millis, else None."""
    cleaned = (text or "").strip()
    try:
        if all_day:
            day = _datetime.date.fromisoformat(cleaned)
            moment = _datetime.datetime(day.year, day.month, day.day,
                                        tzinfo=_datetime.timezone.utc)
        else:
            naive = _datetime.datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
            moment = naive.replace(tzinfo=zone)
    except ValueError:
        return None
    try:
        return int(moment.timestamp() * 1000)
    except (OverflowError, OSError):
        return None


class DayHeaderItem(GObject.Object):
    label = GObject.Property(type=str, default="")


class EventItem(GObject.Object):
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    when = GObject.Property(type=str, default="")
    sigil = GObject.Property(type=str, default="·")
    dimmed = GObject.Property(type=bool, default=False)
    start = GObject.Property(type=GObject.TYPE_INT64, default=0)
    end = GObject.Property(type=GObject.TYPE_INT64, default=0)
    recurrence_id = GObject.Property(type=GObject.TYPE_INT64, default=0)


def agenda_list_model(days: list, dim_past: bool,
                      now_ms: int) -> Gio.ListStore:
    """Flat header/row model for a ListView; rows ellipsize downstream."""
    model = Gio.ListStore.new(GObject.Object)
    for day in days:
        header = DayHeaderItem()
        header.props.label = views.day_header(day.date)
        model.append(header)
        for row in list(day.all_day) + list(day.timed):
            item = EventItem()
            item.props.uid = row.uid
            item.props.title = row.title or views.UNTITLED
            item.props.start = int(row.start_ms)
            item.props.end = int(row.end_ms)
            item.props.recurrence_id = int(row.recurrence_id)
            item.props.sigil = views.sigil_for_tier(row.sigil_tier)
            if row.all_day:
                item.props.when = views.ALL_DAY_LABEL
            else:
                item.props.when = ""
            if row.end_ms > row.start_ms:
                past = row.end_ms < now_ms
            else:
                past = row.start_ms < now_ms
            item.props.dimmed = bool(dim_past and past)
            model.append(item)
    return model


class CalendarApp(Adw.Application):
    def __init__(self, launch: dict | None = None,
                 db_path: str | None = None) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._launch = launch or {"date": None, "today": False,
                                  "new_event": False, "opens": []}
        home = os.path.expanduser("~")
        self._db_path = db_path or default_db_path(home)
        self._window = None

    def do_activate(self) -> None:
        self._ensure_window()
        assert self._window is not None
        self._window.apply_launch(self._launch, initial=True)
        self._window.present()

    def do_open(self, files, _hint) -> None:
        self._ensure_window()
        assert self._window is not None
        for info in files:
            uri = info.get_uri() if hasattr(info, "get_uri") else None
            if uri and views.parse_app_uri(uri) is not None:
                self._window.jump_to(views.parse_app_uri(uri))
            elif uri and uri.lower().endswith(".ics"):
                self._window.import_ics_uri(uri)
        self._window.present()

    def _ensure_window(self) -> None:
        if self._window is None:
            self._window = CalendarWindow(self, self._db_path)


class CalendarWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, db_path: str) -> None:
        super().__init__(application=app)
        self.set_title("XX-Calendar")
        self.set_default_size(360, 720)
        self._store = CalendarStore(db_path)
        self._service = CalendarService(self._store, start_worker=False)
        self._settings = load_settings()
        self._zone = local_zone()
        self._sigils: dict = {}
        self._schedule = views.ScheduleWindow()
        self._month_year = self._schedule.picker_month()
        self._month_selected = None
        self._view = views.start_route(self._settings.get("default_view"))
        self._load_generation = 0
        self._reload_pending = False
        self._bus_connection = None
        self._last_dialog = None
        self._theme_watcher = ThemeWatcher(
            on_change=lambda _data: GLib.idle_add(self._apply_theme))
        self._build_chrome()
        self._apply_theme()
        self._theme_watcher.start()
        self._subscribe_changed()
        GLib.timeout_add_seconds(60, self._on_minute_tick)

    # ---------------------------------------------------------------- chrome

    def _build_chrome(self) -> None:
        bar = Adw.HeaderBar()
        bar.set_show_start_title_buttons(False)
        bar.set_show_end_title_buttons(False)
        self._title_button = Gtk.Button()
        self._title_button.connect("clicked", self._on_title_clicked)
        bar.pack_start(self._title_button)
        self._today_button = Gtk.Button(label="Today")
        self._today_button.connect("clicked", self._on_today_clicked)
        bar.pack_start(self._today_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.pack_start(spacer)
        view_button = Gtk.MenuButton(label="Schedule")
        view_button.set_can_focus(False)
        menu = Gio.Menu()
        for key, label in views.VIEWS:
            if key.lower() in views.IMPLEMENTED_VIEWS:
                action = f"app.view-{key.lower()}"
                menu.append(label, action)
                act = Gio.SimpleAction.new(f"view-{key.lower()}", None)
                act.connect("activate",
                            lambda _a, _p, tag=key.lower(): self.show_view(tag))
                self.get_application().add_action(act)
        view_button.set_menu_model(menu)
        self._view_button = view_button
        bar.pack_end(view_button)
        add_button = Gtk.Button(label="+")
        add_button.set_can_focus(False)
        add_button.connect("clicked",
                           lambda _b: self.open_create_sheet(None))
        bar.pack_end(add_button)

        self._nav = Adw.NavigationView()
        self._pages: dict[str, Adw.NavigationPage] = {}
        self._schedule_list, self._schedule_page = self._build_schedule()
        self._month_box, self._month_page = self._build_month()
        for tag, page in (("schedule", self._schedule_page),
                          ("month", self._month_page)):
            page.set_tag(tag)
            self._nav.add(page)
            self._pages[tag] = page
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(bar)
        box.append(self._nav)
        box.set_vexpand(True)
        self._nav.set_vexpand(True)
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(box)
        self.set_content(self._toasts)
        self.show_view(self._view)

    def _build_schedule(self):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_row_setup)
        factory.connect("bind", self._on_row_bind)
        self._schedule_model = Gio.ListStore.new(GObject.Object)
        selection = Gtk.NoSelection(model=self._schedule_model)
        view = Gtk.ListView(model=selection, factory=factory)
        view.set_single_click_activate(True)
        view.set_vexpand(True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(view)
        scroller.set_vexpand(True)
        scroller.connect("edge-reached", self._on_schedule_edge)
        swipe = Gtk.GestureSwipe()
        swipe.connect("swipe", self._on_schedule_swipe)
        view.add_controller(swipe)
        page = Adw.NavigationPage.new(scroller, "Schedule")
        return view, page

    def _build_month(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_vexpand(True)
        self._month_dow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(self._month_dow)
        self._month_grid = Gtk.Grid()
        self._month_grid.set_column_homogeneous(True)
        self._month_grid.set_row_homogeneous(False)
        self._month_grid.set_vexpand(True)
        grid_swipe = Gtk.GestureSwipe()
        grid_swipe.connect("swipe", self._on_month_swipe)
        self._month_grid.add_controller(grid_swipe)
        box.append(self._month_grid)
        peek_label = Gtk.Label(label="")
        peek_label.set_xalign(0.0)
        peek_label.add_css_class("xxc-body")
        box.append(peek_label)
        self._peek_label = peek_label
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_row_setup)
        factory.connect("bind", self._on_row_bind)
        self._peek_model = Gio.ListStore.new(GObject.Object)
        selection = Gtk.NoSelection(model=self._peek_model)
        peek = Gtk.ListView(model=selection, factory=factory)
        peek.set_single_click_activate(True)
        peek.set_vexpand(True)
        peek_scroller = Gtk.ScrolledWindow()
        peek_scroller.set_child(peek)
        peek_scroller.set_vexpand(True)
        box.append(peek_scroller)
        page = Adw.NavigationPage.new(box, "Month")
        return box, page

    # ------------------------------------------------------------- row paint

    @staticmethod
    def _on_row_setup(_factory, list_item) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(16)
        row.set_margin_end(16)
        row.set_margin_top(7)
        row.set_margin_bottom(7)
        sigil = Gtk.Label(label="·")
        sigil.set_xalign(0.0)
        when = Gtk.Label(label="")
        when.set_xalign(0.0)
        when.set_size_request(64, -1)
        title = Gtk.Label(label="")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(1)
        row.append(sigil)
        row.append(when)
        row.append(title)
        list_item.set_child(row)
        row._xxc_sigil = sigil
        row._xxc_when = when
        row._xxc_title = title

    @staticmethod
    def _on_row_bind(_factory, list_item) -> None:
        row = list_item.get_child()
        item = list_item.get_item()
        sigil = row._xxc_sigil
        when = row._xxc_when
        title = row._xxc_title
        if isinstance(item, DayHeaderItem):
            sigil.set_label("")
            when.set_label("")
            title.set_label(item.props.label)
            title.add_css_class("xxc-day-header")
        else:
            assert isinstance(item, EventItem)
            sigil.set_label(item.props.sigil)
            when.set_label(item.props.when or "")
            title.set_label(item.props.title)
            title.remove_css_class("xxc-day-header")
            for widget in (sigil, when, title):
                if item.props.dimmed:
                    widget.add_css_class("dim")
                else:
                    widget.remove_css_class("dim")

    # ---------------------------------------------------------------- views

    def show_view(self, tag: str) -> None:
        if tag not in self._pages:
            tag = views.ROUTE_SCHEDULE
        self._view = tag
        self._nav.replace_with_tags([tag])
        labels = {key.lower(): label for key, label in views.VIEWS}
        self._view_button.set_label(labels.get(tag, "Schedule"))
        self._refresh_chrome()
        self.reload()

    def jump_to(self, day) -> None:
        if day is None:
            return
        self._schedule.jump_to(day)
        self._month_year = (day.year, day.month)
        self._month_selected = day
        if self._view != views.ROUTE_MONTH:
            self.show_view(self._view)
        else:
            self.reload()
        self._refresh_chrome()

    def apply_launch(self, launch: dict, initial: bool = False) -> None:
        day = launch.get("date")
        if launch.get("today"):
            day = _datetime.date.today()
        if day is not None:
            self.jump_to(day)
        if launch.get("new_event") and initial:
            self.open_create_sheet(day)
        for arg in launch.get("opens", []):
            self.import_ics_uri(arg)

    def _refresh_chrome(self) -> None:
        today = _datetime.date.today()
        if self._view == views.ROUTE_MONTH:
            year, month = self._month_year
            self._title_button.set_label(views.month_label(year, month))
            self._today_button.set_visible((year, month)
                                           != (today.year, today.month))
        else:
            year, month = self._schedule.picker_month(today)
            self._title_button.set_label(views.month_label(year, month))
            focus = self._schedule.focus_date
            self._today_button.set_visible(focus is not None
                                           and focus != today)

    def _on_title_clicked(self, _button) -> None:
        dialog = Adw.Dialog()
        dialog.set_title("Pick a day")
        year, month = self._schedule.picker_month()
        if self._view == views.ROUTE_MONTH:
            year, month = self._month_year
        cal = Gtk.Calendar()
        cal.select_day(GLib.DateTime.new_local(year, month, 1))
        dialog.set_child(cal)
        dialog.present(self)

        def on_picked(picked) -> None:
            moment = picked.get_date()
            dialog.close()
            self.jump_to(_datetime.date(moment.get_year(),
                                        moment.get_month(),
                                        moment.get_day_of_month()))

        cal.connect("day-selected", on_picked)

    def _on_today_clicked(self, _button) -> None:
        self.jump_to(_datetime.date.today())

    # -------------------------------------------------------------- loading

    def _visible_calendar_ids(self) -> list[str]:
        try:
            return [c.id for c in self._store.list_calendars()
                    if c.is_visible]
        except Exception:
            return []

    def _assign_sigils(self) -> None:
        try:
            calendars = self._store.list_calendars()
        except Exception:
            return
        assignment = SigilAssigner.assign(
            self._sigils,
            [CalendarKey(c.id, c.account_name or "") for c in calendars])
        self._sigils = dict(assignment.assignments)

    def reload(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        if self._view == views.ROUTE_SCHEDULE:
            start_ms, end_ms = views.schedule_window_ms(self._schedule,
                                                        self._zone)
        else:
            year, month = self._month_year
            start_ms, end_ms = views.month_window_ms(year, month, self._zone)
        ids = self._visible_calendar_ids()
        worker = threading.Thread(
            target=self._load_in_background,
            args=(generation, start_ms, end_ms, ids),
            name="calendar-load", daemon=True)
        worker.start()

    def _load_in_background(self, generation: int, start_ms: int,
                            end_ms: int, ids: list[str]) -> None:
        try:
            instances = self._service.list_instances(start_ms, end_ms, ids)
            plain = [{key: _variant_value(value)
                      for key, value in item.items()}
                     for item in instances]
        except Exception:
            plain = []
        GLib.idle_add(self._apply_loaded, generation, plain)

    def _apply_loaded(self, generation: int, instances: list[dict]) -> bool:
        if generation != self._load_generation:
            return False
        self._assign_sigils()
        tiers = {key.calendar_id: _tier_index(tier)
                 for key, tier in self._sigils.items()}
        for item in instances:
            item["sigil_tier"] = tiers.get(item.get("calendar_id", ""), 0)
        now_ms = int(_datetime.datetime.now(
            _datetime.timezone.utc).timestamp() * 1000)
        dim_past = bool(self._settings.get("dim_past", True))
        zone = self._zone
        if self._view == views.ROUTE_SCHEDULE:
            days = views.group_agenda(instances, zone,
                                      self._schedule.start_date,
                                      self._schedule.end_date)
            model = agenda_list_model(days, dim_past, now_ms)
            self._schedule_model.remove_all()
            for i in range(model.get_n_items()):
                self._schedule_model.append(model.get_item(i))
            if self._schedule.focus_date is not None:
                self._scroll_to_focus(days)
        else:
            self._render_month(instances, now_ms, dim_past)
        self._refresh_chrome()
        return False

    def _scroll_to_focus(self, days: list) -> None:
        focus = self._schedule.focus_date
        if focus is None:
            return
        index = 0
        for day in days:
            if day.date < focus:
                index += 1 + len(day.all_day) + len(day.timed)
            else:
                break
        self._schedule_list.scroll_to(index, Gtk.ListScrollFlags.NONE, None)

    def _render_month(self, instances: list[dict], now_ms: int,
                      dim_past: bool) -> None:
        year, month = self._month_year
        settings = self._settings
        first_weekday = _weekday_index(
            str(settings.get("start_day_of_week", "MONDAY")))
        show_week_numbers = bool(settings.get("week_numbers", False))
        for child in list(self._iter_box_children(self._month_dow)):
            self._month_dow.remove(child)
        if show_week_numbers:
            self._month_dow.append(_gutter_spacer())
        for label in views.weekday_labels(first_weekday):
            cell = Gtk.Label(label=label)
            cell.set_hexpand(True)
            cell.set_xalign(0.0)
            cell.add_css_class("xxc-dow")
            self._month_dow.append(cell)
        weeks = views.build_weeks(year, month, first_weekday)
        start = weeks[0][0][0]
        last = weeks[-1][-1][0] + _datetime.timedelta(days=1)
        by_date = views.events_by_date(instances, self._zone, start, last)
        while self._month_grid.get_child_at(0, 0) is not None:
            self._month_grid.remove_row(0)
        today = _datetime.date.today()
        for row_index, week in enumerate(weeks):
            col = 0
            if show_week_numbers:
                gutter = Gtk.Label(label=str(views.week_number(week[0][0])))
                gutter.add_css_class("dim")
                self._month_grid.attach(gutter, 0, row_index, 1, 1)
                col = 1
            for col_offset, (day, in_month) in enumerate(week):
                rows = by_date.get(day, [])
                button = Gtk.Button()
                button.set_can_focus(False)
                button.set_hexpand(True)
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                numeral = Gtk.Label(label=str(day.day))
                numeral.set_xalign(0.5)
                numeral.add_css_class("xxc-numeral")
                if day == today or day == self._month_selected:
                    numeral.add_css_class("xxc-inverted")
                elif not in_month:
                    numeral.add_css_class("dim")
                summary = Gtk.Label(
                    label=views.month_cell_summary(len(rows)))
                summary.set_xalign(0.5)
                summary.set_ellipsize(Pango.EllipsizeMode.END)
                summary.set_max_width_chars(6)
                inner.append(numeral)
                inner.append(summary)
                button.set_child(inner)
                button.connect("clicked", self._on_day_clicked, day)
                self._month_grid.attach(button, col + col_offset,
                                        row_index, 1, 1)
        selected = self._month_selected
        if selected is None:
            self._peek_label.set_label("")
            self._peek_model.remove_all()
            return
        self._peek_label.set_label(views.day_header(selected))
        rows = by_date.get(selected, [])
        day = views.AgendaDay(date=selected, timed=[], all_day=[])
        for item in rows:
            target = day.all_day if item["all_day"] else day.timed
            target.append(views.AgendaRow(
                uid=item["uid"], calendar_id=item.get("calendar_id", ""),
                title=item.get("title", "") or views.UNTITLED,
                start_ms=int(item["start"]), end_ms=int(item["end"]),
                all_day=bool(item["all_day"]),
                recurrence_id=int(item.get("recurrence_id", 0)),
                status=item.get("status", ""),
                sigil_tier=int(item.get("sigil_tier", 0))))
        model = agenda_list_model([day] if rows else [], dim_past, now_ms)
        self._peek_model.remove_all()
        for i in range(model.get_n_items()):
            entry = model.get_item(i)
            if isinstance(entry, EventItem):
                self._peek_model.append(entry)

    @staticmethod
    def _iter_box_children(box):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            yield child
            child = nxt

    def _on_day_clicked(self, _button, day) -> None:
        if self._month_selected == day:
            self.open_create_sheet(day)
        else:
            self._month_selected = day
            self.reload()

    # ------------------------------------------------------------ navigation

    def _on_schedule_edge(self, _scroller, pos) -> None:
        if pos == Gtk.PositionType.BOTTOM:
            self._schedule.extend_forward()
        elif pos == Gtk.PositionType.TOP:
            self._schedule.extend_backward()
        else:
            return
        self.reload()

    def _on_schedule_swipe(self, _gesture, velocity_x, velocity_y) -> None:
        direction = views.swipe_direction(velocity_x, velocity_y,
                                          SWIPE_VELOCITY_PX_S)
        if direction == 0:
            return
        anchor = self._schedule.focus_date or _datetime.date.today()
        self._schedule.focus_on(anchor + _datetime.timedelta(days=direction))
        self._ensure_focus_window()
        self.reload()

    def _ensure_focus_window(self) -> None:
        focus = self._schedule.focus_date
        if focus is None:
            return
        near_start = focus - self._schedule.start_date
        near_end = self._schedule.end_date - focus
        if near_start.days < RELOAD_MARGIN_DAYS:
            self._schedule.extend_backward()
        if near_end.days < RELOAD_MARGIN_DAYS:
            self._schedule.extend_forward()

    def shift_focus(self, days: int) -> None:
        anchor = self._schedule.focus_date or _datetime.date.today()
        self._schedule.focus_on(anchor + _datetime.timedelta(days=days))
        self._ensure_focus_window()
        self.reload()

    def _on_month_swipe(self, _gesture, velocity_x, velocity_y) -> None:
        direction = views.swipe_direction(velocity_x, velocity_y,
                                          SWIPE_VELOCITY_PX_S)
        if direction == 0:
            return
        year, month = self._month_year
        first = _datetime.date(year, month, 1)
        if direction > 0:
            nxt = first + _datetime.timedelta(days=32)
        else:
            nxt = first - _datetime.timedelta(days=1)
        self._month_year = (nxt.year, nxt.month)
        self.reload()

    # -------------------------------------------------------------- changed

    def _subscribe_changed(self) -> None:
        try:
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)
        except Exception:
            self._bus_connection = None

    def _on_bus_ready(self, _source, result) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except Exception:
            return
        self._bus_connection = connection
        try:
            connection.signal_subscribe(
                BUS_NAME, INTERFACE, "Changed", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_changed_signal)
        except Exception:
            pass

    def _on_changed_signal(self, _conn, _sender, _path, _iface, _signal,
                           params) -> None:
        try:
            _revision, _uids = params.unpack()
        except Exception:
            _revision, _uids = (0, [])
        del _revision, _uids
        self._schedule.force_refresh()
        if not self._reload_pending:
            self._reload_pending = True
            GLib.timeout_add_seconds(CHANGED_DEBOUNCE_S,
                                     self._on_changed_debounced)

    def _on_changed_debounced(self) -> bool:
        self._reload_pending = False
        self.reload()
        return False

    def _on_minute_tick(self) -> bool:
        self.reload()
        return True

    # ---------------------------------------------------------- create sheet

    def writable_calendars(self) -> list:
        try:
            return [c for c in self._store.list_calendars() if c.is_writable]
        except Exception:
            return []

    def open_create_sheet(self, day=None) -> None:
        calendars = self.writable_calendars()
        if not calendars:
            self._toasts.add_toast(
                Adw.Toast.new("No writable calendar yet"))
            return
        dialog = Adw.Dialog()
        dialog.set_title("New event")
        dialog.set_content_width(320)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        title_entry = Gtk.Entry()
        title_entry.set_placeholder_text("Title")
        content.append(title_entry)
        all_day_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=8)
        all_day_label = Gtk.Label(label="All day")
        all_day_label.set_hexpand(True)
        all_day_label.set_xalign(0.0)
        all_day_switch = Gtk.Switch()
        all_day_switch.set_can_focus(False)
        all_day_row.append(all_day_label)
        all_day_row.append(all_day_switch)
        content.append(all_day_row)
        start_entry = Gtk.Entry()
        end_entry = Gtk.Entry()
        duration_min = self._settings.get("default_duration_min", 30)
        try:
            duration_min = int(duration_min)
        except (TypeError, ValueError):
            duration_min = 30
        anchor = day or _datetime.date.today()
        start_entry.set_placeholder_text("Start: YYYY-MM-DD HH:MM")
        end_entry.set_placeholder_text("End: YYYY-MM-DD HH:MM")
        content.append(start_entry)
        content.append(end_entry)
        names = [f"{c.display_name or c.id}" for c in calendars]
        picker = Gtk.DropDown.new_from_strings(names)
        picker.set_selected(0)
        content.append(picker)
        error_label = Gtk.Label(label="")
        error_label.set_wrap(True)
        error_label.set_xalign(0.0)
        error_label.add_css_class("error")
        content.append(error_label)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        buttons.append(cancel)
        buttons.append(save)
        content.append(buttons)
        dialog.set_child(content)
        dialog.present(self)
        self._last_dialog = dialog
        state = {"anchor": anchor, "duration_min": duration_min}

        def refresh_placeholders(*_args) -> None:
            if all_day_switch.get_active():
                start_entry.set_placeholder_text("Start: YYYY-MM-DD")
                end_entry.set_placeholder_text("End: YYYY-MM-DD")
            else:
                start_entry.set_placeholder_text("Start: YYYY-MM-DD HH:MM")
                end_entry.set_placeholder_text("End: YYYY-MM-DD HH:MM")

        def on_all_day_toggled(_switch, _spec) -> None:
            refresh_placeholders()

        all_day_switch.connect("notify::active", on_all_day_toggled)
        cancel.connect("clicked", lambda _b: dialog.close())

        def on_save(_button) -> None:
            self._save_from_sheet(dialog, title_entry, all_day_switch,
                                  start_entry, end_entry, picker, calendars,
                                  error_label, state)

        save.connect("clicked", on_save)

    def _save_from_sheet(self, dialog, title_entry, all_day_switch,
                         start_entry, end_entry, picker, calendars,
                         error_label, state) -> None:
        title = title_entry.get_text()
        if not title.strip():
            error_label.set_label("Give the event a title.")
            return
        all_day = all_day_switch.get_active()
        start_ms = parse_sheet_datetime(start_entry.get_text(), all_day,
                                        self._zone)
        if start_ms is None:
            error_label.set_label(
                "Start is not a date" + ("" if all_day else " and time")
                + ".")
            return
        raw_end = end_entry.get_text().strip()
        if raw_end:
            end_ms = parse_sheet_datetime(raw_end, all_day, self._zone)
            if end_ms is None:
                error_label.set_label("End is not a date"
                                      + ("" if all_day else " and time")
                                      + ".")
                return
        else:
            if all_day:
                end_ms = start_ms + 86400000
            else:
                end_ms = start_ms + max(1, state["duration_min"]) * 60000
        if end_ms <= start_ms:
            error_label.set_label("End must be after start.")
            return
        index = picker.get_selected()
        calendar_id = calendars[index].id if 0 <= index < len(calendars) \
            else calendars[0].id
        ics_text = views.build_create_ics(
            title, all_day, start_ms, end_ms,
            reminder_minutes=self._settings.get("default_notification_min"))
        self._write_create(calendar_id, ics_text, dialog, error_label)

    def _write_create(self, calendar_id: str, ics_text: str, dialog,
                      error_label) -> None:
        if self._bus_connection is not None:
            try:
                self._bus_connection.call(
                    BUS_NAME, OBJECT_PATH, INTERFACE, "Create",
                    GLib.Variant("(ss)", (calendar_id, ics_text)),
                    GLib.VariantType.new("(s)"),
                    Gio.DBusCallFlags.NONE, -1, None,
                    self._on_create_replied, dialog)
                return
            except Exception:
                pass
        worker = threading.Thread(
            target=self._create_locally,
            args=(calendar_id, ics_text, dialog), daemon=True)
        worker.start()

    def _on_create_replied(self, connection, result, dialog) -> None:
        try:
            reply = connection.call_finish(result)
            _uid = reply.unpack()[0]
        except Exception as exc:
            self._show_sheet_error(dialog, f"Could not save: {exc}")
            return
        GLib.idle_add(self._on_create_done, dialog, True, "Saved")

    def _create_locally(self, calendar_id: str, ics_text: str,
                        dialog) -> None:
        try:
            self._service.create(calendar_id, ics_text)
        except Exception as exc:  # keep the sheet open with the reason
            GLib.idle_add(self._show_sheet_error, dialog,
                          f"Could not save: {exc}")
            return
        GLib.idle_add(self._on_create_done, dialog, True, "Saved")

    def _show_sheet_error(self, dialog, message: str) -> bool:
        error = _find_error_label(dialog.get_child())
        if error is not None:
            error.set_label(message)
        else:
            self._toasts.add_toast(Adw.Toast.new(message))
        return False

    def _on_create_done(self, dialog, _ok: bool, message: str) -> bool:
        dialog.close()
        self._toasts.add_toast(Adw.Toast.new(message))
        self._schedule.force_refresh()
        self.reload()
        return False

    # ------------------------------------------------------------ ics import

    def import_ics_uri(self, uri: str) -> None:
        path = _uri_to_path(uri)
        if path is None or not path.lower().endswith(".ics"):
            return
        calendars = self.writable_calendars()
        if not calendars:
            self._toasts.add_toast(
                Adw.Toast.new("No writable calendar yet"))
            return
        dialog = Adw.Dialog()
        dialog.set_title("Import into")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        picker = Gtk.DropDown.new_from_strings(
            [c.display_name or c.id for c in calendars])
        picker.set_selected(0)
        content.append(picker)
        error_label = Gtk.Label(label="")
        error_label.set_wrap(True)
        content.append(error_label)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        go = Gtk.Button(label="Import")
        go.add_css_class("suggested-action")
        buttons.append(cancel)
        buttons.append(go)
        content.append(buttons)
        dialog.set_child(content)
        dialog.present(self)
        self._last_dialog = dialog
        cancel.connect("clicked", lambda _b: dialog.close())

        def on_import(_button) -> None:
            index = picker.get_selected()
            calendar_id = calendars[index].id \
                if 0 <= index < len(calendars) else calendars[0].id
            worker = threading.Thread(
                target=self._import_locally,
                args=(calendar_id, path, dialog), daemon=True)
            worker.start()

        go.connect("clicked", on_import)

    def _import_locally(self, calendar_id: str, path: str,
                        dialog) -> None:
        from xx_calendar.service import read_bounded_text

        try:
            text = read_bounded_text(path)
            created, updated, skipped = self._service.import_ics(calendar_id,
                                                                 text)
        except Exception as exc:
            GLib.idle_add(self._show_sheet_error, dialog,
                          f"Could not import: {exc}")
            return
        message = (f"{created:d} imported · {updated:d} updated · "
                   f"{skipped:d} skipped")
        GLib.idle_add(self._on_create_done, dialog, True, message)

    # ----------------------------------------------------------------- theme

    def _apply_theme(self) -> bool:
        resolved = theme_from_xxwm_config(load_xxwm_config())
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if resolved.prefer_dark
            else Adw.ColorScheme.FORCE_LIGHT)
        provider = Gtk.CssProvider()
        provider.load_from_string(css_for_theme(resolved)
                                  + _extra_css(resolved.preset))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return False


def _extra_css(preset) -> str:
    return (
        ".xxc-day-header { font-weight: bold; color: %s; }\n"
        ".xxc-dow { color: %s; font-size: 11px; }\n"
        ".xxc-numeral { font-size: 12px; color: %s; }\n"
        ".xxc-inverted { background-color: %s; color: %s; }\n"
        ".dim { color: %s; }\n"
        ".error { color: %s; }\n"
        % (preset.foreground, preset.muted, preset.foreground,
           preset.foreground, preset.background, preset.muted,
           preset.foreground)
    )


def _gutter_spacer():
    spacer = Gtk.Box()
    spacer.set_size_request(26, -1)
    return spacer


def _weekday_index(name: str) -> int:
    order = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
             "SATURDAY", "SUNDAY"]
    try:
        return order.index(name.upper())
    except ValueError:
        return 0


def _tier_index(tier) -> int:
    try:
        from xx_calendar.models import SigilTier

        return list(SigilTier).index(tier)
    except (ValueError, TypeError):
        return 0


def _variant_value(variant):
    text = variant.get_type_string()
    if text == "b":
        return variant.get_boolean()
    if text == "x":
        return variant.get_int64()
    if text == "i":
        return variant.get_int32()
    if text == "s":
        return variant.get_string()
    try:
        return variant.unpack()
    except Exception:
        return None


def _find_error_label(widget):
    if widget is None:
        return None
    if isinstance(widget, Gtk.Label) and widget.has_css_class("error"):
        return widget
    child = widget.get_first_child() if hasattr(widget, "get_first_child") \
        else None
    while child is not None:
        found = _find_error_label(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _uri_to_path(uri: str) -> str | None:
    try:
        info = Gio.File.new_for_uri(uri)
        path = info.get_path()
        if path:
            return path
    except Exception:
        pass
    if os.path.isfile(uri):
        return uri
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else list(__import__("sys").argv)
    program = args[0] if args else "xx-calendar"
    launch = parse_launch_args(args[1:])
    app = CalendarApp(launch=launch)
    return app.run([program] + launch["rest"])


if __name__ == "__main__":
    raise SystemExit(main())
