from datetime import datetime, timedelta, time, date
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, AccessError, UserError
from odoo.tools.sql import column_exists, create_column
from odoo.tools.misc import format_datetime
from markupsafe import Markup
import logging
import math
import pytz

_logger = logging.getLogger(__name__)

# Process-local unforgeable sentinel proving an internal server call wrote protected context flags.
_TRUSTED_CONTEXT_TOKEN = object()
_LITHUANIAN_HOLIDAYS_CACHE = {}

_APPROVAL_LOCK_FIELDS = (
    "has_been_approved",
    "first_approved_date",
    "approved_planned_date_start",
    "approved_planned_date_end",
    "approved_baseline_duration",
)
_SCHEDULE_EDIT_FIELDS = (
    "planned_date_start",
    "planned_date_end",
    "date_deadline",
    "depend_on_ids",
    "allocated_hours",
)
_TIMER_STATE_FIELDS = (
    "is_timer_running",
    "timer_start",
    "first_timer_start",
    "timer_total_seconds",
    "is_paused",
    "active_user_id",
    "last_pause_reason",
    "last_pause_date",
)


def _parse_int_id(raw_id, prefix=None):
    """Parses a prefixed or numeric record ID, raising ValidationError on invalid input."""
    if raw_id is None or raw_id is False or isinstance(raw_id, bool):
        raise ValidationError(_("Neteisingas identifikatorius: %s") % (raw_id,))
    str_val = str(raw_id).strip()
    if prefix and str_val.startswith(prefix):
        str_val = str_val[len(prefix):]
    if not str_val:
        raise ValidationError(_("Tuščias identifikatorius: %s") % (raw_id,))
    try:
        val = int(str_val)
    except (ValueError, TypeError) as exc:
        raise ValidationError(_("Neteisingas identifikatorius: %s") % (raw_id,)) from exc
    if val <= 0:
        raise ValidationError(_("Identifikatorius turi būti teigiamas skaičius: %s") % (raw_id,))
    return val


def floor_minute(dt):
    """Drops seconds/microseconds (15:00:43 -> 15:00:00)."""
    return dt.replace(second=0, microsecond=0) if isinstance(dt, datetime) else dt


def ceil_minutes_between(start, end):
    """
    Whole minutes between two datetimes, always rounded UP and never less than 1:
    15 min 43 s -> 16 min, 15 s -> 1 min.
    """
    seconds = max(0.0, (end - start).total_seconds())
    return max(1, int(math.ceil(seconds / 60.0 - 1e-9)))


def format_duration_minutes(total_min):
    """Human duration from minutes: '16 min', '1 val. 5 min', '2 d. 3 val.'"""
    total_min = int(max(0, round(total_min)))
    if total_min < 60:
        return f"{total_min} min"
    days, rem = divmod(total_min, 1440)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} d.")
    if hours:
        parts.append(f"{hours} val.")
    if mins and not days:
        parts.append(f"{mins} min")
    return " ".join(parts) or "0 min"


def format_hours_label(hours):
    """Compact label for allocated hours: '16 min', '8h', '1h 30min' (instead of '0.1h')."""
    if not hours or hours <= 0:
        return ""
    total_min = max(1, int(round(hours * 60)))
    if total_min < 60:
        return f"{total_min} min"
    h, m = divmod(total_min, 60)
    return f"{h}h" if m == 0 else f"{h}h {m}min"


def get_user_tz(env):
    """Returns the user's active pytz timezone (defaults to Europe/Vilnius)."""
    tz_name = env.user.tz or env.context.get("tz") or "Europe/Vilnius"
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("Europe/Vilnius")


def utc_to_local_dt(env, dt, user_tz=None):
    """
    Converts a naive UTC datetime (stored in Odoo PostgreSQL) into a naive datetime
    in the user's active timezone (e.g. Europe/Vilnius), so dates match the Odoo Form View 100%.
    """
    if not dt:
        return dt
    if not isinstance(dt, datetime):
        return dt
    tz = user_tz or get_user_tz(env)
    utc_dt = pytz.utc.localize(dt) if dt.tzinfo is None else dt.astimezone(pytz.utc)
    return utc_dt.astimezone(tz).replace(tzinfo=None)


def local_to_utc_dt(env, dt, user_tz=None):
    """
    Converts a naive datetime in the user's local timezone (from Timeline UI)
    to a naive UTC datetime for Odoo PostgreSQL storage, so the Odoo Form View displays the exact same date & time.
    """
    if not dt:
        return dt
    if not isinstance(dt, datetime):
        return dt
    tz = user_tz or get_user_tz(env)
    if dt.tzinfo is None:
        try:
            local_dt = tz.localize(dt, is_dst=None)
        except pytz.AmbiguousTimeError:
            local_dt = tz.localize(dt, is_dst=False)
        except pytz.NonExistentTimeError:
            local_dt = tz.localize(dt + timedelta(hours=1), is_dst=True)
    else:
        local_dt = dt.astimezone(tz)
    return local_dt.astimezone(pytz.utc).replace(tzinfo=None)


def format_user_datetime(env, dt, dt_format="yyyy-MM-dd HH:mm:ss"):
    """
    Formats a UTC datetime into a localized string matching the user's active timezone.
    Falls back to 'Europe/Vilnius' or UTC if no user timezone is set.
    """
    if not dt:
        return ""
    tz = env.user.tz or env.context.get("tz") or "Europe/Vilnius"
    try:
        return format_datetime(env, dt, tz=tz, dt_format=dt_format)
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def _get_cached_lithuanian_holidays(year):
    """Returns cached frozenset of Lithuanian public holiday date objects for the given year."""
    cached = _LITHUANIAN_HOLIDAYS_CACHE.get(year)
    if cached is not None:
        return cached
    fixed = {
        (1, 1),    # Naujieji metai
        (2, 16),   # Lietuvos valstybės atkūrimo diena
        (3, 11),   # Lietuvos nepriklausomybės atkūrimo diena
        (5, 1),    # Tarptautinė darbo diena
        (6, 24),   # Rasos ir Joninių diena
        (7, 6),    # Valstybės diena
        (8, 15),   # Žolinė
        (11, 1),   # Visų Šventųjų diena
        (11, 2),   # Vėlinių diena
        (12, 24),  # Kūčių diena
        (12, 25),  # Kalėdų pirmoji diena
        (12, 26),  # Kalėdų antroji diena
    }
    # Movable Easter (Gregorian Computus)
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    easter_month = (h + l - 7 * m + 114) // 31
    easter_day = ((h + l - 7 * m + 114) % 31) + 1
    easter_sunday = date(year, easter_month, easter_day)
    easter_monday = easter_sunday + timedelta(days=1)

    holidays = {date(year, mo, d) for (mo, d) in fixed}
    holidays.add(easter_sunday)
    holidays.add(easter_monday)
    frozen = frozenset(holidays)
    _LITHUANIAN_HOLIDAYS_CACHE[year] = frozen
    return frozen


def get_lithuanian_holidays(year):
    """
    Returns a set of date objects representing Lithuanian national public holidays for the given year.
    Covers all fixed annual holidays plus movable Easter Sunday and Easter Monday.
    """
    return set(_get_cached_lithuanian_holidays(year))


def calculate_lithuanian_working_hours(start_dt, end_dt, holidays_cache=None):
    """
    Calculates actual working hours excluding weekends (Saturday, Sunday)
    and Lithuanian national public holidays.
    - If dates represent standard full work days (08:00 - 17:00 or date-only boundaries),
      counts 8 hours per full working day.
    - If dates have specific intra-day hours (e.g. 10:00 - 14:00),
      calculates the actual working hours within business working window (08:00 - 17:00).
    """
    if not start_dt or not end_dt:
        return 0.0

    if isinstance(start_dt, str):
        s_str = start_dt.strip()
        start_dt = fields.Date.to_date(s_str) if len(s_str) <= 10 else fields.Datetime.to_datetime(s_str)
    if isinstance(end_dt, str):
        e_str = end_dt.strip()
        end_dt = fields.Date.to_date(e_str) if len(e_str) <= 10 else fields.Datetime.to_datetime(e_str)

    if not start_dt or not end_dt:
        return 0.0

    if isinstance(start_dt, datetime) and isinstance(end_dt, datetime) and start_dt == end_dt:
        return 0.0

    s_dt = start_dt if isinstance(start_dt, datetime) else datetime.combine(start_dt, time(8, 0, 0))
    if isinstance(end_dt, datetime):
        if (end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0) or (end_dt.hour == 23 and end_dt.minute == 59):
            e_dt = datetime.combine(end_dt.date(), time(17, 0, 0))
        else:
            e_dt = end_dt
    else:
        e_dt = datetime.combine(end_dt, time(17, 0, 0))

    if e_dt <= s_dt:
        return 0.0

    holidays = set()
    for yr in range(s_dt.year, e_dt.year + 1):
        if holidays_cache is not None:
            if yr not in holidays_cache:
                holidays_cache[yr] = _get_cached_lithuanian_holidays(yr)
            holidays.update(holidays_cache[yr])
        else:
            holidays.update(_get_cached_lithuanian_holidays(yr))

    total_hours = 0.0
    curr_date = s_dt.date()

    while curr_date <= e_dt.date():
        if curr_date.weekday() < 5 and curr_date not in holidays:
            day_work_start = datetime.combine(curr_date, time(8, 0, 0))
            day_work_end = datetime.combine(curr_date, time(17, 0, 0))
            lunch_start = datetime.combine(curr_date, time(12, 45, 0))
            lunch_end = datetime.combine(curr_date, time(13, 45, 0))

            act_start = max(s_dt, day_work_start)
            act_end = min(e_dt, day_work_end)

            if act_end > act_start:
                gross_sec = (act_end - act_start).total_seconds()
                l_start = max(act_start, lunch_start)
                l_end = min(act_end, lunch_end)
                lunch_sec = (l_end - l_start).total_seconds() if l_end > l_start else 0.0
                net_sec = max(0.0, gross_sec - lunch_sec)
                day_hours = min(8.0, round(net_sec / 3600.0, 2))
                total_hours += day_hours

        curr_date += timedelta(days=1)

    return float(round(total_hours, 2))


class ProjectTask(models.Model):
    _inherit = "project.task"

    planned_date_start = fields.Datetime(
        string="Planuota pradžia / Planned Start Date",
        compute="_compute_planned_date_start",
        store=True,
        readonly=False,
        precompute=True,
        recursive=True,
        copy=False,
        index=True,
    )
    planned_date_end = fields.Datetime(
        string="Planuota pabaiga / Planned End Date",
        compute="_compute_planned_date_end",
        store=True,
        readonly=False,
        precompute=True,
        recursive=True,
        copy=False,
        index=True,
    )
    plan_is_auto = fields.Boolean(
        string="Automatinis planas / Auto plan",
        copy=False,
        default=False,
        index=True,
        help="Planned dates were derived by the system (timer actual work, or the span of subtasks) "
             "instead of being typed by a user. Such a plan follows the real work and never shows a red tail.",
    )
    deadline_is_auto = fields.Boolean(
        string="Automatinis terminas / Auto deadline",
        copy=False,
        default=False,
        index=True,
        help="The deadline was written by the system because work went past the planned end. "
             "The system may update or clear it; a user typed deadline is never erased.",
    )
    actual_finish_date = fields.Datetime(
        string="Faktinė pabaigos data / Actual Finish Date",
        copy=False,
        index=True,
    )

    # --- Post-Approval Immutability & Baseline Tracking ---
    has_been_approved = fields.Boolean(
        string="Buvęs patvirtintas / Has Been Approved",
        default=False,
        readonly=True,
        copy=False,
        index=True,
        help="Indicates the task has reached approved status at least once. Its planned schedule and minimum duration are locked."
    )
    first_approved_date = fields.Datetime("Pirmo patvirtinimo data / First Approved Date", readonly=True, copy=False)
    approved_planned_date_start = fields.Datetime("Patvirtinta pradžia / Approved Start", readonly=True, copy=False)
    approved_planned_date_end = fields.Datetime("Patvirtinta pabaiga / Approved End", readonly=True, copy=False)
    approved_baseline_duration = fields.Float("Patvirtinta bazinė trukmė (val.)", readonly=True, copy=False)
    can_edit_gannt_dates = fields.Boolean(
        compute="_compute_can_edit_gannt_dates",
        string="Can Edit GanntLT Dates",
    )

    @api.depends_context("uid")
    def _compute_can_edit_gannt_dates(self):
        can_edit = self._has_edit_rights()
        for rec in self:
            rec.can_edit_gannt_dates = can_edit

    # --- Exclusive Multi-User Concurrency Lock & Paused State ---
    active_user_id = fields.Many2one(
        "res.users",
        string="Aktyvus vykdytojas / Active Worker",
        readonly=True,
        copy=False,
        index=True,
        help="The user currently running the work timer on this task. Locks the task for other users."
    )
    is_paused = fields.Boolean(
        string="Pristabdyta / Is Paused",
        default=False,
        readonly=True,
        copy=False,
        index=True,
        help="True if the task timer was paused via the pause wizard."
    )
    last_pause_reason = fields.Char("Paskutinė stabdymo priežastis / Last Pause Reason", readonly=True, copy=False)
    last_pause_date = fields.Datetime("Paskutinio pristabdymo laikas", readonly=True, copy=False)

    @api.depends("parent_id", "parent_id.planned_date_start")
    def _compute_planned_date_start(self):
        """Default planned_date_start to parent task if not already set."""
        for record in self.filtered(lambda x: not x.planned_date_start):
            if record.parent_id and record.parent_id.planned_date_start:
                record.planned_date_start = record.parent_id.planned_date_start

    @api.depends("parent_id", "parent_id.planned_date_end")
    def _compute_planned_date_end(self):
        """Default planned_date_end to parent task if not already set."""
        for record in self.filtered(lambda x: not x.planned_date_end):
            if record.parent_id and record.parent_id.planned_date_end:
                record.planned_date_end = record.parent_id.planned_date_end

    @api.constrains("planned_date_start", "planned_date_end")
    def _check_planned_dates(self):
        for task in self:
            if task.planned_date_start and task.planned_date_end:
                if task.planned_date_end < task.planned_date_start:
                    raise ValidationError(
                        self.env._("Pabaigos data negali būti ankstesnė už pradžios datą. (The end date must be after the start date.)")
                    )

    def _is_trusted_ctx(self):
        """Returns True only for internal server calls carrying _TRUSTED_CONTEXT_TOKEN or su=True."""
        return bool(self.env.su or self.env.context.get("_gannt_lt_trusted_token") is _TRUSTED_CONTEXT_TOKEN)

    def _has_view_rights(self):
        """True only if user holds GanntLT: Can View (or higher) or internal superuser."""
        return bool(
            self.env.is_superuser()
            or self.env.user.has_group("GanntLT.group_gannt_lt_user")
        )

    def _has_edit_rights(self):
        """True only if user holds GanntLT: Can Edit (or internal superuser)."""
        return bool(
            self.env.is_superuser()
            or self.env.user.has_group("GanntLT.group_gannt_lt_manager")
        )

    def _has_timer_override_rights(self):
        """True only if user holds GanntLT: Can Edit AND Project Manager/System rights (or superuser)."""
        return bool(
            self.env.is_superuser()
            or (
                self.env.user.has_group("GanntLT.group_gannt_lt_manager")
                and (
                    self.env.user.has_group("project.group_project_manager")
                    or self.env.user.has_group("base.group_system")
                )
            )
        )

    def _has_timer_rights(self):
        """True if user can run/manage GanntLT task timers."""
        return bool(
            self._has_edit_rights()
            or self.env.user.has_group("GanntLT.group_gannt_lt_worker")
        )

    @api.model
    def _enable_timeline_view_modes(self):
        """Safely injects 'timeline' into project task actions without clobbering third-party view modes."""
        action_xml_ids = [
            "project.action_view_task",
            "project.project_task_action_from_partner",
            "project.act_project_project_2_project_task_all",
            "project.action_view_all_task",
        ]
        for xml_id in action_xml_ids:
            action = self.env.ref(xml_id, raise_if_not_found=False)
            if not action or not action.view_mode:
                continue
            modes = [m.strip() for m in action.view_mode.split(",") if m.strip()]
            if "timeline" not in modes:
                if "calendar" in modes:
                    modes.insert(modes.index("calendar") + 1, "timeline")
                else:
                    modes.append("timeline")
                action.write({"view_mode": ",".join(modes)})

    def _check_gannt_lt_edit_access(self):
        """
        Verifies that the current user has 'GanntLT: Can Edit' (group_gannt_lt_manager),
        Project Manager, or System Admin rights.
        Users with 'Can View' or 'Can Track Time' can view the timeline but cannot modify schedules.
        """
        if self._has_edit_rights():
            return True
        raise AccessError(
            _("Jūs turite tik peržiūros teises („Can View“ / „Can Track Time“). Laiko juostos redagavimas leidžiamas tik vartotojams, turintiems „Can Edit“ teises.")
        )

    def _check_gannt_lt_timer_access(self):
        """
        Verifies that the current user has 'GanntLT: Can Track Time' (group_gannt_lt_worker),
        'GanntLT: Can Edit' (group_gannt_lt_manager), Project Manager, or System Admin rights.
        Users with 'Can View' (group_gannt_lt_user) cannot start or manage the work timer.
        """
        if self._has_timer_rights():
            return True
        raise AccessError(
            _("Jūs turite tik „GanntLT: Can View“ (peržiūros) teises. Darbo laiko fiksavimas (laikmatis) leidžiamas tik vartotojams, turintiems „Can Track Time“ arba „Can Edit“ teises.")
        )

    def _auto_init(self):
        cr = self.env.cr
        first_install = not column_exists(cr, "project_task", "planned_date_end")
        if not column_exists(cr, "project_task", "planned_date_start"):
            create_column(cr, "project_task", "planned_date_start", "timestamp")
            cr.execute("""
                UPDATE project_task
                SET planned_date_start = COALESCE(date_assign, create_date)
                WHERE planned_date_start IS NULL
            """)
        if not column_exists(cr, "project_task", "planned_date_end"):
            create_column(cr, "project_task", "planned_date_end", "timestamp")
            cr.execute("""
                UPDATE project_task
                SET planned_date_end = COALESCE(date_end, date_deadline)
                WHERE planned_date_end IS NULL
                AND COALESCE(planned_date_start, date_end, date_deadline) <= COALESCE(date_end, date_deadline)
            """)
        if first_install:
            # One-time initial sync of parent task spans to their subtasks. Must NOT run on every
            # upgrade, otherwise it would overwrite parent dates that users set deliberately.
            cr.execute("""
                UPDATE project_task p
                SET planned_date_end = sub.max_end,
                    planned_date_start = sub.min_start
                FROM (
                    SELECT parent_id,
                           MIN(planned_date_start) AS min_start,
                           MAX(planned_date_end) AS max_end
                    FROM project_task
                    WHERE parent_id IS NOT NULL AND active = TRUE
                      AND planned_date_start IS NOT NULL AND planned_date_end IS NOT NULL
                    GROUP BY parent_id
                ) sub
                WHERE p.id = sub.parent_id
                  AND (p.planned_date_end IS DISTINCT FROM sub.max_end OR p.planned_date_start IS DISTINCT FROM sub.min_start)
            """)
        # NOTE: date_deadline is user data and must never be wiped automatically.
        flags_new = not column_exists(cr, "project_task", "plan_is_auto")
        res = super()._auto_init()
        if flags_new:
            # One-time: parents that were previously synced to the exact span of their subtasks are 'auto plan'.
            cr.execute("""
                UPDATE project_task p
                SET plan_is_auto = TRUE
                FROM (
                    SELECT parent_id, MIN(planned_date_start) AS min_start, MAX(planned_date_end) AS max_end
                    FROM project_task
                    WHERE parent_id IS NOT NULL AND active = TRUE
                      AND planned_date_start IS NOT NULL AND planned_date_end IS NOT NULL
                    GROUP BY parent_id
                ) sub
                WHERE p.id = sub.parent_id
                  AND p.planned_date_start = sub.min_start
                  AND p.planned_date_end = sub.max_end
            """)
        # Gate optional data cleanups behind lightweight existence probes so normal restarts skip full-table UPDATEs
        cr.execute("""
            SELECT 1
            FROM project_task c
            JOIN project_task p ON c.parent_id = p.id
            WHERE c.active = TRUE
              AND c.planned_date_start IS NULL
              AND c.planned_date_end IS NULL
              AND p.planned_date_start IS NOT NULL
              AND p.planned_date_end IS NOT NULL
            LIMIT 1
        """)
        if cr.fetchone():
            cr.execute("""
                UPDATE project_task c
                SET planned_date_start = p.planned_date_start,
                    planned_date_end = p.planned_date_end,
                    plan_is_auto = FALSE
                FROM project_task p
                WHERE c.parent_id = p.id
                  AND c.active = TRUE
                  AND c.planned_date_start IS NULL
                  AND c.planned_date_end IS NULL
                  AND p.planned_date_start IS NOT NULL
                  AND p.planned_date_end IS NOT NULL;
            """)

        cr.execute("""
            SELECT 1
            FROM project_task
            WHERE deadline_is_auto = TRUE
              AND (first_timer_start IS NULL)
              AND (state IS NULL OR state NOT IN ('03_approved', '1_done', '02_changes_requested'))
              AND planned_date_end IS NOT NULL
              AND (
                NOT EXISTS (
                  SELECT 1 FROM project_task child
                  WHERE child.parent_id = project_task.id
                    AND child.active = TRUE
                    AND COALESCE(child.date_deadline, child.planned_date_end) > project_task.planned_date_end
                )
              )
            LIMIT 1
        """)
        if cr.fetchone():
            cr.execute("""
                UPDATE project_task
                SET date_deadline = NULL,
                    deadline_is_auto = FALSE
                WHERE deadline_is_auto = TRUE
                  AND (first_timer_start IS NULL)
                  AND (state IS NULL OR state NOT IN ('03_approved', '1_done', '02_changes_requested'))
                  AND planned_date_end IS NOT NULL
                  AND (
                    NOT EXISTS (
                      SELECT 1 FROM project_task child
                      WHERE child.parent_id = project_task.id
                        AND child.active = TRUE
                        AND COALESCE(child.date_deadline, child.planned_date_end) > project_task.planned_date_end
                    )
                  );
            """)
        if not column_exists(cr, "project_task", "actual_finish_date"):
            create_column(cr, "project_task", "actual_finish_date", "timestamp")
            cr.execute("""
                UPDATE project_task
                SET actual_finish_date = date_end
                WHERE state IN ('03_approved', '1_done') AND actual_finish_date IS NULL AND date_end IS NOT NULL
            """)
        if not column_exists(cr, "project_task", "has_been_approved"):
            create_column(cr, "project_task", "has_been_approved", "boolean")
            create_column(cr, "project_task", "first_approved_date", "timestamp")
            create_column(cr, "project_task", "approved_planned_date_start", "timestamp")
            create_column(cr, "project_task", "approved_planned_date_end", "timestamp")
            create_column(cr, "project_task", "approved_baseline_duration", "float8")
            cr.execute("""
                UPDATE project_task
                SET has_been_approved = TRUE,
                    first_approved_date = COALESCE(actual_finish_date, date_end, write_date),
                    approved_planned_date_start = planned_date_start,
                    approved_planned_date_end = planned_date_end,
                    approved_baseline_duration = COALESCE(allocated_hours, 0.0)
                WHERE state IN ('03_approved', '1_done')
            """)
        if not column_exists(cr, "project_task", "active_user_id"):
            create_column(cr, "project_task", "active_user_id", "int4")
        if not column_exists(cr, "project_task", "is_paused"):
            create_column(cr, "project_task", "is_paused", "boolean")
        if not column_exists(cr, "project_task", "last_pause_reason"):
            create_column(cr, "project_task", "last_pause_reason", "varchar")
        if not column_exists(cr, "project_task", "last_pause_date"):
            create_column(cr, "project_task", "last_pause_date", "timestamp")
        return res

    timer_start = fields.Datetime("Laikmačio pradžia / Timer Start", readonly=True, copy=False)
    first_timer_start = fields.Datetime("Pirmo starto laikas / First Timer Start", readonly=True, copy=False)
    is_timer_running = fields.Boolean("Laikmatis veikia / Timer Running", default=False, readonly=True, copy=False)
    timer_total_seconds = fields.Integer("Laikmačio sekundės / Timer Total Seconds", default=0, readonly=True, copy=False)
    timer_display_seconds = fields.Integer("Rodyti sekundes / Display Seconds", compute="_compute_timer_display_seconds")

    @api.depends("timer_start", "is_timer_running", "timer_total_seconds", "effective_hours")
    def _compute_timer_display_seconds(self):
        now = fields.Datetime.now()
        for task in self:
            eff_sec = int(round((task.effective_hours or 0.0) * 3600))
            base_sec = max(task.timer_total_seconds or 0, eff_sec)
            if task.is_timer_running and task.timer_start:
                diff_sec = max(0, int((now - task.timer_start).total_seconds()))
                task.timer_display_seconds = base_sec + diff_sec
            else:
                task.timer_display_seconds = base_sec

    def _ensure_inherited_plan(self):
        """
        Ensures any subtask/sub-subtask without its own planned dates inherits from its nearest
        scheduled ancestor with plan_is_auto=False so work timer / completion compares against
        the inherited schedule and creates a red tail on overrun.
        """
        for rec in self:
            if not rec.parent_id:
                continue
            if not rec.planned_date_start or not rec.planned_date_end:
                visited = {rec.id}
                anc = rec.parent_id
                while anc and anc.id not in visited:
                    visited.add(anc.id)
                    if anc.planned_date_start and anc.planned_date_end:
                        rec.check_access("write")
                        rec.with_context(
                            skip_hierarchy_propagation=True,
                            gannt_auto_plan=True,
                            mail_notrack=True,
                            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                        ).write({
                            "planned_date_start": anc.planned_date_start,
                            "planned_date_end": anc.planned_date_end,
                            "plan_is_auto": False,
                        })
                        break
                    anc = anc.parent_id

    def action_timer_start(self):
        self.ensure_one()
        self._check_gannt_lt_timer_access()
        if self.state in ("03_approved", "1_done", "1_canceled"):
            return False

        if self.active_user_id and self.active_user_id != self.env.user:
            can_manage = self._has_timer_override_rights()
            if self.is_timer_running and not can_manage:
                raise UserError(
                    _("Užduotį šiuo metu vykdo %s. Kitas vartotojas negali pradėti tos pačios užduoties.")
                    % self.active_user_id.name
                )
            if self.is_paused and not can_manage:
                raise UserError(
                    _("Užduotis yra pristabdyta vartotojo %s. Kitas vartotojas negali tęsti šios užduoties.")
                    % self.active_user_id.name
                )

        if self.is_timer_running:
            return False

        self._ensure_inherited_plan()
        now = fields.Datetime.now()
        eff_sec = int(round((self.effective_hours or 0.0) * 3600))
        vals = {
            "timer_start": now,
            "is_timer_running": True,
            "active_user_id": self.env.uid,
            "is_paused": False,
        }
        # Record first start time of work if not already recorded
        if not self.first_timer_start:
            vals["first_timer_start"] = now

        # Keep timer_total_seconds synced with timesheets if uninitialized
        if (not self.timer_total_seconds or self.timer_total_seconds < eff_sec) and eff_sec > 0:
            vals["timer_total_seconds"] = eff_sec

        # R1: no established plan -> the plan is derived from the real work (start = first timer start).
        # An established plan (typed by a user or inherited from a scheduled parent) is NEVER moved by the timer.
        has_established_plan = bool(self.planned_date_start and self.planned_date_end) and (
            not self.plan_is_auto or bool(self.child_ids)
        )
        if not has_established_plan:
            start_f = floor_minute(vals.get("first_timer_start") or self.first_timer_start or now)
            vals["planned_date_start"] = start_f
            vals["plan_is_auto"] = True
            if self.planned_date_end and self.planned_date_end < start_f:
                vals["planned_date_end"] = False  # stale end of an old auto plan; rewritten on Pause/Finish

        if self.state in ("02_changes_requested", "04_waiting_normal", "draft"):
            vals["state"] = "01_in_progress"
        self.with_context(
            gannt_auto_plan=True,
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        ).write(vals)
        self.with_context(
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        )._propagate_deadline_upward()

        user_name = self.env.user.name
        time_str = format_user_datetime(self.env, now, "yyyy.MM.dd -HH:mm")
        self.message_post(
            body=Markup("%s Pradėta %s") % (user_name, time_str),
            subtype_xmlid="mail.mt_note",
        )
        return True

    def action_timer_pause(self):
        self.ensure_one()
        self._check_gannt_lt_timer_access()
        if self.active_user_id and self.active_user_id != self.env.user:
            can_manage = self._has_timer_override_rights()
            if not can_manage:
                raise UserError(
                    _("Pristabdyti užduotį gali tik ją vykdantis vartotojas (%s) arba projekto vadovas.")
                    % self.active_user_id.name
                )
        return {
            "name": _("Pristabdyti užduotį / Pause Task"),
            "type": "ir.actions.act_window",
            "res_model": "project.task.pause.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_task_id": self.id,
                "default_timer_start": self.timer_start or fields.Datetime.now(),
            },
        }

    def action_timer_finish(self, force_now=None):
        self.ensure_one()
        self._check_gannt_lt_timer_access()
        if self.active_user_id and self.active_user_id != self.env.user:
            can_manage = self._has_timer_override_rights()
            if not can_manage:
                raise UserError(
                    _("Užbaigti užduotį gali tik ją vykdantis vartotojas (%s) arba projekto vadovas.")
                    % self.active_user_id.name
                )
        if self.child_ids:
            unfinished_desc = self.env["project.task"].sudo().search([
                ("id", "child_of", self.id),
                ("id", "!=", self.id),
                ("active", "=", True),
                ("state", "not in", ["03_approved", "1_done", "1_canceled"]),
            ], limit=1)
            if unfinished_desc:
                raise ValidationError(
                    _("Tėvinė užduotis „%s“ negali būti užbaigta („Baigti“), kol visos jos po-užduotys nėra užbaigtos (pvz., „%s“).")
                    % (self.name, unfinished_desc.name)
                )
        if self.state == "1_canceled":
            return False
        if self.state in ("03_approved", "1_done") and not self.is_timer_running:
            return True
        now = force_now or fields.Datetime.now()
        user_name = self.env.user.name

        eff_sec = int(round((self.effective_hours or 0.0) * 3600))
        cur_total_sec = max(self.timer_total_seconds or 0, eff_sec)

        # 1. If timer was running, register the elapsed time to Timesheets and accumulate seconds
        if self.is_timer_running and self.timer_start:
            diff_sec = max(0, int((now - self.timer_start).total_seconds()))
            cur_total_sec += diff_sec
            elapsed_min = max(1, int(math.ceil(diff_sec / 60.0 - 1e-9)))
            elapsed_hours = elapsed_min / 60.0
            active_worker = self.active_user_id or self.env.user
            emp = False
            if active_worker == self.env.user:
                try:
                    emp = active_worker.employee_id
                except AccessError:
                    emp = False
            if not emp and "hr.employee" in self.env:
                emp_domain = [("user_id", "=", active_worker.id)]
                if self.company_id:
                    emp_domain.append(("company_id", "in", [False, self.company_id.id]))
                emp = self.env["hr.employee"].sudo().search(emp_domain, limit=1)

            company = self.company_id or (emp and emp.company_id) or self.env.company
            if self.project_id and getattr(self.project_id, "allow_timesheets", True) and "account.analytic.line" in self.env and elapsed_hours > 0 and emp:
                self.env["account.analytic.line"].with_context(mail_notrack=True).create({
                    "name": _("Darbas baigtas (Finish)"),
                    "task_id": self.id,
                    "project_id": self.project_id.id,
                    "employee_id": emp.id,
                    "user_id": active_worker.id,
                    "company_id": company.id if company else False,
                    "date": fields.Date.context_today(self),
                    "unit_amount": elapsed_hours,
                })

        vals = {
            "is_timer_running": False,
            "timer_start": False,
            "timer_total_seconds": cur_total_sec,
            "state": "03_approved",
            "active_user_id": False,
            "is_paused": False,
        }

        # 2. Plan / deadline logic (R1 / R2), see _work_stop_vals
        stop_vals, _info = self._work_stop_vals(now, cur_total_sec, finished=True)
        vals.update(stop_vals)

        # 3. Snapshot approved baseline if first time reaching approved status
        if not self.has_been_approved:
            s_dt = vals.get("planned_date_start") or self.planned_date_start
            e_dt = vals.get("planned_date_end") or self.planned_date_end
            s_conv = fields.Datetime.to_datetime(s_dt) if isinstance(s_dt, str) else s_dt
            e_conv = fields.Datetime.to_datetime(e_dt) if isinstance(e_dt, str) else e_dt
            dur = 0.0
            if s_conv and e_conv:
                dur = calculate_lithuanian_working_hours(
                    utc_to_local_dt(self.env, s_conv),
                    utc_to_local_dt(self.env, e_conv),
                )
                if dur <= 0:
                    alloc_val = float(vals.get("allocated_hours") or self.allocated_hours or 0.0)
                    dur = alloc_val if alloc_val > 0 else min(8.0, max(0.01, round((e_conv - s_conv).total_seconds() / 3600.0, 2)))
            alloc_h = float(vals.get("allocated_hours") or self.allocated_hours or 0.0)
            if alloc_h > dur:
                dur = alloc_h
            vals.update({
                "has_been_approved": True,
                "first_approved_date": now,
                "approved_planned_date_start": s_conv,
                "approved_planned_date_end": e_conv,
                "approved_baseline_duration": dur,
            })

        self.with_context(
            gannt_auto_plan=True,
            from_timer_finish=True,
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        ).write(vals)
        self.with_context(
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        )._propagate_deadline_upward()

        # 4. Post single clean finish note to Chatter
        time_str = format_user_datetime(self.env, now, "yyyy.MM.dd -HH:mm")
        self.message_post(
            body=Markup("%s Baigta (Laukia patvirtinimo) %s") % (user_name, time_str),
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _work_stop_vals(self, now, total_sec, finished):
        """
        Single place that decides how the plan / deadline change when work stops (Pause, Finish, or Status -> Approved/Done).

        R1  no ESTABLISHED plan -> the plan is derived automatically:
            start = first timer start (or task creation time create_date if timer was not used),
            end = start + whole minutes up to now (always rounded UP, min 1 min). Never a red tail.
            If reopened via Changes Requested and finished again later, end updates to the new current time.
        R2  established plan (manual or inherited from a scheduled parent) -> the plan is untouched.
            If completed past the planned end, deadline = planned end + overrun minutes (rounded UP, min 1 min)
            flagged auto; the timeline draws the red tail planned end -> deadline.

        Returns (vals, info).
        """
        self.ensure_one()
        self._ensure_inherited_plan()
        vals = {}
        info = {"mode": "auto", "late_min": 0, "early_min": 0, "plan_minutes": 0, "planned_end": self.planned_date_end,
                "extended_manual": False, "old_deadline": self.date_deadline}
        has_established_plan = bool(self.planned_date_start and self.planned_date_end) and (
            not self.plan_is_auto or bool(self.child_ids)
        )

        if not has_established_plan:
            start = self.first_timer_start or self.planned_date_start or self.create_date or now
            if start > now:
                start = now
            start_f = floor_minute(start)
            minutes = ceil_minutes_between(start, now)
            vals["planned_date_start"] = start_f
            vals["planned_date_end"] = start_f + timedelta(minutes=minutes)
            vals["plan_is_auto"] = True
            info["plan_minutes"] = minutes
            if finished:
                vals["actual_finish_date"] = now
                vals["date_end"] = now
                if total_sec and total_sec > 0:
                    vals["allocated_hours"] = max(1, int(math.ceil(total_sec / 60.0 - 1e-9))) / 60.0
                else:
                    user_tz = get_user_tz(self.env)
                    w_h = calculate_lithuanian_working_hours(
                        utc_to_local_dt(self.env, start_f, user_tz=user_tz),
                        utc_to_local_dt(self.env, vals["planned_date_end"], user_tz=user_tz),
                    )
                    vals["allocated_hours"] = w_h if w_h > 0 else round(minutes / 60.0, 2)
            if self.deadline_is_auto and self.date_deadline:
                vals["date_deadline"] = False
                vals["deadline_is_auto"] = False
            return vals, info

        info["mode"] = "planned"
        p_end = self.planned_date_end
        if finished:
            vals["actual_finish_date"] = now
            vals["date_end"] = now
            if p_end:
                if now < p_end:
                    info["early_min"] = ceil_minutes_between(now, p_end)
                    if self.deadline_is_auto and self.date_deadline:
                        vals["date_deadline"] = False
                        vals["deadline_is_auto"] = False
                elif now > p_end:
                    info["late_min"] = ceil_minutes_between(p_end, now)
                    vals["date_deadline"] = now
                    vals["deadline_is_auto"] = True
                else:
                    if self.deadline_is_auto and self.date_deadline:
                        vals["date_deadline"] = False
                        vals["deadline_is_auto"] = False
        else:
            if p_end and now > p_end:
                over = ceil_minutes_between(p_end, now)
                new_dl = p_end + timedelta(minutes=over)
                cur_dl = self.date_deadline
                info["late_min"] = over
                if not cur_dl or self.deadline_is_auto or cur_dl < new_dl:
                    vals["date_deadline"] = new_dl
                    vals["deadline_is_auto"] = True
                    info["extended_manual"] = bool(cur_dl and not self.deadline_is_auto and cur_dl < new_dl)
            elif self.deadline_is_auto and self.date_deadline and not self.child_ids:
                vals["date_deadline"] = False
                vals["deadline_is_auto"] = False
        return vals, info

    def action_timer_reopen(self):
        self.ensure_one()
        self._check_gannt_lt_timer_access()
        if self.active_user_id and self.active_user_id != self.env.user:
            can_manage = self._has_timer_override_rights()
            if (self.is_timer_running or self.is_paused) and not can_manage:
                raise UserError(
                    _("Atnaujinti užduotį gali tik ją vykdantis vartotojas (%s) arba projekto vadovas.")
                    % self.active_user_id.name
                )
        now = fields.Datetime.now()
        eff_sec = int(round((self.effective_hours or 0.0) * 3600))
        cur_total_sec = max(self.timer_total_seconds or 0, eff_sec)
        reopen_vals = {
            "state": "01_in_progress",
            "is_timer_running": False,
            "timer_start": False,
            "timer_total_seconds": cur_total_sec,
            "actual_finish_date": False,
            "date_end": False,
            "active_user_id": False,
            "is_paused": False,
        }
        if self.deadline_is_auto:
            reopen_vals["date_deadline"] = False
            reopen_vals["deadline_is_auto"] = False
        self.with_context(
            skip_hierarchy_propagation=True,
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        ).write(reopen_vals)
        self._cascade_clear_downstream_progress()
        visited = {self.id}
        anc = self.parent_id
        while anc and anc.id not in visited:
            visited.add(anc.id)
            if anc.state in ("03_approved", "1_done") or anc.actual_finish_date or anc.date_end:
                anc_vals = {
                    "state": "01_in_progress",
                    "actual_finish_date": False,
                    "date_end": False,
                }
                if anc.deadline_is_auto:
                    anc_vals["date_deadline"] = False
                    anc_vals["deadline_is_auto"] = False
                anc.with_context(
                    skip_hierarchy_propagation=True,
                    gannt_auto_plan=True,
                    mail_notrack=True,
                    _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                ).sudo().write(anc_vals)
            anc = anc.parent_id
        self.with_context(
            mail_notrack=True,
            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
        )._propagate_deadline_upward()
        time_str = format_user_datetime(self.env, now, "yyyy.MM.dd -HH:mm")
        self.message_post(
            body=Markup("%s Atnaujinta %s") % (self.env.user.name, time_str),
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _cascade_clear_downstream_progress(self, visited=None):
        """
        When a prerequisite task is reopened / no longer finished,
        any downstream dependent tasks (dependent_ids via finish-to-start links)
        that were completed should be reset to waiting (state = '04_waiting_normal').
        Never resets child subtasks (child_ids) of a parent container.
        """
        if visited is None:
            visited = set()
        for task in self:
            if task.id in visited:
                continue
            visited.add(task.id)
            for dep in task.dependent_ids:
                if dep.id in visited:
                    continue
                if dep.state in ("1_done", "03_approved"):
                    dep.with_context(
                        mail_notrack=True,
                        _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                    ).sudo().write({"state": "04_waiting_normal"})
                    dep._cascade_clear_downstream_progress(visited=visited)

    def _recompute_container(self):
        """
        R3. Keeps one parent task consistent with its direct subtasks:
          * parent with an AUTO plan (or no plan): plan = span of the subtasks (never a red tail by itself);
          * parent with a MANUAL plan: the plan is never touched. If a subtask needs more time than the parent,
            the parent gets an auto deadline = latest subtask end (planned end or deadline) -> red tail.
        """
        self.ensure_one()
        children = self.child_ids.filtered(lambda c: c.active)
        if not children:
            return
        starts, ends, eff_ends = [], [], []
        for c in children:
            c_s = c.planned_date_start
            c_e = c.planned_date_end
            if not c_s and not c_e and self.planned_date_start and self.planned_date_end:
                c_s = self.planned_date_start
                c_e = self.planned_date_end
            if c_s and c_e:
                starts.append(c_s)
                ends.append(c_e)
                if c.state != "1_canceled":
                    eff = c_e
                    if c.date_deadline and isinstance(c.date_deadline, datetime) and c.date_deadline > c_e:
                        eff = c.date_deadline
                    eff_ends.append(eff)
            elif c.state != "1_canceled" and c.date_deadline and isinstance(c.date_deadline, datetime):
                eff_ends.append(c.date_deadline)

        vals = {}
        manual_plan = bool(self.planned_date_start and self.planned_date_end) and not self.plan_is_auto
        if not manual_plan and starts and ends:
            min_s, max_e = min(starts), max(ends)
            if max_e < min_s:
                min_s = max_e
            if self.planned_date_start != min_s:
                vals["planned_date_start"] = min_s
            if self.planned_date_end != max_e:
                vals["planned_date_end"] = max_e
            if not self.plan_is_auto:
                vals["plan_is_auto"] = True
            eff_s = vals.get("planned_date_start", self.planned_date_start)
            eff_e = vals.get("planned_date_end", self.planned_date_end)
            if (
                ("planned_date_start" in vals or "planned_date_end" in vals)
                and eff_s and eff_e
            ):
                user_tz = get_user_tz(self.env)
                w_h = calculate_lithuanian_working_hours(
                    utc_to_local_dt(self.env, eff_s, user_tz=user_tz),
                    utc_to_local_dt(self.env, eff_e, user_tz=user_tz),
                )
                if w_h > 0:
                    vals["allocated_hours"] = w_h

        p_end = vals.get("planned_date_end", self.planned_date_end)

        non_canceled_children = children.filtered(lambda c: c.state != "1_canceled")
        all_children_finished = bool(
            non_canceled_children and all(c.state in ("03_approved", "1_done") for c in non_canceled_children)
        )

        if all_children_finished:
            child_finish_dates = []
            for c in non_canceled_children:
                c_p_end = c.planned_date_end
                # A child's actual completion point:
                if c.date_deadline and isinstance(c.date_deadline, datetime) and (not c_p_end or c.date_deadline > c_p_end):
                    child_finish_dates.append(c.date_deadline)
                elif c.actual_finish_date and isinstance(c.actual_finish_date, datetime):
                    child_finish_dates.append(c.actual_finish_date)
                elif c.date_end and isinstance(c.date_end, datetime):
                    child_finish_dates.append(c.date_end)
                elif c_p_end and isinstance(c_p_end, datetime):
                    child_finish_dates.append(c_p_end)
                elif c.date_deadline and isinstance(c.date_deadline, datetime):
                    child_finish_dates.append(c.date_deadline)

            if (
                self.first_timer_start
                and self.date_deadline
                and isinstance(self.date_deadline, datetime)
                and p_end
                and self.date_deadline > p_end
                and self.state != "1_canceled"
            ):
                child_finish_dates.append(self.date_deadline)

            max_child_finish = max(child_finish_dates) if child_finish_dates else None

            if self.state in ("1_canceled", "02_changes_requested"):
                vals["actual_finish_date"] = False
                vals["date_end"] = False
            elif p_end and max_child_finish:
                if max_child_finish > p_end:
                    vals["date_deadline"] = max_child_finish
                    vals["deadline_is_auto"] = True
                    vals["actual_finish_date"] = False
                    vals["date_end"] = max_child_finish
                elif max_child_finish < p_end:
                    vals["actual_finish_date"] = max_child_finish
                    vals["date_end"] = max_child_finish
                    if self.deadline_is_auto and self.date_deadline:
                        vals["date_deadline"] = False
                        vals["deadline_is_auto"] = False
                else:
                    # max_child_finish == p_end: exactly on time
                    vals["actual_finish_date"] = max_child_finish
                    vals["date_end"] = max_child_finish
                    if self.deadline_is_auto and self.date_deadline:
                        vals["date_deadline"] = False
                        vals["deadline_is_auto"] = False
            elif max_child_finish:
                vals["actual_finish_date"] = max_child_finish
                vals["date_end"] = max_child_finish

            if self.state not in ("1_canceled", "02_changes_requested"):
                target_state = "1_done" if all(c.state == "1_done" for c in non_canceled_children) else "03_approved"
                if self.state != target_state:
                    vals["state"] = target_state
        else:
            # Not all children finished: container CANNOT have early finish!
            if self.actual_finish_date:
                vals["actual_finish_date"] = False
            if self.date_end:
                vals["date_end"] = False
            if self.state in ("03_approved", "1_done"):
                vals["state"] = "01_in_progress"

            # Check late tail while in progress
            tail_end = max(eff_ends) if eff_ends else None
            if (
                self.first_timer_start
                and self.date_deadline
                and isinstance(self.date_deadline, datetime)
                and p_end
                and self.date_deadline > p_end
                and self.state != "1_canceled"
            ):
                tail_end = max(tail_end, self.date_deadline) if tail_end else self.date_deadline
            cur_dl = self.date_deadline
            if p_end and tail_end and tail_end > p_end:
                if not cur_dl or self.deadline_is_auto or cur_dl < tail_end:
                    if cur_dl != tail_end or not self.deadline_is_auto:
                        vals["date_deadline"] = tail_end
                        vals["deadline_is_auto"] = True
            elif self.deadline_is_auto and cur_dl:
                # Subtasks no longer overflow the parent: the system-written tail goes away.
                vals["date_deadline"] = False
                vals["deadline_is_auto"] = False

        if vals:
            try:
                self.with_context(
                    skip_hierarchy_propagation=True,
                    gannt_auto_plan=True,
                    mail_notrack=True,
                    _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                ).sudo().write(vals)
            except Exception as e:
                _logger.warning("Could not update container task %s: %s", self.id, e)

    def _propagate_deadline_upward(self):
        """
        Re-evaluates the task itself (if it has subtasks) and every ancestor (see _recompute_container),
        then the milestone span.
        """
        is_trusted = self._is_trusted_ctx()
        if is_trusted and (
            self.env.context.get("skip_hierarchy_propagation")
            or self.env.context.get("defer_hierarchy_propagation")
        ):
            return

        # 1. Gather all unique container tasks (tasks in self with children, plus all ancestors)
        containers_depth = {}

        def _gather(t, depth=0, path=None):
            if path is None:
                path = set()
            if not t or t.id in path:
                return
            next_path = path | {t.id}
            if t.child_ids and (t.id not in containers_depth or containers_depth[t.id][1] < depth):
                containers_depth[t.id] = (t, depth)
            if t.parent_id:
                _gather(t.parent_id, depth + 1, next_path)

        for rec in self:
            _gather(rec, depth=0)

        # Recompute bottom-up (lowest depth = leaf containers first, highest depth = top-level ancestors)
        sorted_containers = sorted(containers_depth.values(), key=lambda x: x[1])
        for container, _depth in sorted_containers:
            container._recompute_container()

        # 2. Propagate to distinct milestones once (batched query to avoid N+1)
        milestone_ids = set()
        for rec in self:
            if rec.milestone_id:
                milestone_ids.add(rec.milestone_id.id)
        for container, _depth in sorted_containers:
            if container.milestone_id:
                milestone_ids.add(container.milestone_id.id)

        if milestone_ids:
            milestones = self.env["project.milestone"].browse(list(milestone_ids)).filtered(lambda m: m.exists())
            if milestones:
                user_tz = get_user_tz(self.env)
                all_ms_tasks = self.env["project.task"].search([
                    ("milestone_id", "in", milestones.ids),
                    ("active", "=", True),
                ])
                ms_tasks_map = {}
                for task_rec in all_ms_tasks:
                    ms_tasks_map.setdefault(task_rec.milestone_id.id, []).append(task_rec)

                for ms in milestones:
                    ms_tasks = ms_tasks_map.get(ms.id, [])
                    if not ms_tasks:
                        continue
                    ms_vals = {}
                    ms_starts = [utc_to_local_dt(self.env, t.planned_date_start, user_tz=user_tz).date() for t in ms_tasks if t.planned_date_start]
                    ms_ends = [utc_to_local_dt(self.env, t.planned_date_end, user_tz=user_tz).date() for t in ms_tasks if t.planned_date_end]
                    if ms_starts and hasattr(ms, "date_start"):
                        min_ms_s = min(ms_starts)
                        if not ms.date_start or min_ms_s < ms.date_start:
                            ms_vals["date_start"] = min_ms_s
                    if ms_ends and hasattr(ms, "planned_date_end"):
                        max_ms_e = max(ms_ends)
                        if not ms.planned_date_end or max_ms_e > ms.planned_date_end:
                            ms_vals["planned_date_end"] = max_ms_e
                    eff_ms_start = ms_vals.get("date_start", ms.date_start)
                    eff_ms_end = ms_vals.get("planned_date_end", ms.planned_date_end)
                    if eff_ms_start and eff_ms_end and eff_ms_end < eff_ms_start:
                        ms_vals["planned_date_end"] = eff_ms_start
                    if ms_vals:
                        try:
                            ms.with_context(mail_notrack=True).sudo().write(ms_vals)
                        except Exception as exc:
                            _logger.warning("Could not update milestone %s span: %s", ms.id, exc)

    def _normalize_form_datetime_vals(self, vals):
        """
        Normalizes midnight (00:00:00) and end-of-day (23:59:59) local timestamps sent by the Odoo Form
        datepicker into standard business working hours (08:00:00 start, 17:00:00 end/deadline) in the user's
        local timezone, preventing UTC midnight rollover (+1 day) mismatches between Form View and Timeline.
        """
        if self._is_trusted_ctx() and (
            self.env.context.get("from_timeline_batch")
            or self.env.context.get("skip_hierarchy_propagation")
        ):
            return
        if vals.get("planned_date_start"):
            try:
                s_dt = fields.Datetime.to_datetime(vals["planned_date_start"]) if isinstance(vals["planned_date_start"], str) else vals["planned_date_start"]
                if isinstance(s_dt, datetime):
                    s_local = utc_to_local_dt(self.env, s_dt)
                    if s_local.hour == 0 and s_local.minute == 0:
                        s_local = s_local.replace(hour=8, minute=0, second=0)
                        vals["planned_date_start"] = local_to_utc_dt(self.env, s_local)
            except Exception:
                pass
        if vals.get("planned_date_end"):
            try:
                e_dt = fields.Datetime.to_datetime(vals["planned_date_end"]) if isinstance(vals["planned_date_end"], str) else vals["planned_date_end"]
                if isinstance(e_dt, datetime):
                    e_local = utc_to_local_dt(self.env, e_dt)
                    if (e_local.hour == 0 and e_local.minute == 0) or (e_local.hour == 23 and e_local.minute == 59):
                        e_local = e_local.replace(hour=17, minute=0, second=0)
                        vals["planned_date_end"] = local_to_utc_dt(self.env, e_local)
            except Exception:
                pass
        if vals.get("date_deadline"):
            try:
                dl_dt = fields.Datetime.to_datetime(vals["date_deadline"]) if isinstance(vals["date_deadline"], str) else vals["date_deadline"]
                if isinstance(dl_dt, datetime):
                    dl_local = utc_to_local_dt(self.env, dl_dt)
                    if (dl_local.hour == 0 and dl_local.minute == 0) or (dl_local.hour == 23 and dl_local.minute == 59):
                        dl_local = dl_local.replace(hour=17, minute=0, second=0)
                        vals["date_deadline"] = local_to_utc_dt(self.env, dl_local)
            except Exception:
                pass

    @api.model_create_multi
    def create(self, vals_list):
        is_trusted = self._is_trusted_ctx()
        user_tz = get_user_tz(self.env)
        holidays_cache = {}
        clean_vals_list = []
        for orig_vals in vals_list:
            vals = dict(orig_vals)
            if not is_trusted and any(f in vals for f in _APPROVAL_LOCK_FIELDS):
                raise AccessError(_("Patvirtinimo istorijos ir bazinio plano laukų tiesiogiai nustatyti negalima."))

            if vals.get("parent_id") and not vals.get("planned_date_start") and not vals.get("planned_date_end"):
                try:
                    anc = self.browse(vals["parent_id"])
                    visited = set()
                    while anc.exists() and anc.id not in visited:
                        visited.add(anc.id)
                        if anc.planned_date_start and anc.planned_date_end:
                            vals["planned_date_start"] = anc.planned_date_start
                            vals["planned_date_end"] = anc.planned_date_end
                            vals["plan_is_auto"] = False
                            break
                        anc = anc.parent_id
                except Exception:
                    pass

            self._normalize_form_datetime_vals(vals)
            s_dt_obj = None
            e_dt_obj = None
            s = vals.get("planned_date_start")
            e = vals.get("planned_date_end") or vals.get("date_deadline")
            if s and e:
                try:
                    s_dt_obj = fields.Datetime.to_datetime(s) if isinstance(s, str) else s
                    e_dt_obj = fields.Datetime.to_datetime(e) if isinstance(e, str) else e
                except (ValueError, TypeError):
                    pass

            # Auto-fill allocated_hours from actual working hours (darbo valandos)
            if "allocated_hours" not in vals:
                if s_dt_obj and e_dt_obj:
                    try:
                        w_h = calculate_lithuanian_working_hours(
                            utc_to_local_dt(self.env, s_dt_obj, user_tz=user_tz),
                            utc_to_local_dt(self.env, e_dt_obj, user_tz=user_tz),
                            holidays_cache=holidays_cache,
                        )
                        if w_h > 0:
                            vals["allocated_hours"] = w_h
                    except Exception:
                        pass

            if vals.get("state") in ("03_approved", "1_done") and not vals.get("has_been_approved"):
                dur = 0.0
                if s_dt_obj and e_dt_obj:
                    dur = calculate_lithuanian_working_hours(
                        utc_to_local_dt(self.env, s_dt_obj, user_tz=user_tz),
                        utc_to_local_dt(self.env, e_dt_obj, user_tz=user_tz),
                        holidays_cache=holidays_cache,
                    )
                    if dur <= 0:
                        alloc_val = float(vals.get("allocated_hours") or 0.0)
                        dur = alloc_val if alloc_val > 0 else min(8.0, max(0.01, round((e_dt_obj - s_dt_obj).total_seconds() / 3600.0, 2)))
                alloc = float(vals.get("allocated_hours") or 0.0)
                if alloc > dur:
                    dur = alloc
                vals.update({
                    "has_been_approved": True,
                    "first_approved_date": fields.Datetime.now(),
                    "approved_planned_date_start": s_dt_obj,
                    "approved_planned_date_end": e_dt_obj,
                    "approved_baseline_duration": dur,
                })
            clean_vals_list.append(vals)

        records = super().create(clean_vals_list)
        records.with_context(_gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN)._propagate_deadline_upward()
        return records

    def _cascade_planned_dates_to_unscheduled_descendants(self, start_dt, end_dt, visited=None):
        """
        When planned dates are set on a parent task, any active descendant (subtask / sub-subtask)
        that does not yet have planned dates inherits them with plan_is_auto=False.
        """
        if visited is None:
            visited = set()
        for rec in self:
            if rec.id in visited:
                continue
            visited.add(rec.id)
            for child in rec.child_ids.filtered(lambda c: c.active):
                if child.id in visited:
                    continue
                if not child.planned_date_start and not child.planned_date_end:
                    child.with_context(
                        skip_hierarchy_propagation=True,
                        gannt_auto_plan=True,
                        mail_notrack=True,
                        _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                    ).sudo().write({
                        "planned_date_start": start_dt,
                        "planned_date_end": end_dt,
                        "plan_is_auto": False,
                    })
                child._cascade_planned_dates_to_unscheduled_descendants(
                    child.planned_date_start or start_dt,
                    child.planned_date_end or end_dt,
                    visited=visited,
                )

    def write(self, vals):
        vals = dict(vals)
        is_trusted = self._is_trusted_ctx()
        auto_ctx = bool(is_trusted and self.env.context.get("gannt_auto_plan"))
        trusted_skip_prop = bool(is_trusted and self.env.context.get("skip_hierarchy_propagation"))
        trusted_from_finish = bool(is_trusted and self.env.context.get("from_timer_finish"))
        trusted_admin_override = bool(is_trusted and self.env.context.get("gannt_admin_override") and self._has_edit_rights())

        if not is_trusted:
            if any(f in vals for f in _APPROVAL_LOCK_FIELDS):
                raise AccessError(_("Patvirtinimo istorijos ir bazinio plano laukų tiesiogiai keisti negalima."))
            if any(f in vals for f in _SCHEDULE_EDIT_FIELDS):
                self._check_gannt_lt_edit_access()
            if any(f in vals for f in _TIMER_STATE_FIELDS):
                self._check_gannt_lt_timer_access()
            if "state" in vals and (
                vals.get("state") in ("03_approved", "1_done", "1_canceled")
                or any(rec.state in ("03_approved", "1_done", "1_canceled") for rec in self)
            ):
                self._check_gannt_lt_timer_access()

        user_tz = get_user_tz(self.env)
        holidays_cache = {}
        if not auto_ctx:
            self._normalize_form_datetime_vals(vals)

        if "parent_id" in vals and vals.get("parent_id") and "planned_date_start" not in vals and "planned_date_end" not in vals:
            try:
                p_id = int(vals["parent_id"])
                anc = self.browse(p_id)
                visited_anc = set()
                while anc.exists() and anc.id not in visited_anc:
                    visited_anc.add(anc.id)
                    if anc.planned_date_start and anc.planned_date_end:
                        unscheduled = self.filtered(lambda r: not r.planned_date_start and not r.planned_date_end)
                        if unscheduled:
                            unscheduled.with_context(
                                skip_hierarchy_propagation=True,
                                gannt_auto_plan=True,
                                mail_notrack=True,
                                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                            ).write({
                                "planned_date_start": anc.planned_date_start,
                                "planned_date_end": anc.planned_date_end,
                                "plan_is_auto": False,
                            })
                        break
                    anc = anc.parent_id
            except (ValueError, TypeError):
                pass

        # A user (form, timeline drag) typing planned dates / a deadline makes them MANUAL.
        # Internal system writes pass context gannt_auto_plan and keep their own flags.
        if not auto_ctx:
            if ("planned_date_start" in vals or "planned_date_end" in vals) and "plan_is_auto" not in vals:
                vals["plan_is_auto"] = False
            if "date_deadline" in vals and "deadline_is_auto" not in vals:
                vals["deadline_is_auto"] = False

        # Auto-update allocated_hours from working hours (darbo valandos) if dates are modified
        needs_multi_alloc = False
        if (
            ("planned_date_start" in vals or "planned_date_end" in vals or ("date_deadline" in vals and not auto_ctx))
            and "allocated_hours" not in vals
        ):
            if len(self) == 1:
                rec = self[0]
                s_val = vals.get("planned_date_start") if "planned_date_start" in vals else rec.planned_date_start
                e_val = vals.get("planned_date_end") if "planned_date_end" in vals else (rec.planned_date_end or (vals.get("date_deadline") if "date_deadline" in vals else rec.date_deadline))
                if not s_val and not e_val:
                    vals["allocated_hours"] = 0.0
                elif s_val and e_val:
                    try:
                        s_dt = fields.Datetime.to_datetime(s_val) if isinstance(s_val, str) else s_val
                        e_dt = fields.Datetime.to_datetime(e_val) if isinstance(e_val, str) else e_val
                        w_h = calculate_lithuanian_working_hours(
                            utc_to_local_dt(self.env, s_dt, user_tz=user_tz),
                            utc_to_local_dt(self.env, e_dt, user_tz=user_tz),
                            holidays_cache=holidays_cache,
                        )
                        if w_h > 0:
                            vals["allocated_hours"] = w_h
                    except (ValueError, TypeError):
                        pass
            elif len(self) > 1:
                needs_multi_alloc = True

        was_completed = {
            rec.id: (rec.state in ("03_approved", "1_done") and not rec.is_timer_running)
            for rec in self
        }
        # Immutability validation (Anti-Cheating): once approved, start cannot be moved & duration cannot be decreased
        if not trusted_from_finish and not trusted_skip_prop and not trusted_admin_override:
            for rec in self:
                # Concurrency lock check: if task is active or paused by another worker
                if (
                    (rec.is_timer_running or rec.is_paused)
                    and rec.active_user_id
                    and rec.active_user_id != self.env.user
                    and ("planned_date_start" in vals or "planned_date_end" in vals or "state" in vals)
                    and not self._has_timer_override_rights()
                ):
                    raise UserError(
                        _("Užduotį šiuo metu vykdo %s. Ji yra užrakinta kitiems vartotojams.")
                        % rec.active_user_id.name
                    )

                if rec.has_been_approved:
                    # Rule 1: Cannot move or clear start date
                    if "planned_date_start" in vals:
                        orig_start = rec.approved_planned_date_start or rec.planned_date_start
                        if not vals.get("planned_date_start"):
                            if orig_start:
                                raise ValidationError(
                                    _("Užduotis „%s“ jau buvo patvirtinta. Suplanuotos pradžios negalima išvalyti ar perkelti.")
                                    % rec.name
                                )
                        else:
                            new_start = fields.Datetime.to_datetime(vals["planned_date_start"]) if isinstance(vals["planned_date_start"], str) else vals["planned_date_start"]
                            if orig_start and new_start:
                                diff_sec = abs((new_start - orig_start).total_seconds())
                                if diff_sec > 60:
                                    raise ValidationError(
                                        _("Užduotis „%s“ jau buvo patvirtinta. Jos negalima perkelti laiko juostoje (suplanuota pradžia užrakinta).")
                                        % rec.name
                                    )

                    # Rule 2: Cannot clear end date or decrease duration
                    if "planned_date_end" in vals:
                        orig_end = rec.approved_planned_date_end or rec.planned_date_end
                        if not vals.get("planned_date_end"):
                            if orig_end:
                                raise ValidationError(
                                    _("Užduotis „%s“ jau buvo patvirtinta. Suplanuotos pabaigos negalima išvalyti ar sumažinti trukmės.")
                                    % rec.name
                                )
                        else:
                            new_end = fields.Datetime.to_datetime(vals["planned_date_end"]) if isinstance(vals["planned_date_end"], str) else vals["planned_date_end"]
                            if orig_end and new_end:
                                if (orig_end - new_end).total_seconds() > 60:
                                    cur_start = rec.approved_planned_date_start or rec.planned_date_start
                                    new_dur = calculate_lithuanian_working_hours(
                                        utc_to_local_dt(self.env, cur_start, user_tz=user_tz),
                                        utc_to_local_dt(self.env, new_end, user_tz=user_tz),
                                        holidays_cache=holidays_cache,
                                    ) if cur_start else 0.0
                                    base_dur = rec.approved_baseline_duration or (
                                        calculate_lithuanian_working_hours(
                                            utc_to_local_dt(self.env, cur_start, user_tz=user_tz),
                                            utc_to_local_dt(self.env, orig_end, user_tz=user_tz),
                                            holidays_cache=holidays_cache,
                                        ) if cur_start and orig_end else 0.0
                                    )
                                    if (not base_dur or base_dur <= 0) and cur_start and orig_end:
                                        alloc_val = float(rec.allocated_hours or 0.0)
                                        base_dur = alloc_val if alloc_val > 0 else min(8.0, max(0.01, round((orig_end - cur_start).total_seconds() / 3600.0, 2)))
                                    if base_dur > 0:
                                        if new_dur <= 0 and cur_start and new_end:
                                            alloc_val = float(vals.get("allocated_hours") or 0.0)
                                            new_dur = alloc_val if alloc_val > 0 else min(8.0, max(0.01, round((new_end - cur_start).total_seconds() / 3600.0, 2)))
                                        if new_dur < (base_dur - 0.05):
                                            raise ValidationError(
                                                _("Užduotis „%s“ jau buvo patvirtinta. Suplanuotos trukmės negalima sumažinti (bazinis planas: %s val.).")
                                                % (rec.name, round(base_dur, 2))
                                            )

                    if "allocated_hours" in vals:
                        new_alloc = float(vals.get("allocated_hours") or 0.0)
                        base_dur = rec.approved_baseline_duration or 0.0
                        if base_dur > 0 and new_alloc < (base_dur - 0.05):
                            raise ValidationError(
                                _("Užduotis „%s“ jau buvo patvirtinta. Suplanuotų valandų negalima sumažinti (bazinis planas: %s val.).")
                                % (rec.name, round(base_dur, 2))
                            )

                    # Rule 4: Cannot clear or shorten date_deadline manually once approved/completed
                    if "date_deadline" in vals and not is_trusted:
                        new_dl = vals.get("date_deadline")
                        if not new_dl and rec.date_deadline:
                            raise ValidationError(
                                _("Užduotis „%s“ jau buvo patvirtinta. Termino (Deadline) išvalyti negalima.") % rec.name
                            )
                        if new_dl and rec.date_deadline:
                            new_dl_dt = fields.Datetime.to_datetime(new_dl) if isinstance(new_dl, str) else new_dl
                            if isinstance(new_dl_dt, datetime) and new_dl_dt < rec.date_deadline:
                                raise ValidationError(
                                    _("Užduotis „%s“ jau buvo patvirtinta. Termino (Deadline) sutrumpinti negalima.") % rec.name
                                )

                # Parent task hierarchy state rule:
                # A parent task CANNOT be marked '1_done' (Done) until ALL tasks below it are '1_done' (Done).
                if "state" in vals and not auto_ctx and rec.child_ids:
                    target_st = vals.get("state")
                    if target_st == "1_done":
                        not_done_desc = self.env["project.task"].sudo().search([
                            ("id", "child_of", rec.id),
                            ("id", "!=", rec.id),
                            ("active", "=", True),
                            ("state", "not in", ["1_done", "1_canceled"]),
                        ], limit=1)
                        if not_done_desc:
                            raise ValidationError(
                                _("Tėvinė užduotis „%s“ negali būti pažymėta kaip „Atlikta (Done)“, kol visos jos po-užduotys nėra patikrintos ir pažymėtos kaip „Atlikta (Done)“ (pvz., „%s“).")
                                % (rec.name, not_done_desc.name)
                            )
                    elif target_st == "03_approved":
                        unfinished_desc = self.env["project.task"].sudo().search([
                            ("id", "child_of", rec.id),
                            ("id", "!=", rec.id),
                            ("active", "=", True),
                            ("state", "not in", ["03_approved", "1_done", "1_canceled"]),
                        ], limit=1)
                        if unfinished_desc:
                            raise ValidationError(
                                _("Tėvinė užduotis „%s“ negali būti pažymėta kaip „Patvirtinta (Approved)“, kol visos jos po-užduotys nėra bent „Patvirtinta (Approved)“ arba „Atlikta (Done)“ (pvz., „%s“).")
                                % (rec.name, unfinished_desc.name)
                            )

        res = super().write(vals)

        if needs_multi_alloc:
            for rec in self:
                s_dt = rec.planned_date_start
                e_dt = rec.planned_date_end or rec.date_deadline
                target_alloc = None
                if not s_dt and not e_dt:
                    if rec.allocated_hours:
                        target_alloc = 0.0
                elif s_dt and e_dt:
                    w_h = calculate_lithuanian_working_hours(
                        utc_to_local_dt(self.env, s_dt, user_tz=user_tz),
                        utc_to_local_dt(self.env, e_dt, user_tz=user_tz),
                        holidays_cache=holidays_cache,
                    )
                    if w_h > 0 and abs((rec.allocated_hours or 0.0) - w_h) > 1e-4:
                        target_alloc = w_h
                if target_alloc is not None:
                    rec.with_context(
                        skip_hierarchy_propagation=True,
                        gannt_auto_plan=True,
                        mail_notrack=True,
                        _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                    ).write({"allocated_hours": target_alloc})

        # An auto-written deadline is only true while the plan ends BEFORE it. When the plan end was moved
        # (form / timeline drag-resize) up to or past it, the system clears it. Typed deadlines are never touched.
        if not auto_ctx and ("planned_date_end" in vals or "planned_date_start" in vals):
            for rec in self:
                if (
                    rec.deadline_is_auto and rec.date_deadline and rec.planned_date_end
                    and rec.planned_date_end >= rec.date_deadline
                ):
                    rec.with_context(
                        skip_hierarchy_propagation=True,
                        gannt_auto_plan=True,
                        mail_notrack=True,
                        _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                    ).write({"date_deadline": False, "deadline_is_auto": False})
                if rec.planned_date_start and rec.planned_date_end and rec.child_ids:
                    rec._cascade_planned_dates_to_unscheduled_descendants(rec.planned_date_start, rec.planned_date_end)

        # When state is changed directly to Done ('1_done') or Approved ('03_approved') via Form/Kanban/Gantt:
        # evaluate _work_stop_vals(now, cur_total_sec, finished=True) ONLY when transitioning from a non-completed
        # state (or while timer is running). Confirming an already-Approved ('03_approved') task into Done ('1_done')
        # preserves the worker's original completion deadline.
        if (
            "state" in vals
            and not trusted_from_finish
            and not trusted_skip_prop
        ):
            new_state = vals.get("state")
            if new_state in ("03_approved", "1_done"):
                now = fields.Datetime.now()
                for rec in self:
                    if not was_completed.get(rec.id) and "actual_finish_date" not in vals:
                        eff_sec = int(round((rec.effective_hours or 0.0) * 3600))
                        cur_total_sec = max(rec.timer_total_seconds or 0, eff_sec)
                        if rec.is_timer_running and rec.timer_start:
                            cur_total_sec += max(0, int((now - rec.timer_start).total_seconds()))
                        stop_vals, _stop_info = rec._work_stop_vals(now, cur_total_sec, finished=True)
                        if rec.is_timer_running:
                            stop_vals.update({
                                "is_timer_running": False,
                                "timer_start": False,
                                "timer_total_seconds": cur_total_sec,
                            })
                        if stop_vals:
                            rec.with_context(
                                skip_hierarchy_propagation=True,
                                gannt_auto_plan=True,
                                from_timer_finish=True,
                                mail_notrack=True,
                                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                            ).write(stop_vals)
                    if not rec.has_been_approved:
                        s_dt = rec.planned_date_start
                        e_dt = rec.planned_date_end
                        dur = 0.0
                        if s_dt and e_dt:
                            dur = calculate_lithuanian_working_hours(
                                utc_to_local_dt(self.env, s_dt, user_tz=user_tz),
                                utc_to_local_dt(self.env, e_dt, user_tz=user_tz),
                                holidays_cache=holidays_cache,
                            )
                            if dur <= 0:
                                alloc_val = float(rec.allocated_hours or 0.0)
                                dur = alloc_val if alloc_val > 0 else min(8.0, max(0.01, round((e_dt - s_dt).total_seconds() / 3600.0, 2)))
                        alloc_h = float(rec.allocated_hours or 0.0)
                        if alloc_h > dur:
                            dur = alloc_h
                        rec.with_context(
                            skip_hierarchy_propagation=True,
                            gannt_auto_plan=True,
                            mail_notrack=True,
                            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                        ).write({
                            "has_been_approved": True,
                            "first_approved_date": now,
                            "approved_planned_date_start": s_dt,
                            "approved_planned_date_end": e_dt,
                            "approved_baseline_duration": dur,
                            "active_user_id": False,
                            "is_paused": False,
                        })
                    elif rec.active_user_id or rec.is_paused:
                        rec.with_context(
                            skip_hierarchy_propagation=True,
                            gannt_auto_plan=True,
                            mail_notrack=True,
                            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                        ).write({
                            "active_user_id": False,
                            "is_paused": False,
                        })
            elif new_state == "1_canceled":
                for rec in self:
                    cancel_vals = {}
                    if rec.is_timer_running or rec.timer_start:
                        cancel_vals["is_timer_running"] = False
                        cancel_vals["timer_start"] = False
                    if rec.deadline_is_auto and rec.date_deadline:
                        cancel_vals["date_deadline"] = False
                        cancel_vals["deadline_is_auto"] = False
                    if rec.actual_finish_date:
                        cancel_vals["actual_finish_date"] = False
                    if rec.date_end:
                        cancel_vals["date_end"] = False
                    if rec.active_user_id or rec.is_paused:
                        cancel_vals["active_user_id"] = False
                        cancel_vals["is_paused"] = False
                    if cancel_vals:
                        rec.with_context(
                            skip_hierarchy_propagation=True,
                            gannt_auto_plan=True,
                            mail_notrack=True,
                            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                        ).write(cancel_vals)
                    visited_anc = {rec.id}
                    anc = rec.parent_id
                    while anc and anc.id not in visited_anc:
                        visited_anc.add(anc.id)
                        if anc.state in ("03_approved", "1_done") or anc.actual_finish_date or anc.date_end:
                            anc.with_context(
                                skip_hierarchy_propagation=True,
                                gannt_auto_plan=True,
                                mail_notrack=True,
                                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                            ).sudo().write({
                                "state": "01_in_progress",
                                "actual_finish_date": False,
                                "date_end": False,
                            })
                        anc = anc.parent_id
            elif new_state in ("01_in_progress", "02_changes_requested", "04_waiting_normal"):
                for rec in self:
                    reset_vals = {}
                    if rec.actual_finish_date:
                        reset_vals["actual_finish_date"] = False
                    if rec.date_end:
                        reset_vals["date_end"] = False
                    if rec.deadline_is_auto:
                        reset_vals["date_deadline"] = False
                        reset_vals["deadline_is_auto"] = False
                    if reset_vals:
                        rec.with_context(
                            skip_hierarchy_propagation=True,
                            gannt_auto_plan=True,
                            mail_notrack=True,
                            _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                        ).write(reset_vals)
                    visited_anc = {rec.id}
                    anc = rec.parent_id
                    while anc and anc.id not in visited_anc:
                        visited_anc.add(anc.id)
                        if anc.state in ("03_approved", "1_done") or anc.actual_finish_date or anc.date_end:
                            anc_vals = {
                                "state": "01_in_progress",
                                "actual_finish_date": False,
                                "date_end": False,
                            }
                            if anc.deadline_is_auto:
                                anc_vals["date_deadline"] = False
                                anc_vals["deadline_is_auto"] = False
                            anc.with_context(
                                skip_hierarchy_propagation=True,
                                gannt_auto_plan=True,
                                mail_notrack=True,
                                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
                            ).sudo().write(anc_vals)
                        anc = anc.parent_id

        # Parent task and Milestone hierarchy propagation (run once for entire batch)
        if not trusted_skip_prop and (
            "date_deadline" in vals
            or "planned_date_start" in vals
            or "planned_date_end" in vals
            or "parent_id" in vals
            or "milestone_id" in vals
            or (
                "state" in vals
                and not trusted_from_finish
                and vals.get("state") in (
                    "03_approved", "1_done", "1_canceled",
                    "01_in_progress", "02_changes_requested", "04_waiting_normal",
                )
            )
        ):
            self.with_context(
                mail_notrack=True,
                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
            )._propagate_deadline_upward()

        # If task state/progress changed to not done, clear downstream dependent tasks
        if (
            ("state" in vals and vals.get("state") not in ("03_approved", "1_done"))
            or (
                "progress" in vals
                and vals.get("progress") is not None
                and vals.get("progress") is not False
                and float(vals.get("progress") or 0.0) < 1.0
            )
        ):
            for rec in self:
                is_not_done = (
                    (rec.state and rec.state not in ("03_approved", "1_done"))
                    or (rec.progress is not None and rec.progress is not False and float(rec.progress or 0.0) < 1.0)
                )
                if is_not_done:
                    rec._cascade_clear_downstream_progress()

        return res

    @api.model
    def get_all_in_one_timeline_data(self, project_id=None, domain=None, date_start=None, date_end=None, user_id=None, model_name=None, only_active=False, only_overdue=False, only_mo=False, only_po=False, only_delivery=False):
        """
        Fetches structured timeline data for DHTMLX Gantt chart:
        - Projects and Tasks hierarchy (Project -> Task -> Subtask)
        - Dependency links (depend_on_ids)
        - Available resources (res.users)
        - Available projects for filter dropdown
        Safely applies search domain whether the active view is on project.project or project.task.
        """
        if not self._has_view_rights():
            raise AccessError(_("Jūs neturite prieigos prie GanntLT laiko juostos."))
        user_tz = get_user_tz(self.env)
        holidays_cache = {}
        dt_format = "%Y-%m-%d %H:%M:%S"
        can_edit = self._has_edit_rights()
        can_track_time = self._has_timer_rights()
        has_mrp = bool("mrp.production" in self.env and self.env["mrp.production"].has_access("read"))
        has_purchase = bool("purchase.order" in self.env and self.env["purchase.order"].has_access("read"))
        has_delivery = bool("stock.picking" in self.env and self.env["stock.picking"].has_access("read"))

        is_only_active = bool(only_active or project_id == "active")
        is_only_overdue = bool(only_overdue or project_id == "overdue")
        is_only_mo = bool(only_mo or project_id == "only_mo")
        is_only_po = bool(only_po or project_id == "only_po")
        is_only_delivery = bool(only_delivery or project_id == "only_delivery")

        now_utc = fields.Datetime.now()
        now_loc_supply = utc_to_local_dt(self.env, now_utc, user_tz=user_tz)

        def _build_supply_gantt_item(kind, rec, parent_key, fallback_start, rec_proj, rec_user):
            if kind == "po":
                s_dt = rec.date_order or rec.date_approve or rec.create_date or fallback_start
                s_loc = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                e_loc = utc_to_local_dt(self.env, rec.date_planned, user_tz=user_tz) if rec.date_planned else (s_loc + timedelta(days=7))
                if e_loc <= s_loc:
                    e_loc = s_loc + timedelta(days=1)
                has_explicit_end = bool(rec.date_planned)
                done_picks = rec.picking_ids.filtered(lambda pk: pk.state == "done" and pk.date_done) if "picking_ids" in rec._fields else False
                rec_status = getattr(rec, "receipt_status", False)
                is_done = (rec_status == "full") or (rec.state == "done" and not getattr(rec, "picking_ids", False))
                prog = 1.0 if is_done else (0.5 if rec.state == "purchase" else 0.0)
                color = "#16a34a" if is_done else "#0284c7"
                act_loc = max((utc_to_local_dt(self.env, pk.date_done, user_tz=user_tz) for pk in done_picks), default=None) if (is_done and done_picks) else None
                item_id = f"po_{rec.id}"
                label = f"{rec.name} - {rec.partner_id.name}" if rec.partner_id else rec.name
            elif kind == "mo":
                s_dt = rec.date_start or rec.create_date or fallback_start
                s_loc = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                is_done = (rec.state == "done")
                if rec.date_deadline:
                    e_loc = utc_to_local_dt(self.env, rec.date_deadline, user_tz=user_tz)
                elif is_done and rec.date_finished:
                    e_loc = utc_to_local_dt(self.env, rec.date_finished, user_tz=user_tz)
                else:
                    e_loc = s_loc + timedelta(days=3)
                if e_loc <= s_loc:
                    e_loc = s_loc + timedelta(days=1)
                has_explicit_end = bool(rec.date_deadline or (is_done and rec.date_finished))
                prog = 1.0 if is_done else (0.5 if rec.state == "progress" else 0.0)
                color = "#16a34a" if is_done else "#ea580c"
                act_loc = utc_to_local_dt(self.env, rec.date_finished, user_tz=user_tz) if (is_done and rec.date_finished) else None
                item_id = f"mo_{rec.id}"
                label = f"{rec.name}: {rec.product_id.display_name or rec.product_id.name or ''}"
            else:
                s_dt = rec.scheduled_date or rec.date_done or rec.date_deadline or rec.create_date or fallback_start
                s_loc = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                is_done = (rec.state == "done")
                if rec.date_deadline and utc_to_local_dt(self.env, rec.date_deadline, user_tz=user_tz) > s_loc:
                    e_loc = utc_to_local_dt(self.env, rec.date_deadline, user_tz=user_tz)
                else:
                    e_loc = s_loc + timedelta(days=1)
                has_explicit_end = bool(rec.date_deadline or rec.scheduled_date)
                prog = 1.0 if is_done else (0.5 if rec.state == "assigned" else 0.0)
                color = "#16a34a" if is_done else "#0f766e"
                act_loc = utc_to_local_dt(self.env, rec.date_done, user_tz=user_tz) if (is_done and rec.date_done) else None
                item_id = f"delivery_{rec.id}"
                label = f"{rec.name} - {rec.partner_id.name}" if rec.partner_id else rec.name

            has_deadline_delay = False
            deadline_str = False
            deadline_end_str = False
            delay_kind = ""
            delay_days = 0
            delay_hours = 0.0
            delay_minutes = 0
            has_early_finish = False
            early_minutes = 0
            early_hours = 0.0
            early_days = 0
            actual_finish_str = False

            if is_done and act_loc:
                if act_loc > e_loc:
                    has_deadline_delay = True
                    delay_kind = "late"
                    deadline_str = act_loc.strftime(dt_format)
                    deadline_end_str = deadline_str
                    delay_minutes = ceil_minutes_between(e_loc, act_loc)
                    delay_hours = round(delay_minutes / 60.0, 1)
                    delay_days = (act_loc.date() - e_loc.date()).days if act_loc.date() > e_loc.date() else 0
                elif act_loc < e_loc and (e_loc.date() - act_loc.date()).days > 0:
                    has_early_finish = True
                    early_minutes = ceil_minutes_between(act_loc, e_loc)
                    early_hours = round(early_minutes / 60.0, 1)
                    early_days = (e_loc.date() - act_loc.date()).days
                    actual_finish_str = act_loc.strftime(dt_format)

            is_overdue_past = bool(not is_done and has_explicit_end and e_loc < now_loc_supply)

            gantt_s = datetime.combine(s_loc.date(), time.min)
            gantt_e = datetime.combine(e_loc.date() + timedelta(days=1), time.min) if e_loc.time() > time.min else datetime.combine(e_loc.date(), time.min)
            if gantt_e <= gantt_s:
                gantt_e = gantt_s + timedelta(days=1)

            return {
                "id": item_id,
                "odoo_id": rec.id,
                "text": label,
                "type": "task",
                "open": False,
                "parent": parent_key,
                "start_date": gantt_s.strftime(dt_format),
                "end_date": gantt_e.strftime(dt_format),
                "work_start_date": s_loc.strftime(dt_format),
                "work_end_date": e_loc.strftime(dt_format),
                "date_deadline": deadline_str or e_loc.strftime(dt_format),
                "deadline_end": deadline_end_str,
                "has_deadline": bool(has_explicit_end),
                "has_deadline_delay": has_deadline_delay,
                "delay_kind": delay_kind,
                "delay_days": delay_days,
                "delay_hours": delay_hours,
                "delay_minutes": delay_minutes,
                "has_early_finish": has_early_finish,
                "early_minutes": early_minutes,
                "early_hours": early_hours,
                "early_days": early_days,
                "actual_finish_date": actual_finish_str,
                "is_overdue_past": is_overdue_past,
                "is_po": kind == "po",
                "is_mo": kind == "mo",
                "is_delivery": kind == "delivery",
                "is_project": False,
                "is_milestone": False,
                "is_group": False,
                "readonly": True,
                "color": color,
                "state": rec.state,
                "is_done": is_done,
                "progress": prog,
                "progress_percent": int(prog * 100),
                "assignees": rec_user.name if rec_user else "",
                "assignee_avatars": [{
                    "id": rec_user.id,
                    "name": rec_user.name,
                    "avatar": f"/web/image/res.users/{rec_user.id}/avatar_128",
                }] if rec_user else [],
                "project_id": rec_proj.id if rec_proj else False,
                "project_name": rec_proj.name if rec_proj else "Be projekto",
            }

        if is_only_mo or is_only_po or is_only_delivery:
            gantt_tasks = []
            if is_only_mo and has_mrp:
                mo_model = self.env["mrp.production"]
                mo_has_proj = "project_id" in mo_model._fields
                mo_has_user = "user_id" in mo_model._fields
                for mo in mo_model.search([("state", "!=", "cancel")], order="date_start, id", limit=2000):
                    proj_r = mo.project_id if mo_has_proj else False
                    if proj_r and not proj_r.has_access("read"):
                        continue
                    gantt_tasks.append(_build_supply_gantt_item("mo", mo, None, now_utc, proj_r, mo.user_id if mo_has_user else False))
            elif is_only_po and has_purchase:
                po_model = self.env["purchase.order"]
                po_has_proj = "project_id" in po_model._fields
                po_has_user = "user_id" in po_model._fields
                for po in po_model.search([("state", "!=", "cancel")], order="date_order, id", limit=2000):
                    proj_r = po.project_id if po_has_proj else False
                    if proj_r and not proj_r.has_access("read"):
                        continue
                    gantt_tasks.append(_build_supply_gantt_item("po", po, None, now_utc, proj_r, po.user_id if po_has_user else False))
            elif is_only_delivery and has_delivery:
                pick_model = self.env["stock.picking"]
                pick_has_proj = "project_id" in pick_model._fields
                pick_has_user = "user_id" in pick_model._fields
                for pick in pick_model.search([("picking_type_code", "=", "outgoing"), ("state", "!=", "cancel")], order="scheduled_date, id", limit=2000):
                    proj_r = pick.project_id if pick_has_proj else False
                    if proj_r and not proj_r.has_access("read"):
                        continue
                    gantt_tasks.append(_build_supply_gantt_item("delivery", pick, None, now_utc, proj_r, pick.user_id if pick_has_user else False))

            all_projects = self.env["project.project"].search([], order="name", limit=500)
            project_list = [{"id": p.id, "name": p.name} for p in all_projects]
            return {
                "tasks": gantt_tasks,
                "links": [],
                "projects": project_list,
                "can_edit": False,
                "can_track_time": can_track_time,
                "has_mrp": has_mrp,
                "has_purchase": has_purchase,
                "has_delivery": has_delivery,
            }

        project_model = self.env["project.project"]
        task_model = self.env["project.task"]

        explicit_proj_id = None
        if project_id and project_id not in ("overdue", "only_mo", "only_po", "only_delivery"):
            try:
                p_val = int(project_id)
                if p_val > 0:
                    explicit_proj_id = p_val
            except (ValueError, TypeError):
                explicit_proj_id = None

        user_id_int = 0
        if user_id and not isinstance(user_id, bool):
            try:
                u_val = int(user_id)
                if u_val > 0:
                    user_id_int = u_val
            except (ValueError, TypeError):
                user_id_int = 0

        is_project_model = (model_name == "project.project")

        proj_domain = []
        task_domain = [("active", "=", True)]

        if explicit_proj_id:
            proj_domain.append(("id", "=", explicit_proj_id))
            task_domain.append(("project_id", "=", explicit_proj_id))

        if user_id_int > 0:
            task_domain.append(("user_ids", "in", [user_id_int]))

        if date_start:
            try:
                ds_dt = fields.Datetime.to_datetime(date_start) if isinstance(date_start, str) else date_start
                if ds_dt:
                    task_domain.extend(["|", ("planned_date_end", ">=", ds_dt), ("planned_date_end", "=", False)])
            except (ValueError, TypeError):
                pass
        if date_end:
            try:
                de_dt = fields.Datetime.to_datetime(date_end) if isinstance(date_end, str) else date_end
                if de_dt:
                    task_domain.extend(["|", ("planned_date_start", "<=", de_dt), ("planned_date_start", "=", False)])
            except (ValueError, TypeError):
                pass

        projects = project_model.browse([])
        tasks = task_model.browse([])

        # Clean domain for timeline: remove flat-view restrictions (display_in_project, parent_id=False)
        # so all subtasks and nested children to unlimited depth are loaded in the tree
        def _clean_timeline_domain(dom):
            if not dom:
                return []
            cleaned = []
            for leaf in dom:
                if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                    field_name = leaf[0]
                    if field_name == "display_in_project":
                        continue
                    if field_name == "parent_id" and leaf[1] in ("=", "==") and (leaf[2] is False or leaf[2] is None):
                        continue
                cleaned.append(leaf)
            return cleaned

        cleaned_domain = _clean_timeline_domain(domain)

        if cleaned_domain:
            if is_project_model:
                try:
                    projects = project_model.search(proj_domain + cleaned_domain, limit=200)
                except Exception as e:
                    _logger.warning("Error searching projects with domain %s: %s", domain, e)
                    projects = project_model.browse([])

                if projects:
                    if not explicit_proj_id:
                        task_domain.append(("project_id", "in", projects.ids))
                    tasks = task_model.search(task_domain, order="project_id, sequence, id", limit=5000)
                else:
                    tasks = task_model.browse([])
            else:
                try:
                    tasks = task_model.search(task_domain + cleaned_domain, order="project_id, sequence, id", limit=5000)
                except Exception as e:
                    _logger.warning("Domain %s failed on task search: %s. Trying project search fallback.", domain, e)
                    try:
                        projects = project_model.search(proj_domain + cleaned_domain, limit=200)
                        if projects:
                            if not explicit_proj_id:
                                task_domain.append(("project_id", "in", projects.ids))
                            tasks = task_model.search(task_domain, order="project_id, sequence, id", limit=5000)
                        else:
                            tasks = task_model.browse([])
                    except Exception as e2:
                        _logger.warning("Domain failed on both models: %s", e2)
                        projects = project_model.browse([])
                        tasks = task_model.browse([])
        else:
            if is_project_model:
                projects = project_model.search(proj_domain, limit=200)
                if projects:
                    if not explicit_proj_id:
                        task_domain.append(("project_id", "in", projects.ids))
                    tasks = task_model.search(task_domain, order="project_id, sequence, id", limit=5000)
                else:
                    tasks = task_model.browse([])
            else:
                tasks = task_model.search(task_domain, order="project_id, sequence, id", limit=5000)

        # Recursively ensure all child tasks, sub-subtasks, and deeper nested items are included
        if tasks:
            all_task_ids = set(tasks.ids)
            curr_parent_ids = list(all_task_ids)
            while curr_parent_ids:
                new_children = task_model.search([
                    ("parent_id", "in", curr_parent_ids),
                    ("id", "not in", list(all_task_ids)),
                    ("active", "=", True),
                ], order="project_id, sequence, id")
                if not new_children:
                    break
                tasks = tasks | new_children
                all_task_ids.update(new_children.ids)
                curr_parent_ids = new_children.ids

        # Gather relevant projects if not already gathered
        if not projects:
            if explicit_proj_id:
                projects = project_model.search([("id", "=", explicit_proj_id)])
            elif tasks:
                project_ids = [pid for pid in tasks.mapped("project_id.id") if pid]
                if project_ids:
                    projects = project_model.search([("id", "in", project_ids)])
            elif not cleaned_domain and not user_id_int and not is_only_overdue:
                projects = project_model.search([], limit=20)

        if not projects and not tasks and (cleaned_domain or explicit_proj_id or is_only_overdue or user_id_int or not has_mrp):
            all_projects = project_model.search([], order="name", limit=500)
            return {
                "tasks": [],
                "links": [],
                "projects": [{"id": p.id, "name": p.name} for p in all_projects],
                "can_edit": can_edit,
                "can_track_time": can_track_time,
                "has_mrp": has_mrp,
                "has_purchase": has_purchase,
                "has_delivery": has_delivery,
            }

        # Pre-index task children once (avoids O(T^2) tasks.filtered scans)
        children_by_parent_id = {}
        for t in tasks:
            if t.parent_id:
                children_by_parent_id.setdefault(t.parent_id.id, []).append(t)

        # Build Gantt items list
        gantt_tasks = []
        links = []
        seen_project_keys = set()
        seen_milestone_keys = set()
        added_task_ids = set()
        user_task_counts = {}

        now = fields.Datetime.now()
        now_local = utc_to_local_dt(self.env, now, user_tz=user_tz)

        # Query milestones for projects
        milestone_model = self.env["project.milestone"]
        milestones = milestone_model.browse([])
        if projects:
            try:
                milestones = milestone_model.search([("project_id", "in", projects.ids)], order="project_id, sequence, id")
            except Exception as e:
                _logger.warning("Error fetching milestones: %s", e)

        # Hierarchical task dates helper: container CANNOT be smaller than items inside it
        task_dates_cache = {}
        visiting_tasks = set()

        def get_task_base_dates(t):
            """Returns the task's own base dates without recursing into subtasks."""
            if t.planned_date_start:
                s = utc_to_local_dt(self.env, t.planned_date_start, user_tz=user_tz)
                if s.hour == 0 and s.minute == 0 and not t.plan_is_auto:
                    if not t.planned_date_end or utc_to_local_dt(self.env, t.planned_date_end, user_tz=user_tz) >= s.replace(hour=8, minute=0, second=0):
                        s = s.replace(hour=8, minute=0, second=0)
            else:
                base_dt = utc_to_local_dt(self.env, t.date_assign or t.create_date or now, user_tz=user_tz)
                s = datetime.combine(base_dt.date(), time(8, 0, 0))

            if t.planned_date_end:
                e = utc_to_local_dt(self.env, t.planned_date_end, user_tz=user_tz)
                if (e.hour == 0 and e.minute == 0) or (e.hour == 23 and e.minute == 59):
                    if not t.plan_is_auto:
                        e = e.replace(hour=17, minute=0, second=0)
            elif t.is_timer_running and t.planned_date_start:
                e = s + timedelta(minutes=ceil_minutes_between(s, now_local))
            elif t.date_deadline:
                e = utc_to_local_dt(self.env, t.date_deadline, user_tz=user_tz) if isinstance(t.date_deadline, datetime) else datetime.combine(t.date_deadline, time(17, 0, 0))
                if (e.hour == 0 and e.minute == 0) or (e.hour == 23 and e.minute == 59):
                    e = e.replace(hour=17, minute=0, second=0)
            else:
                e = datetime.combine(s.date(), time(17, 0, 0))

            if e < s:
                e = s + timedelta(minutes=1)
            return s, e

        def get_task_dates(t):
            if t.id in task_dates_cache:
                return task_dates_cache[t.id]
            if t.id in visiting_tasks:
                return get_task_base_dates(t)

            visiting_tasks.add(t.id)
            try:
                # If this is a subtask without explicit planned dates, inherit strictly from parent task
                if t.parent_id and not t.planned_date_start and not t.planned_date_end:
                    p_s, p_e = get_task_dates(t.parent_id)
                    t_s, t_e = p_s, p_e
                    task_dates_cache[t.id] = (t_s, t_e)
                    return t_s, t_e

                t_s, t_e = get_task_base_dates(t)

                if t.parent_id:
                    # If start or end is not set, inherit from parent
                    if not t.planned_date_start:
                        p_s, _ = get_task_dates(t.parent_id)
                        t_s = p_s
                    if not t.planned_date_end and not t.date_deadline:
                        _, p_e = get_task_dates(t.parent_id)
                        t_e = max(t_s, p_e)

                # Encompass subtasks ONLY for parents without a manual plan (auto plan / no plan).
                # A parent with a manual plan keeps its own span; subtasks beyond it make a red tail instead.
                subtasks = children_by_parent_id.get(t.id, [])
                manual_parent_plan = bool(t.planned_date_start and t.planned_date_end) and not t.plan_is_auto
                if subtasks and not manual_parent_plan:
                    sub_pairs = [get_task_dates(sub) for sub in subtasks if sub.id not in visiting_tasks]
                    if sub_pairs:
                        t_s = min(t_s, min(p[0] for p in sub_pairs))
                        t_e = max(t_e, max(p[1] for p in sub_pairs))

                if t_e < t_s:
                    t_e = datetime.combine(t_s.date(), time(17, 0, 0))
                    if t_e <= t_s:
                        t_e = datetime.combine(t_s.date() + timedelta(days=1), time(17, 0, 0))

                task_dates_cache[t.id] = (t_s, t_e)
                return t_s, t_e
            finally:
                visiting_tasks.discard(t.id)

        # Hierarchical progress calculation helper (Odoo 19 progress scale: 0.0..1.0)
        task_metrics_cache = {}
        visiting_metrics = set()

        def get_task_metrics(t):
            if t.id in task_metrics_cache:
                return task_metrics_cache[t.id]
            if t.id in visiting_metrics:
                return {"is_done": False, "is_approved": False, "progress": 0.0}

            visiting_metrics.add(t.id)
            try:
                subtasks = children_by_parent_id.get(t.id, [])
                if subtasks:
                    # Parent task with subtasks: progress strictly depends on bottom subtasks!
                    # A parent CANNOT be 100% if bottom tasks are not finished!
                    sub_results = [get_task_metrics(s) for s in subtasks]
                    all_subs_done = all(sr["is_done"] for sr in sub_results)
                    all_subs_approved_or_done = all(sr["is_done"] or sr.get("is_approved", False) for sr in sub_results)
                    avg_sub_prog = sum(sr["progress"] for sr in sub_results) / len(sub_results)

                    if all_subs_done and len(subtasks) > 0:
                        res = {"is_done": True, "is_approved": False, "progress": 1.0}
                    elif all_subs_approved_or_done and len(subtasks) > 0:
                        res = {"is_done": False, "is_approved": True, "progress": 1.0}
                    else:
                        # Not all bottom tasks are finished: cannot be 100% or done!
                        res = {"is_done": False, "is_approved": False, "progress": min(0.99, round(avg_sub_prog, 2))}
                else:
                    # Leaf task (no subtasks): its own completion state in Odoo
                    stage_name = (t.stage_id.name or "").lower() if t.stage_id else ""
                    is_done = bool(
                        t.state == "1_done"
                        or getattr(t.stage_id, "is_closed", False)
                        or "done" in stage_name
                        or "completed" in stage_name
                        or "closed" in stage_name
                    )
                    is_approved = bool(t.state == "03_approved")
                    if is_done:
                        res = {"is_done": True, "is_approved": False, "progress": 1.0}
                    elif is_approved:
                        res = {"is_done": False, "is_approved": True, "progress": 1.0}
                    else:
                        p = float(t.progress) if t.progress else 0.0
                        if p == 0.0 and t.allocated_hours and t.effective_hours:
                            p = min(0.99, t.effective_hours / t.allocated_hours)
                        res = {"is_done": False, "is_approved": False, "progress": min(0.99, max(0.0, p))}

                task_metrics_cache[t.id] = res
                return res
            finally:
                visiting_metrics.discard(t.id)

        def get_task_effective_deadline(task_obj):
            """Local datetime of the task's deadline (typed by a user or auto-written), or None."""
            if task_obj.date_deadline:
                dl_loc = utc_to_local_dt(self.env, task_obj.date_deadline, user_tz=user_tz) if isinstance(task_obj.date_deadline, datetime) else datetime.combine(task_obj.date_deadline, time(17, 0, 0))
                if not task_obj.deadline_is_auto and ((dl_loc.hour == 0 and dl_loc.minute == 0) or (dl_loc.hour == 23 and dl_loc.minute == 59)):
                    dl_loc = dl_loc.replace(hour=17, minute=0, second=0)
                return dl_loc
            return None

        def has_manual_plan(task_obj):
            return bool(task_obj.planned_date_start and task_obj.planned_date_end) and not task_obj.plan_is_auto

        delay_cache = {}
        visiting_delays = set()

        def compute_task_delay(task_obj):
            """
            Red tail ("vėlavimas") = from the planned end up to the deadline (deadline LATER than the plan end).
            Returns dict(deadline, kind, seconds, days, end):
              kind "late"  -> a stored deadline (typed, or auto-written when work went past the plan) is after the plan end
              kind "timer" -> the running timer is already past the planned end (live, not saved yet)
              kind ""      -> no tail
              end          -> effective end incl. the tail and all descendants (used by parents)
            """
            if task_obj.id in delay_cache:
                return delay_cache[task_obj.id]
            _, t_e = get_task_dates(task_obj)
            if task_obj.id in visiting_delays:
                return {
                    "deadline": None,
                    "kind": "",
                    "seconds": 0.0,
                    "days": 0,
                    "end": t_e,
                    "has_early_finish": False,
                    "early_minutes": 0,
                    "early_hours": 0.0,
                    "early_days": 0,
                    "actual_finish_date": None,
                }
            visiting_delays.add(task_obj.id)
            try:
                own_dl = get_task_effective_deadline(task_obj)
                children = children_by_parent_id.get(task_obj.id, [])
                manual = has_manual_plan(task_obj)
                eff_end = t_e
                kind = ""
                deadline_out = own_dl

                has_plan = manual or bool(task_obj.parent_id) or bool(children)
                if task_obj.state == "1_canceled":
                    allow_own_dl = not task_obj.deadline_is_auto
                else:
                    allow_own_dl = (not task_obj.deadline_is_auto) or bool(
                        task_obj.first_timer_start
                        or task_obj.timer_total_seconds
                        or task_obj.effective_hours
                        or task_obj.state in ("03_approved", "1_done", "02_changes_requested")
                    )
                if own_dl and own_dl > eff_end and has_plan and allow_own_dl:
                    eff_end = own_dl
                    kind = "late"
                if task_obj.is_timer_running and task_obj.state != "1_canceled" and has_plan and now_local > eff_end:
                    eff_end = floor_minute(now_local) + timedelta(minutes=1)
                    kind = "timer"
                non_canceled_children = [c for c in children if c.state != "1_canceled"]
                child_delay_results = []
                for child in non_canceled_children:
                    cres = compute_task_delay(child)
                    child_delay_results.append((child, cres))
                    if cres["end"] > eff_end:
                        eff_end = cres["end"]
                        kind = cres["kind"] or "late"

                seconds = 0.0
                days = 0
                if kind:
                    deadline_out = eff_end
                    seconds = float(ceil_minutes_between(t_e, eff_end) * 60)
                    days = (eff_end.date() - t_e.date()).days if eff_end.date() > t_e.date() else 0

                # Early Finish Calculation (Lateness strictly dominates!)
                has_early_finish = False
                early_minutes = 0
                early_hours = 0.0
                early_days = 0
                actual_finish_dt = None

                is_task_completed = task_obj.state in ("03_approved", "1_done")
                if not kind and is_task_completed:
                    if children:
                        all_subs_finished = bool(
                            non_canceled_children and all(
                                c.state in ("03_approved", "1_done") for c in non_canceled_children
                            )
                        )
                        if all_subs_finished:
                            finish_dts = []
                            for child, cres in child_delay_results:
                                if cres.get("kind") and cres.get("end"):
                                    finish_dts.append(cres["end"])
                                elif cres.get("actual_finish_date"):
                                    finish_dts.append(cres["actual_finish_date"])
                                elif child.date_deadline and (not child.planned_date_end or child.date_deadline > child.planned_date_end):
                                    finish_dts.append(utc_to_local_dt(self.env, child.date_deadline, user_tz=user_tz))
                                elif child.actual_finish_date:
                                    finish_dts.append(utc_to_local_dt(self.env, child.actual_finish_date, user_tz=user_tz))
                                elif child.date_end:
                                    finish_dts.append(utc_to_local_dt(self.env, child.date_end, user_tz=user_tz))
                                elif child.planned_date_end:
                                    finish_dts.append(utc_to_local_dt(self.env, child.planned_date_end, user_tz=user_tz))

                            if finish_dts:
                                max_f = max(finish_dts)
                                if max_f < t_e:
                                    has_early_finish = True
                                    actual_finish_dt = max_f
                                    early_minutes = ceil_minutes_between(max_f, t_e)
                                    early_hours = round(early_minutes / 60.0, 1)
                                    early_days = (t_e.date() - max_f.date()).days if t_e.date() > max_f.date() else 0
                    else:
                        leaf_act = task_obj.actual_finish_date or task_obj.date_end
                        if leaf_act:
                            leaf_act_loc = utc_to_local_dt(self.env, leaf_act, user_tz=user_tz)
                            if leaf_act_loc < t_e:
                                has_early_finish = True
                                actual_finish_dt = leaf_act_loc
                                early_minutes = ceil_minutes_between(leaf_act_loc, t_e)
                                early_hours = round(early_minutes / 60.0, 1)
                                early_days = (t_e.date() - leaf_act_loc.date()).days if t_e.date() > leaf_act_loc.date() else 0

                res = {
                    "deadline": deadline_out,
                    "kind": kind,
                    "seconds": seconds,
                    "days": days,
                    "end": eff_end,
                    "has_early_finish": has_early_finish,
                    "early_minutes": early_minutes,
                    "early_hours": early_hours,
                    "early_days": early_days,
                    "actual_finish_date": actual_finish_dt,
                }
                delay_cache[task_obj.id] = res
                return res
            finally:
                visiting_delays.discard(task_obj.id)

        def compute_container_delay(own_dl, c_end, c_is_done, child_tasks):
            """Tail for a project/milestone: its own deadline after its end, or the latest task tail."""
            eff_end = c_end
            kind = ""
            if own_dl and c_end and own_dl > c_end:
                eff_end = own_dl
                kind = "late"
            for ct in child_tasks:
                if ct.state == "1_canceled":
                    continue
                cres = compute_task_delay(ct)
                if c_end and cres["end"] > eff_end:
                    eff_end = cres["end"]
                    kind = cres["kind"] or "late"
            res = {"deadline": own_dl, "kind": kind, "seconds": 0.0, "days": 0}
            if kind:
                res["deadline"] = eff_end
                res["seconds"] = float(ceil_minutes_between(c_end, eff_end) * 60)
                res["days"] = (eff_end.date() - c_end.date()).days if eff_end.date() > c_end.date() else 0
            return res

        can_manage_any = self._has_timer_override_rights()

        def build_task_dict(t, parent_key):
            metrics = get_task_metrics(t)
            t_is_done = metrics["is_done"]
            prog = metrics["progress"]

            t_start, t_end = get_task_dates(t)
            subtasks = children_by_parent_id.get(t.id, [])
            t_state = t.state or "01_in_progress"
            is_t_continuous = bool(
                not t_is_done
                and t_state not in ("1_done", "03_approved", "1_canceled")
                and not t.planned_date_end
                and not t.date_deadline
                and not subtasks
                and (not t.parent_id or not t.parent_id.planned_date_end)
            )
            if is_t_continuous:
                today_eod = now_local.replace(hour=17, minute=0, second=0, microsecond=0)
                if today_eod > t_end:
                    t_end = today_eod
            has_dates = bool((t.planned_date_start and t.planned_date_end) or t.date_deadline or t.parent_id or subtasks or is_t_continuous)

            # Auto-fill display hours from darbo valandos (working hours) if unset or 0.0
            if not has_dates:
                t_alloc = 0.0
                allocated_str = ""
            else:
                w_h = calculate_lithuanian_working_hours(t_start, t_end, holidays_cache=holidays_cache)
                t_alloc = t.allocated_hours if (t.allocated_hours and t.allocated_hours > 0) else (w_h if w_h > 0 else 0.0)
                allocated_str = format_hours_label(t_alloc)

            # Grid alignment for DHTMLX Gantt visual coordinates (midnight boundaries)
            gantt_start = datetime.combine(t_start.date(), time.min)
            if t_end.date() > t_start.date():
                if t_end.time() > time.min:
                    gantt_end = datetime.combine(t_end.date() + timedelta(days=1), time.min)
                else:
                    gantt_end = datetime.combine(t_end.date(), time.min)
            else:
                # Exactly span of one day on Gantt grid (midnight to next midnight)
                gantt_end = gantt_start + timedelta(days=1)

            t_start_str = gantt_start.strftime(dt_format)
            t_end_str = gantt_end.strftime(dt_format)
            work_start_str = t_start.strftime(dt_format)
            work_end_str = t_end.strftime(dt_format)

            # Assignees info
            assignee_names = ", ".join(t.user_ids.mapped("name"))
            avatars = []
            for u in t.user_ids:
                avatars.append({
                    "id": u.id,
                    "name": u.name,
                    "avatar": f"/web/image/res.users/{u.id}/avatar_128",
                })
                user_task_counts[u.id] = user_task_counts.get(u.id, 0) + 1

            # Check for uncompleted dependencies (03_approved and 1_done both satisfy dependency)
            is_locked = False
            blocking_tasks = []
            if t.depend_on_ids:
                for dep in t.depend_on_ids:
                    dep_m = get_task_metrics(dep)
                    if not dep_m.get("is_done") and not dep_m.get("is_approved") and dep.state not in ("1_done", "03_approved"):
                        is_locked = True
                        blocking_tasks.append(dep.name or f"Task #{dep.id}")
            elif getattr(t, "depend_on_count", 0) > getattr(t, "closed_depend_on_count", 0):
                is_locked = True

            # If parent task is locked or not finished and this is a subtask
            if not is_locked and t.parent_id:
                parent_m = get_task_metrics(t.parent_id)
                if not parent_m.get("is_done") and not parent_m.get("is_approved") and t.parent_id.state not in ("1_done", "03_approved"):
                    if t.parent_id.depend_on_ids:
                        for pdep in t.parent_id.depend_on_ids:
                            pdep_m = get_task_metrics(pdep)
                            if not pdep_m.get("is_done") and not pdep_m.get("is_approved") and pdep.state not in ("1_done", "03_approved"):
                                is_locked = True
                                blocking_tasks.append(pdep.name or f"Task #{pdep.id}")

            t_state = t.state or "01_in_progress"

            # Purely read-only visual mapping (no DB writes during timeline reads)
            if is_locked:
                prog = 0.0
                t_is_done = False
                color = "#1e293b"  # Dark Charcoal Steel for locked tasks
                t_state = "locked"
            else:
                if t_state == "04_waiting_normal":
                    t_state = "01_in_progress"

                if subtasks and t_state in ("1_done", "03_approved") and not (t_is_done or metrics.get("is_approved")):
                    t_state = "01_in_progress"

                if t_state == "1_canceled":
                    color = "#dc3545"  # Cancelled: red
                elif t_is_done or (not subtasks and t_state == "1_done"):
                    color = "#16a34a"  # Done / Atlikta: green with white border & checkmark
                    t_state = "1_done"
                    t_is_done = True
                    prog = 1.0
                elif metrics.get("is_approved") or (not subtasks and t_state == "03_approved"):
                    color = "#10b981"  # Approved: just green (no checkmark, no white border)
                    t_state = "03_approved"
                    t_is_done = False
                    prog = 1.0
                elif t_state == "02_changes_requested":
                    color = "#f59e0b"  # Changes Requested: orange
                elif t_state == "01_in_progress":
                    color = "#71639e"  # In Progress: Purple
                else:
                    color = "#71639e"
                    t_state = "01_in_progress"

            allocated_str = format_hours_label(t_alloc)

            # Deadline and Delay Calculation (see compute_task_delay)
            delay_info = compute_task_delay(t)
            dl_dt = delay_info["deadline"]
            deadline_str = dl_dt.strftime(dt_format) if dl_dt else False
            deadline_end_str = deadline_str
            has_deadline = bool(dl_dt)
            has_deadline_delay = bool(delay_info["kind"])
            delay_kind = delay_info["kind"]
            delay_days = delay_info["days"]
            delay_hours = round(delay_info["seconds"] / 3600.0, 1)
            delay_minutes = int(round(delay_info["seconds"] / 60.0))

            has_early_finish = bool(delay_info.get("has_early_finish"))
            early_minutes = int(delay_info.get("early_minutes", 0))
            early_hours = float(delay_info.get("early_hours", 0.0))
            early_days = int(delay_info.get("early_days", 0))
            act_finish_dt = delay_info.get("actual_finish_date")
            actual_finish_date_str = act_finish_dt.strftime(dt_format) if act_finish_dt else False

            p_start_loc = utc_to_local_dt(self.env, t.planned_date_start, user_tz=user_tz) if t.planned_date_start else False
            p_end_loc = utc_to_local_dt(self.env, t.planned_date_end, user_tz=user_tz) if t.planned_date_end else False

            is_locked_by_other = bool(
                (t.is_timer_running or t.is_paused) and t.active_user_id and t.active_user_id.id != self.env.uid and not can_manage_any
            )

            res = {
                "id": f"task_{t.id}",
                "odoo_id": t.id,
                "text": t.name or "Untitled Task",
                "type": "task",
                "open": False,
                "parent": parent_key,
                "start_date": t_start_str,
                "end_date": t_end_str,
                "work_start_date": work_start_str,
                "work_end_date": work_end_str,
                "planned_date_start": p_start_loc.strftime(dt_format) if p_start_loc else False,
                "planned_date_end": p_end_loc.strftime(dt_format) if p_end_loc else False,
                "date_deadline": deadline_str,
                "deadline_end": deadline_end_str,
                "has_deadline": has_deadline,
                "has_deadline_delay": has_deadline_delay,
                "delay_kind": delay_kind,
                "has_own_deadline": bool(t.date_deadline),
                "delay_minutes": delay_minutes,
                "has_early_finish": has_early_finish,
                "early_minutes": early_minutes,
                "early_hours": early_hours,
                "early_days": early_days,
                "actual_finish_date": actual_finish_date_str,
                "plan_is_manual": bool(t.planned_date_start and t.planned_date_end) and not t.plan_is_auto,
                "deadline_is_auto": bool(t.deadline_is_auto),
                "timer_total_seconds": int(t.timer_total_seconds or 0),
                "delay_days": delay_days,
                "delay_hours": delay_hours,
                "has_dates": has_dates,
                "is_hourly": bool(has_dates and t_start.date() == t_end.date() and (t_end - t_start).total_seconds() < 9 * 3600),
                "is_timer_running": bool(t.is_timer_running),
                "timer_start": utc_to_local_dt(self.env, t.timer_start, user_tz=user_tz).strftime(dt_format) if t.timer_start else False,
                "has_been_approved": bool(t.has_been_approved),
                "approved_baseline_duration": float(t.approved_baseline_duration or 0.0),
                "is_paused": bool(t.is_paused),
                "last_pause_reason": t.last_pause_reason or "",
                "active_user_id": t.active_user_id.id if t.active_user_id else False,
                "active_user_name": t.active_user_id.name if t.active_user_id else "",
                "is_locked_by_other": is_locked_by_other,
                "progress": 0.0 if is_locked else round(prog, 2),
                "progress_percent": 0 if is_locked else round(prog * 100, 1),
                "allocated_hours": allocated_str,
                "allocated_hours_raw": t_alloc or 0.0,
                "effective_hours": f"{round(t.effective_hours, 1)}h" if t.effective_hours else "",
                "assignees": assignee_names,
                "assignee_avatars": avatars,
                "stage_name": t.stage_id.name if t.stage_id else "",
                "stage_id": t.stage_id.id if t.stage_id else False,
                "project_name": (t.project_id.name if t.project_id else (t.parent_id.project_id.name if t.parent_id and t.parent_id.project_id else "")),
                "project_id": (t.project_id.id if t.project_id else (t.parent_id.project_id.id if t.parent_id and t.parent_id.project_id else False)),
                "milestone_id": (t.milestone_id.id if t.milestone_id else (t.parent_id.milestone_id.id if t.parent_id and t.parent_id.milestone_id else False)),
                "milestone_name": (t.milestone_id.name if t.milestone_id else (t.parent_id.milestone_id.name if t.parent_id and t.parent_id.milestone_id else "")),
                "color": color,
                "state": t_state,
                "is_locked": is_locked,
                "blocking_tasks": ", ".join(blocking_tasks),
                "is_overdue_past": bool(
                    not is_t_continuous
                    and not t_is_done
                    and t_state not in ("1_done", "03_approved", "1_canceled")
                    and prog < 1.0
                    and (
                        (has_dates and t_end < now_local)
                        or (t.date_deadline and utc_to_local_dt(self.env, t.date_deadline, user_tz=user_tz) < now_local)
                    )
                ),
                "is_continuous": is_t_continuous,
                "is_project": False,
                "is_milestone": False,
                "is_done": False if is_locked else bool(t_is_done),
                "readonly": (not can_edit) or is_locked_by_other,
            }
            if not has_dates:
                res["duration"] = 1
            return res

        def add_task_hierarchy(t, parent_key, proj_task_id_set):
            if t.id in added_task_ids:
                return
            gantt_tasks.append(build_task_dict(t, parent_key))
            added_task_ids.add(t.id)

            # Subtasks
            for child in children_by_parent_id.get(t.id, []):
                if child.id in proj_task_id_set and child.id not in added_task_ids:
                    add_task_hierarchy(child, f"task_{t.id}", proj_task_id_set)

        # Pre-bucket tasks & milestones by effective project ID once (memoized with cycle guard)
        task_proj_id_cache = {}

        def _get_task_effective_project_id(t):
            if t.id in task_proj_id_cache:
                return task_proj_id_cache[t.id]
            visited = set()
            chain = []
            curr = t
            found_pid = False
            while curr and curr.id not in visited:
                visited.add(curr.id)
                chain.append(curr.id)
                if curr.project_id:
                    found_pid = curr.project_id.id
                    break
                curr = curr.parent_id
            for tid in chain:
                task_proj_id_cache[tid] = found_pid
            return found_pid

        tasks_by_proj_id = {}
        for t in tasks:
            pid = _get_task_effective_project_id(t)
            if pid:
                tasks_by_proj_id.setdefault(pid, []).append(t)

        milestones_by_proj_id = {}
        for m in milestones:
            if m.project_id:
                milestones_by_proj_id.setdefault(m.project_id.id, []).append(m)

        # Batch-fetch POs, MOs, and Deliveries across all projects (ACL-checked, NO .sudo())
        proj_id_set = set(projects.ids)
        pos_by_proj_id = {}
        if projects and "purchase.order" in self.env:
            po_model = self.env["purchase.order"]
            if po_model.has_access("read") and "project_id" in po_model._fields:
                try:
                    all_pos = po_model.search([
                        ("project_id", "in", projects.ids),
                        ("state", "!=", "cancel"),
                    ], order="date_order, id")
                    for po in all_pos:
                        if po.project_id:
                            pos_by_proj_id.setdefault(po.project_id.id, []).append(po)
                except Exception as e:
                    _logger.warning("Error searching POs for projects: %s", e)

        mos_by_proj_id = {}
        if projects and has_mrp:
            mo_model = self.env["mrp.production"]
            if "project_id" in mo_model._fields:
                try:
                    all_p_mos = mo_model.search([
                        ("project_id", "in", projects.ids),
                        ("state", "!=", "cancel"),
                    ], order="date_start, id")
                    for mo in all_p_mos:
                        if mo.project_id:
                            mos_by_proj_id.setdefault(mo.project_id.id, []).append(mo)
                except Exception as e:
                    _logger.warning("Error searching MOs for projects: %s", e)

        deliveries_by_proj_id = {}
        if projects and "stock.picking" in self.env:
            pick_model = self.env["stock.picking"]
            if pick_model.has_access("read"):
                try:
                    has_pick_proj = "project_id" in pick_model._fields
                    has_pick_sale = "sale_id" in pick_model._fields
                    has_proj_sale = "sale_order_id" in project_model._fields
                    sale_to_proj_ids = {}
                    if has_proj_sale and has_pick_sale:
                        for proj in projects:
                            so = getattr(proj, "sale_order_id", False)
                            if so:
                                sale_to_proj_ids.setdefault(so.id, set()).add(proj.id)
                    deliv_dom = [
                        ("picking_type_code", "=", "outgoing"),
                        ("state", "!=", "cancel"),
                    ]
                    if has_pick_proj and sale_to_proj_ids:
                        deliv_dom += ["|", ("project_id", "in", projects.ids), ("sale_id", "in", list(sale_to_proj_ids.keys()))]
                    elif has_pick_proj:
                        deliv_dom += [("project_id", "in", projects.ids)]
                    elif sale_to_proj_ids:
                        deliv_dom += [("sale_id", "in", list(sale_to_proj_ids.keys()))]
                    else:
                        deliv_dom = None
                    if deliv_dom:
                        all_picks = pick_model.search(deliv_dom, order="scheduled_date, id")
                        for pick in all_picks:
                            matched_pids = set()
                            if has_pick_proj and pick.project_id and pick.project_id.id in proj_id_set:
                                matched_pids.add(pick.project_id.id)
                            if has_pick_sale and pick.sale_id and pick.sale_id.id in sale_to_proj_ids:
                                matched_pids.update(sale_to_proj_ids[pick.sale_id.id])
                            for pid in matched_pids:
                                deliveries_by_proj_id.setdefault(pid, []).append(pick)
                except Exception as e:
                    _logger.warning("Error searching Deliveries for projects: %s", e)

        can_read_proj_stage = bool(
            "stage_id" in project_model._fields
            and self.env.user.has_group("project.group_project_stages")
            and "project.project.stage" in self.env
            and self.env["project.project.stage"].has_access("read")
        )

        # 1. Add project groups, milestones, and nested tasks
        for p in projects:
            p_key = f"proj_{p.id}"
            seen_project_keys.add(p_key)

            p_tasks = task_model.browse([t.id for t in tasks_by_proj_id.get(p.id, [])])
            p_tasks_id_set = set(p_tasks.ids)
            p_milestones = milestone_model.browse([m.id for m in milestones_by_proj_id.get(p.id, [])])

            p_pos = pos_by_proj_id.get(p.id, [])
            p_mos = mos_by_proj_id.get(p.id, [])
            p_deliveries = deliveries_by_proj_id.get(p.id, [])

            # Project Base Dates (use scheduled tasks so an unscheduled task with no dates doesn't stretch a short project to 17:00)
            scheduled_p_tasks = p_tasks.filtered(lambda t: t.planned_date_start or t.planned_date_end or t.date_deadline or t.parent_id)
            span_p_tasks = scheduled_p_tasks or p_tasks
            task_date_pairs = [get_task_dates(t) for t in span_p_tasks] if span_p_tasks else []
            if p.date_start:
                p_start = datetime.combine(p.date_start, datetime.min.time()) if not isinstance(p.date_start, datetime) else utc_to_local_dt(self.env, p.date_start, user_tz=user_tz)
            elif task_date_pairs:
                p_start = min(pair[0] for pair in task_date_pairs)
            else:
                p_start = datetime.combine(utc_to_local_dt(self.env, p.create_date, user_tz=user_tz).date(), datetime.min.time()) if p.create_date else now_local

            if p_start.hour == 0 and p_start.minute == 0:
                p_start = p_start.replace(hour=8, minute=0, second=0)

            is_p_continuous = bool(
                (p.last_update_status or "") != "done"
                and not p.date
                and not getattr(p, "date_deadline", False)
                and not p_milestones
                and not p_pos
                and not p_mos
                and not p_deliveries
                and (
                    not p_tasks
                    or any(
                        not t.planned_date_end
                        and not t.date_deadline
                        and (t.state or "01_in_progress") not in ("1_done", "03_approved", "1_canceled")
                        for t in p_tasks
                    )
                )
            )
            if p.date:
                p_end = datetime.combine(p.date, datetime.max.time().replace(microsecond=0)) if not isinstance(p.date, datetime) else utc_to_local_dt(self.env, p.date, user_tz=user_tz)
            elif getattr(p, "actual_finish_date", False):
                p_end = datetime.combine(p.actual_finish_date, time(17, 0, 0))
            elif task_date_pairs:
                p_end = max(pair[1] for pair in task_date_pairs)
            else:
                p_end = p_start.replace(hour=17, minute=0, second=0)

            if (p_end.hour == 0 and p_end.minute == 0) or (p_end.hour == 12 and p_end.minute == 0) or (p_end.hour == 23 and p_end.minute == 59):
                p_end = p_end.replace(hour=17, minute=0, second=0)

            if is_p_continuous:
                today_eod = now_local.replace(hour=17, minute=0, second=0, microsecond=0)
                if today_eod > p_end:
                    p_end = today_eod

            def _get_po_dates(po_rec):
                s_dt = po_rec.date_order or po_rec.date_approve or po_rec.create_date or p_start
                s = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                if po_rec.date_planned:
                    e = utc_to_local_dt(self.env, po_rec.date_planned, user_tz=user_tz)
                else:
                    e = s + timedelta(days=7)
                if e <= s:
                    e = s + timedelta(days=1)
                return s, e

            def _get_mo_dates(mo_rec):
                s_dt = mo_rec.date_start or mo_rec.create_date or p_start
                s = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                if mo_rec.date_deadline:
                    e = utc_to_local_dt(self.env, mo_rec.date_deadline, user_tz=user_tz)
                elif mo_rec.state == "done" and mo_rec.date_finished:
                    e = utc_to_local_dt(self.env, mo_rec.date_finished, user_tz=user_tz)
                else:
                    e = s + timedelta(days=3)
                if e <= s:
                    e = s + timedelta(days=1)
                return s, e

            def _get_delivery_dates(pick_rec):
                s_dt = pick_rec.scheduled_date or pick_rec.date_done or pick_rec.date_deadline or pick_rec.create_date or p_start
                s = utc_to_local_dt(self.env, s_dt, user_tz=user_tz)
                if pick_rec.date_done and pick_rec.date_done > (pick_rec.scheduled_date or pick_rec.create_date):
                    e = utc_to_local_dt(self.env, pick_rec.date_done, user_tz=user_tz)
                elif pick_rec.date_deadline and utc_to_local_dt(self.env, pick_rec.date_deadline, user_tz=user_tz) > s:
                    e = utc_to_local_dt(self.env, pick_rec.date_deadline, user_tz=user_tz)
                else:
                    e = s + timedelta(days=1)
                if e <= s:
                    e = s + timedelta(days=1)
                return s, e

            # Section 1: Tasks group span
            if task_date_pairs:
                tg_s = min(pair[0] for pair in task_date_pairs)
                tg_e = max(pair[1] for pair in task_date_pairs)
            else:
                tg_s = p_start
                tg_e = p_end

            for m in p_milestones:
                m_start_val = getattr(m, "date_start", False)
                if m_start_val:
                    m_s = datetime.combine(m_start_val, time(8, 0, 0)) if not isinstance(m_start_val, datetime) else utc_to_local_dt(self.env, m_start_val, user_tz=user_tz)
                    if m_s < tg_s:
                        tg_s = m_s
                m_end_val = getattr(m, "planned_date_end", False) or m.deadline
                if m_end_val:
                    m_dl = datetime.combine(m_end_val, time(17, 0, 0)) if not isinstance(m_end_val, datetime) else utc_to_local_dt(self.env, m_end_val, user_tz=user_tz)
                    if m_dl > tg_e:
                        tg_e = m_dl

            # Section 2: PO group span
            if p_pos:
                po_pairs = [_get_po_dates(po) for po in p_pos]
                pog_s = min(pair[0] for pair in po_pairs)
                pog_e = max(pair[1] for pair in po_pairs)
            else:
                pog_s = p_start
                pog_e = p_end

            # Section 3: MO group span
            if p_mos:
                mo_pairs = [_get_mo_dates(mo) for mo in p_mos]
                mog_s = min(pair[0] for pair in mo_pairs)
                mog_e = max(pair[1] for pair in mo_pairs)
            else:
                mog_s = p_start
                mog_e = p_end

            # Section 4: Delivery group span
            if p_deliveries:
                del_pairs = [_get_delivery_dates(pick) for pick in p_deliveries]
                delg_s = min(pair[0] for pair in del_pairs)
                delg_e = max(pair[1] for pair in del_pairs)
            else:
                delg_s = p_start
                delg_e = p_end

            # A PROJECT CANNOT BE SMALLER THAN ANY SCHEDULED TASK, MILESTONE, PO, MO OR DELIVERY INSIDE IT!
            p_start = min(p_start, tg_s, pog_s, mog_s, delg_s)
            p_end = max(p_end, tg_e, pog_e, mog_e, delg_e)

            if p_end <= p_start:
                p_end = datetime.combine(p_start.date(), time(17, 0, 0))
                if p_end <= p_start:
                    p_end = p_start + timedelta(hours=1)

            # Grid alignment for Project Gantt visual bar (midnight boundaries)
            gantt_p_start = datetime.combine(p_start.date(), time.min)
            if p_end.time() > time.min:
                gantt_p_end = datetime.combine(p_end.date() + timedelta(days=1), time.min)
            else:
                gantt_p_end = datetime.combine(p_end.date(), time.min)

            if gantt_p_end <= gantt_p_start:
                gantt_p_end = gantt_p_start + timedelta(days=1)

            p_start_str = gantt_p_start.strftime(dt_format)
            p_end_str = gantt_p_end.strftime(dt_format)
            p_work_start_str = p_start.strftime(dt_format)
            p_work_end_str = p_end.strftime(dt_format)

            # Project Progress & Completion:
            total_allocated = sum(p_tasks.mapped("allocated_hours") or [0.0])

            # Project allocated hours: use existing allocated_hours or calculate from working hours
            p_work_hours = calculate_lithuanian_working_hours(p_start, p_end, holidays_cache=holidays_cache)
            p_alloc = p.allocated_hours if (p.allocated_hours and p.allocated_hours > 0) else (p_work_hours if p_work_hours > 0 else 0.0)

            display_proj_alloc = p_alloc or total_allocated
            p_stage = p.stage_id if can_read_proj_stage else False
            p_stage_name = (p_stage.name or "").lower() if p_stage else ""
            p_stage_display = p_stage.name if p_stage else ""
            p_self_done = bool(
                "done" in p_stage_name
                or "closed" in p_stage_name
                or "completed" in p_stage_name
                or (getattr(p_stage, "is_closed", False) if p_stage else False)
            )

            if p_tasks:
                top_p_tasks = p_tasks.filtered(lambda t: not t.parent_id or t.parent_id.id not in p_tasks_id_set)
                p_task_metrics = [get_task_metrics(t) for t in (top_p_tasks or p_tasks)]
                all_p_done = all(tm["is_done"] for tm in p_task_metrics)
                avg_progress_val = sum(tm["progress"] for tm in p_task_metrics) / len(p_task_metrics)
                if all_p_done and len(p_task_metrics) > 0:
                    p_is_done = True
                    p_prog = 1.0
                else:
                    p_is_done = False
                    p_prog = min(0.99, round(avg_progress_val, 2))
            elif p_self_done:
                p_is_done = True
                p_prog = 1.0
            else:
                p_is_done = False
                p_prog = 0.0

            p_update_status = p.last_update_status or ""

            # Check project status (last_update_status from Kanban / Project form)
            if p_update_status == "on_hold":
                p_state = "04_on_hold"
                project_color = "#00a09d"
            elif p_update_status == "on_track":
                p_state = "03_on_track"
                project_color = "#10b981"
            elif p_update_status == "at_risk":
                p_state = "02_at_risk"
                project_color = "#f59e0b"
            elif p_update_status == "off_track":
                p_state = "1_off_track"
                project_color = "#ef4444"
            elif p_update_status == "done" or p_is_done or "done" in p_stage_name or "completed" in p_stage_name:
                p_state = "1_done"
                project_color = "#16a34a"
                p_is_done = True
            elif "approved" in p_stage_name or "patvirtinta" in p_stage_name:
                p_state = "03_approved"
                project_color = "#10b981"
            elif "changes" in p_stage_name or "koreg" in p_stage_name:
                p_state = "02_changes_requested"
                project_color = "#f59e0b"
            elif "cancel" in p_stage_name or "atšauk" in p_stage_name:
                p_state = "1_canceled"
                project_color = "#dc3545"
            elif "hold" in p_stage_name or "lauk" in p_stage_name:
                p_state = "04_on_hold"
                project_color = "#00a09d"
            else:
                p_state = "01_in_progress"
                project_color = "#5f5285"

            # Project Deadline and Delay Calculation
            p_deadline_str = False
            p_deadline_end_str = False
            p_has_deadline = False
            p_has_deadline_delay = False
            p_delay_days = 0
            p_delay_hours = 0.0
            p_delay_minutes = 0

            p_date_deadline = getattr(p, "date_deadline", False)
            p_own_dl = None
            if p_date_deadline:
                p_own_dl = utc_to_local_dt(self.env, p_date_deadline, user_tz=user_tz) if isinstance(p_date_deadline, datetime) else datetime.combine(p_date_deadline, time(17, 0, 0))
            p_delay = compute_container_delay(p_own_dl, p_end, p_is_done, p_tasks)
            p_delay_kind = p_delay["kind"]
            if p_delay["deadline"]:
                p_has_deadline = True
                p_deadline_str = p_delay["deadline"].strftime(dt_format)
                p_deadline_end_str = p_deadline_str
            if p_delay_kind:
                p_has_deadline_delay = True
                p_delay_days = p_delay["days"]
                p_delay_hours = round(p_delay["seconds"] / 3600.0, 1)
                p_delay_minutes = int(round(p_delay["seconds"] / 60.0))

            # Overdue past-due project check (Fire 🔥)
            p_has_overdue_task = False
            for t in p_tasks:
                tm = get_task_metrics(t)
                if tm.get("is_done") or t.state in ("1_done", "03_approved", "1_canceled"):
                    continue
                t_s, t_e = get_task_dates(t)
                t_has_end_or_dl = bool(t.planned_date_end or t.date_deadline or (t.parent_id and t.parent_id.planned_date_end))
                if t_has_end_or_dl and t_e < now_local:
                    p_has_overdue_task = True
                    break
                if t.date_deadline and utc_to_local_dt(self.env, t.date_deadline, user_tz=user_tz) < now_local:
                    p_has_overdue_task = True
                    break

            if is_p_continuous and (p_is_done or p_state in ("1_done", "03_approved", "1_canceled")):
                is_p_continuous = False

            is_p_overdue_past = False
            if not is_p_continuous and not p_is_done and p_state not in ("1_done", "03_approved", "1_canceled", "04_on_hold") and p_prog < 1.0:
                eff_p_deadline = p_delay.get("deadline")
                if (p_end and p_end < now_local) or (p_own_dl and p_own_dl < now_local) or (eff_p_deadline and eff_p_deadline < now_local):
                    is_p_overdue_past = True

            if is_only_overdue and not is_p_overdue_past:
                continue
            if is_only_active and (
                p_is_done
                or p_state in ("1_done", "03_approved", "1_canceled")
                or p_update_status == "done"
                or "cancel" in p_stage_name
                or "atšauk" in p_stage_name
            ):
                continue

            p_has_early_finish = False
            p_early_minutes = 0
            p_early_hours = 0.0
            p_early_days = 0
            p_actual_finish_str = False
            p_act_date = getattr(p, "actual_finish_date", False)
            if p_is_done and not p_has_deadline_delay and p_act_date and p_end:
                p_act_loc = datetime.combine(p_act_date, time(17, 0, 0))
                if p_act_loc < p_end:
                    p_has_early_finish = True
                    p_early_minutes = ceil_minutes_between(p_act_loc, p_end)
                    p_early_hours = round(p_early_minutes / 60.0, 1)
                    p_early_days = (p_end.date() - p_act_loc.date()).days
                    p_actual_finish_str = p_act_loc.strftime(dt_format)

            gantt_tasks.append({
                "id": p_key,
                "odoo_id": p.id,
                "text": p.name or "Untitled Project",
                "open": False,
                "start_date": p_start_str,
                "end_date": p_end_str,
                "work_start_date": p_work_start_str,
                "work_end_date": p_work_end_str,
                "date_deadline": p_deadline_str,
                "deadline_end": p_deadline_end_str,
                "has_deadline": p_has_deadline,
                "has_deadline_delay": p_has_deadline_delay,
                "delay_kind": p_delay_kind,
                "delay_days": p_delay_days,
                "delay_hours": p_delay_hours,
                "delay_minutes": p_delay_minutes,
                "has_early_finish": p_has_early_finish,
                "early_minutes": p_early_minutes,
                "early_hours": p_early_hours,
                "early_days": p_early_days,
                "actual_finish_date": p_actual_finish_str,
                "progress": round(p_prog, 2),
                "progress_percent": round(p_prog * 100, 1),
                "allocated_hours": format_hours_label(display_proj_alloc),
                "allocated_hours_raw": p_alloc or 0.0,
                "assignees": p.user_id.name or "",
                "assignee_avatars": [
                    {
                        "id": p.user_id.id,
                        "name": p.user_id.name,
                        "avatar": f"/web/image/res.users/{p.user_id.id}/avatar_128",
                    }
                ] if p.user_id else [],
                "color": project_color,
                "state": p_state,
                "last_update_status": p_update_status,
                "stage_name": p_stage_display,
                "is_overdue_past": is_p_overdue_past,
                "is_continuous": is_p_continuous,
                "has_overdue_tasks": p_has_overdue_task,
                "is_project": True,
                "is_milestone": False,
                "is_done": bool(p_is_done),
                "readonly": not can_edit,
            })

            has_manufacturing = bool(p_pos or p_mos or p_deliveries)
            tasks_group_parent = p_key

            if has_manufacturing:
                # -----------------------------------------------------------------
                # SECTION 1: 📋 Projekto užduotys (Milestones & Tasks)
                # -----------------------------------------------------------------
                tasks_group_key = f"tasks_group_{p.id}"
                tasks_group_parent = tasks_group_key
                tg_gantt_start = datetime.combine(tg_s.date(), time.min)
                if tg_e.time() > time.min:
                    tg_gantt_end = datetime.combine(tg_e.date() + timedelta(days=1), time.min)
                else:
                    tg_gantt_end = datetime.combine(tg_e.date(), time.min)
                if tg_gantt_end <= tg_gantt_start:
                    tg_gantt_end = tg_gantt_start + timedelta(days=1)

                gantt_tasks.append({
                    "id": tasks_group_key,
                    "odoo_id": p.id,
                    "text": "📋 Projekto užduotys",
                    "type": "task",
                    "open": False,
                    "parent": p_key,
                    "start_date": tg_gantt_start.strftime(dt_format),
                    "end_date": tg_gantt_end.strftime(dt_format),
                    "work_start_date": tg_s.strftime(dt_format),
                    "work_end_date": tg_e.strftime(dt_format),
                    "is_group": True,
                    "is_tasks_group": True,
                    "is_project": False,
                    "is_milestone": False,
                    "readonly": True,
                    "color": "#64748b",
                    "state": p_state,
                    "progress": round(p_prog, 2),
                    "progress_percent": round(p_prog * 100, 1),
                    "assignees": "",
                    "assignee_avatars": [],
                    "project_name": p.name or "",
                    "project_id": p.id,
                })

            m_tasks_by_ms_id = {}
            for t in p_tasks:
                if t.milestone_id:
                    m_tasks_by_ms_id.setdefault(t.milestone_id.id, []).append(t)

            # Add Milestones for this project under tasks_group_parent
            for m in p_milestones:
                m_key = f"milestone_{m.id}"
                seen_milestone_keys.add(m_key)
                m_tasks = task_model.browse([t.id for t in m_tasks_by_ms_id.get(m.id, [])])

                # Milestone Base Start Date
                m_date_start = getattr(m, "date_start", False)
                if m_date_start:
                    m_start = datetime.combine(m.date_start, time(8, 0, 0)) if not isinstance(m.date_start, datetime) else utc_to_local_dt(self.env, m.date_start, user_tz=user_tz)
                elif m.deadline:
                    m_dl = datetime.combine(m.deadline, time(17, 0, 0)) if not isinstance(m.deadline, datetime) else utc_to_local_dt(self.env, m.deadline, user_tz=user_tz)
                    m_start = m_dl - timedelta(days=3)
                else:
                    m_start = p_start

                if m_start.hour == 0 and m_start.minute == 0:
                    m_start = m_start.replace(hour=8, minute=0, second=0)

                # Milestone Base End Date
                m_planned_date_end = getattr(m, "planned_date_end", False)
                if m_planned_date_end:
                    m_end = datetime.combine(m.planned_date_end, time(17, 0, 0)) if not isinstance(m.planned_date_end, datetime) else utc_to_local_dt(self.env, m.planned_date_end, user_tz=user_tz)
                elif m.deadline:
                    m_end = datetime.combine(m.deadline, time(17, 0, 0)) if not isinstance(m.deadline, datetime) else utc_to_local_dt(self.env, m.deadline, user_tz=user_tz)
                else:
                    m_end = m_start + timedelta(days=7)

                if (m_end.hour == 0 and m_end.minute == 0) or (m_end.hour == 12 and m_end.minute == 0) or (m_end.hour == 23 and m_end.minute == 59):
                    m_end = m_end.replace(hour=17, minute=0, second=0)

                if m_tasks:
                    scheduled_m_tasks = m_tasks.filtered(lambda t: t.planned_date_start or t.planned_date_end or t.date_deadline or t.parent_id)
                    for t in (scheduled_m_tasks or m_tasks):
                        t_s, t_e = get_task_dates(t)
                        if t_s < m_start:
                            m_start = t_s
                        if t_e > m_end:
                            m_end = t_e

                if m_end <= m_start:
                    m_end = m_start + timedelta(days=1)

                gantt_m_start = datetime.combine(m_start.date(), time.min)
                if m_end.time() > time.min:
                    gantt_m_end = datetime.combine(m_end.date() + timedelta(days=1), time.min)
                else:
                    gantt_m_end = datetime.combine(m_end.date(), time.min)

                if gantt_m_end <= gantt_m_start:
                    gantt_m_end = gantt_m_start + timedelta(days=1)

                m_start_str = gantt_m_start.strftime(dt_format)
                m_end_str = gantt_m_end.strftime(dt_format)
                m_work_start_str = m_start.strftime(dt_format)
                m_work_end_str = m_end.strftime(dt_format)

                m_allocated = sum(m_tasks.mapped("allocated_hours") or [0.0])

                if m_tasks:
                    top_m_tasks = m_tasks.filtered(lambda t: not t.parent_id or t.parent_id.id not in p_tasks_id_set)
                    m_task_metrics = [get_task_metrics(t) for t in (top_m_tasks or m_tasks)]
                    all_m_done = all(tm["is_done"] for tm in m_task_metrics)
                    avg_m_prog = sum(tm["progress"] for tm in m_task_metrics) / len(m_task_metrics)
                    if all_m_done and len(m_task_metrics) > 0:
                        m_is_done = True
                        m_prog = 1.0
                    else:
                        m_is_done = False
                        m_prog = min(0.99, round(avg_m_prog, 2))
                elif m.is_reached:
                    m_is_done = True
                    m_prog = 1.0
                else:
                    m_is_done = False
                    m_prog = 0.0

                if m_is_done:
                    m_state = "1_done"
                    milestone_color = "#16a34a"
                else:
                    m_state = "01_in_progress"
                    milestone_color = "#2563eb"

                m_deadline_str = False
                m_deadline_end_str = False
                m_has_deadline = False
                m_has_deadline_delay = False
                m_delay_days = 0
                m_delay_hours = 0.0
                m_delay_minutes = 0

                m_dl_base = m.deadline if m.deadline else False
                m_own_dl = None
                if m_dl_base:
                    m_own_dl = utc_to_local_dt(self.env, m_dl_base, user_tz=user_tz) if isinstance(m_dl_base, datetime) else datetime.combine(m_dl_base, time(17, 0, 0))
                if m.is_reached and m.reached_date:
                    m_reached_dt = datetime.combine(m.reached_date, time(17, 0, 0))
                    if m_end and m_reached_dt > m_end and (not m_own_dl or m_reached_dt > m_own_dl):
                        m_own_dl = m_reached_dt
                m_delay = compute_container_delay(m_own_dl, m_end, m_is_done, m_tasks)
                m_delay_kind = m_delay["kind"]
                if m_delay["deadline"]:
                    m_has_deadline = True
                    m_deadline_str = m_delay["deadline"].strftime(dt_format)
                    m_deadline_end_str = m_deadline_str
                if m_delay_kind:
                    m_has_deadline_delay = True
                    m_delay_days = m_delay["days"]
                    m_delay_hours = round(m_delay["seconds"] / 3600.0, 1)
                    m_delay_minutes = int(round(m_delay["seconds"] / 60.0))

                m_has_early_finish = False
                m_early_minutes = 0
                m_early_hours = 0.0
                m_early_days = 0
                m_actual_finish_str = False
                if m_is_done and not m_has_deadline_delay and m.is_reached and m.reached_date and m_end:
                    m_act_loc = datetime.combine(m.reached_date, time(17, 0, 0))
                    if m_act_loc < m_end:
                        m_has_early_finish = True
                        m_early_minutes = ceil_minutes_between(m_act_loc, m_end)
                        m_early_hours = round(m_early_minutes / 60.0, 1)
                        m_early_days = (m_end.date() - m_act_loc.date()).days
                        m_actual_finish_str = m_act_loc.strftime(dt_format)

                m_task_count = len(m_tasks)
                m_done_task_count = sum(
                    1 for t in m_tasks
                    if t.state in ("1_done", "1_canceled") or get_task_metrics(t).get("is_done")
                )

                gantt_tasks.append({
                    "id": m_key,
                    "odoo_id": m.id,
                    "text": m.name or "Gairė",
                    "type": "task",
                    "open": False,
                    "parent": tasks_group_parent,
                    "start_date": m_start_str,
                    "end_date": m_end_str,
                    "work_start_date": m_work_start_str,
                    "work_end_date": m_work_end_str,
                    "planned_date_start": m_date_start.strftime(dt_format) if m_date_start else False,
                    "planned_date_end": m_planned_date_end.strftime(dt_format) if m_planned_date_end else False,
                    "date_deadline": m_deadline_str,
                    "deadline_end": m_deadline_end_str,
                    "has_deadline": m_has_deadline,
                    "has_deadline_delay": m_has_deadline_delay,
                    "delay_kind": m_delay_kind,
                    "delay_days": m_delay_days,
                    "delay_hours": m_delay_hours,
                    "delay_minutes": m_delay_minutes,
                    "has_early_finish": m_has_early_finish,
                    "early_minutes": m_early_minutes,
                    "early_hours": m_early_hours,
                    "early_days": m_early_days,
                    "actual_finish_date": m_actual_finish_str,
                    "progress": round(m_prog, 2),
                    "progress_percent": round(m_prog * 100, 1),
                    "allocated_hours": format_hours_label(m_allocated),
                    "assignees": "",
                    "assignee_avatars": [],
                    "project_name": p.name or "",
                    "project_id": p.id,
                    "task_count": m_task_count,
                    "done_task_count": m_done_task_count,
                    "color": milestone_color,
                    "state": m_state,
                    "is_overdue_past": bool(
                        not m_is_done
                        and m_state not in ("1_done", "03_approved", "1_canceled")
                        and m_prog < 1.0
                        and (
                            (m_end and m_end < now_local)
                            or (m_own_dl and m_own_dl < now_local)
                            or (m_delay.get("deadline") and m_delay["deadline"] < now_local)
                        )
                    ),
                    "is_project": False,
                    "is_milestone": True,
                    "is_done": m_is_done,
                    "readonly": not can_edit,
                })

                # Add tasks assigned to this milestone (Top-level tasks under milestone)
                top_m_tasks = m_tasks.filtered(lambda t: not t.parent_id or t.parent_id.id not in p_tasks_id_set)
                for t in top_m_tasks:
                    add_task_hierarchy(t, m_key, p_tasks_id_set)

            # Top-level tasks without milestone under tasks_group_parent
            top_no_m_tasks = p_tasks.filtered(
                lambda t: (not t.milestone_id or f"milestone_{t.milestone_id.id}" not in seen_milestone_keys)
                and (not t.parent_id or t.parent_id.id not in p_tasks_id_set)
            )
            for t in top_no_m_tasks:
                add_task_hierarchy(t, tasks_group_parent, p_tasks_id_set)

            # Any remaining tasks in this project
            remaining_p_tasks = p_tasks.filtered(lambda t: t.id not in added_task_ids)
            for t in remaining_p_tasks:
                add_task_hierarchy(t, tasks_group_parent, p_tasks_id_set)

            if not has_manufacturing:
                continue

            # -----------------------------------------------------------------
            # SECTION 2: 📦 Pirkimai (Purchase Orders)
            # -----------------------------------------------------------------
            po_group_key = f"po_group_{p.id}"
            pog_gantt_start = datetime.combine(pog_s.date(), time.min)
            if pog_e.time() > time.min:
                pog_gantt_end = datetime.combine(pog_e.date() + timedelta(days=1), time.min)
            else:
                pog_gantt_end = datetime.combine(pog_e.date(), time.min)
            if pog_gantt_end <= pog_gantt_start:
                pog_gantt_end = pog_gantt_start + timedelta(days=1)

            all_pos_done = bool(p_pos and all((getattr(po, "receipt_status", False) == "full") or (po.state == "done" and not getattr(po, "picking_ids", False)) for po in p_pos))
            pog_prog = 1.0 if all_pos_done else (0.5 if p_pos else 0.0)

            gantt_tasks.append({
                "id": po_group_key,
                "odoo_id": p.id,
                "text": "📦 Pirkimai",
                "type": "task",
                "open": False,
                "parent": p_key,
                "start_date": pog_gantt_start.strftime(dt_format),
                "end_date": pog_gantt_end.strftime(dt_format),
                "work_start_date": pog_s.strftime(dt_format),
                "work_end_date": pog_e.strftime(dt_format),
                "is_group": True,
                "is_po_group": True,
                "is_project": False,
                "is_milestone": False,
                "readonly": True,
                "color": "#0284c7",
                "state": "purchase" if all_pos_done else "01_in_progress",
                "progress": pog_prog,
                "progress_percent": int(pog_prog * 100),
                "assignees": "",
                "assignee_avatars": [],
                "project_name": p.name or "",
                "project_id": p.id,
            })

            for po in p_pos:
                gantt_tasks.append(_build_supply_gantt_item("po", po, po_group_key, p_start, p, po.user_id if hasattr(po, "user_id") else False))

            # -----------------------------------------------------------------
            # SECTION 3: ⚙️ Gamyba (Manufacturing Orders)
            # -----------------------------------------------------------------
            mo_group_key = f"mo_group_{p.id}"
            mog_gantt_start = datetime.combine(mog_s.date(), time.min)
            if mog_e.time() > time.min:
                mog_gantt_end = datetime.combine(mog_e.date() + timedelta(days=1), time.min)
            else:
                mog_gantt_end = datetime.combine(mog_e.date(), time.min)
            if mog_gantt_end <= mog_gantt_start:
                mog_gantt_end = mog_gantt_start + timedelta(days=1)

            all_mos_done = bool(p_mos and all(mo.state == "done" for mo in p_mos))
            mog_prog = 1.0 if all_mos_done else (0.5 if p_mos else 0.0)

            gantt_tasks.append({
                "id": mo_group_key,
                "odoo_id": p.id,
                "text": "⚙️ Gamyba",
                "type": "task",
                "open": False,
                "parent": p_key,
                "start_date": mog_gantt_start.strftime(dt_format),
                "end_date": mog_gantt_end.strftime(dt_format),
                "work_start_date": mog_s.strftime(dt_format),
                "work_end_date": mog_e.strftime(dt_format),
                "is_group": True,
                "is_mo_group": True,
                "is_project": False,
                "is_milestone": False,
                "readonly": True,
                "color": "#ea580c",
                "state": "done" if all_mos_done else "01_in_progress",
                "progress": mog_prog,
                "progress_percent": int(mog_prog * 100),
                "assignees": "",
                "assignee_avatars": [],
                "project_name": p.name or "",
                "project_id": p.id,
            })

            for mo in p_mos:
                gantt_tasks.append(_build_supply_gantt_item("mo", mo, mo_group_key, p_start, p, mo.user_id if hasattr(mo, "user_id") else False))

            # -----------------------------------------------------------------
            # SECTION 4: 🚚 Pristatymas (Deliveries)
            # -----------------------------------------------------------------
            delivery_group_key = f"delivery_group_{p.id}"
            delg_gantt_start = datetime.combine(delg_s.date(), time.min)
            if delg_e.time() > time.min:
                delg_gantt_end = datetime.combine(delg_e.date() + timedelta(days=1), time.min)
            else:
                delg_gantt_end = datetime.combine(delg_e.date(), time.min)
            if delg_gantt_end <= delg_gantt_start:
                delg_gantt_end = delg_gantt_start + timedelta(days=1)

            all_del_done = bool(p_deliveries and all(pick.state == "done" for pick in p_deliveries))
            delg_prog = 1.0 if all_del_done else 0.0

            gantt_tasks.append({
                "id": delivery_group_key,
                "odoo_id": p.id,
                "text": "🚚 Pristatymas",
                "type": "task",
                "open": False,
                "parent": p_key,
                "start_date": delg_gantt_start.strftime(dt_format),
                "end_date": delg_gantt_end.strftime(dt_format),
                "work_start_date": delg_s.strftime(dt_format),
                "work_end_date": delg_e.strftime(dt_format),
                "is_group": True,
                "is_delivery_group": True,
                "is_project": False,
                "is_milestone": False,
                "readonly": True,
                "color": "#0f766e",
                "state": "done" if all_del_done else "01_in_progress",
                "progress": delg_prog,
                "progress_percent": int(delg_prog * 100),
                "assignees": "",
                "assignee_avatars": [],
                "project_name": p.name or "",
                "project_id": p.id,
            })

            for pick in p_deliveries:
                gantt_tasks.append(_build_supply_gantt_item("delivery", pick, delivery_group_key, p_start, p, pick.user_id if hasattr(pick, "user_id") else False))

        # Standalone MOs, POs, and Deliveries without a project (ACL-checked, NO .sudo())
        if not explicit_proj_id and not is_only_overdue and not cleaned_domain and not user_id_int:
            def _append_standalone_group(kind, recs, group_id, group_label, group_flag, color, all_done, prog, user_field_ok):
                if not recs:
                    return
                built_items = [
                    _build_supply_gantt_item(kind, r, group_id, now, False, r.user_id if user_field_ok else False)
                    for r in recs
                ]
                g_start = min(item["start_date"] for item in built_items)
                g_end = max(item["end_date"] for item in built_items)
                w_start = min(item["work_start_date"] for item in built_items)
                w_end = max(item["work_end_date"] for item in built_items)
                gantt_tasks.append({
                    "id": group_id,
                    "odoo_id": 0,
                    "text": group_label,
                    "type": "task",
                    "open": False,
                    "parent": None,
                    "start_date": g_start,
                    "end_date": g_end,
                    "work_start_date": w_start,
                    "work_end_date": w_end,
                    "is_group": True,
                    group_flag: True,
                    "is_project": False,
                    "is_milestone": False,
                    "readonly": True,
                    "color": color,
                    "state": "done" if all_done else "01_in_progress",
                    "progress": prog,
                    "progress_percent": int(prog * 100),
                })
                gantt_tasks.extend(built_items)

            if has_mrp:
                try:
                    mo_model = self.env["mrp.production"]
                    mo_dom = [("state", "!=", "cancel")]
                    if "project_id" in mo_model._fields:
                        mo_dom.append(("project_id", "=", False))
                    standalone_mos = mo_model.search(mo_dom, order="date_start, id", limit=1000)
                    if standalone_mos:
                        all_smo_done = all(mo.state == "done" for mo in standalone_mos)
                        _append_standalone_group(
                            "mo", standalone_mos, "mo_standalone_group",
                            "⚙️ Kiti gamybos užsakymai (be projektų)", "is_mo_standalone_group",
                            "#ea580c", all_smo_done, 1.0 if all_smo_done else 0.5,
                            "user_id" in mo_model._fields,
                        )
                except Exception as e:
                    _logger.warning("Error fetching standalone MOs: %s", e)

            if has_purchase:
                try:
                    po_model = self.env["purchase.order"]
                    proj_po_ids = {po.id for pos in pos_by_proj_id.values() for po in pos}
                    po_dom = [("state", "!=", "cancel")]
                    if "project_id" in po_model._fields:
                        po_dom.append(("project_id", "=", False))
                    if proj_po_ids:
                        po_dom.append(("id", "not in", list(proj_po_ids)))
                    standalone_pos = po_model.search(po_dom, order="date_order, id", limit=1000)
                    if standalone_pos:
                        all_spo_done = all((getattr(po, "receipt_status", False) == "full") or (po.state == "done" and not getattr(po, "picking_ids", False)) for po in standalone_pos)
                        _append_standalone_group(
                            "po", standalone_pos, "po_standalone_group",
                            "📦 Kiti pirkimo užsakymai (be projektų)", "is_po_standalone_group",
                            "#0284c7", all_spo_done, 1.0 if all_spo_done else 0.5,
                            "user_id" in po_model._fields,
                        )
                except Exception as e:
                    _logger.warning("Error fetching standalone POs: %s", e)

            if has_delivery:
                try:
                    pick_model = self.env["stock.picking"]
                    proj_del_ids = {pk.id for pks in deliveries_by_proj_id.values() for pk in pks}
                    del_dom = [("picking_type_code", "=", "outgoing"), ("state", "!=", "cancel")]
                    if proj_del_ids:
                        del_dom.append(("id", "not in", list(proj_del_ids)))
                    standalone_dels = pick_model.search(del_dom, order="scheduled_date, id", limit=1000)
                    if standalone_dels:
                        all_sdel_done = all(pk.state == "done" for pk in standalone_dels)
                        _append_standalone_group(
                            "delivery", standalone_dels, "delivery_standalone_group",
                            "🚚 Kiti pristatymai (be projektų)", "is_delivery_standalone_group",
                            "#0f766e", all_sdel_done, 1.0 if all_sdel_done else 0.0,
                            "user_id" in pick_model._fields,
                        )
                except Exception as e:
                    _logger.warning("Error fetching standalone Deliveries: %s", e)

        # 2. Any tasks without project
        all_task_id_set = set(tasks.ids)
        other_tasks = tasks.filtered(lambda t: t.id not in added_task_ids)
        if is_only_overdue:
            other_tasks = other_tasks.filtered(lambda t: (not get_task_metrics(t).get("is_done")) and t.state not in ("1_done", "03_approved", "1_canceled") and ((get_task_dates(t)[1] < now_local) or (t.date_deadline and utc_to_local_dt(self.env, t.date_deadline, user_tz=user_tz) < now_local) or (delay_cache.get(t.id) and delay_cache[t.id].get("kind"))))
        elif is_only_active:
            other_tasks = other_tasks.filtered(lambda t: (not get_task_metrics(t).get("is_done")) and t.state not in ("1_done", "03_approved", "1_canceled"))
        for t in other_tasks:
            parent_k = f"task_{t.parent_id.id}" if (t.parent_id and t.parent_id.id in added_task_ids) else None
            add_task_hierarchy(t, parent_k, all_task_id_set)

        # 3. Add dependencies
        included_task_keys = {item["id"] for item in gantt_tasks}
        for t in tasks:
            for dep in t.depend_on_ids:
                s_key = f"task_{dep.id}"
                t_key = f"task_{t.id}"
                if s_key in included_task_keys and t_key in included_task_keys:
                    links.append({
                        "id": f"link_{dep.id}_{t.id}",
                        "source": s_key,
                        "target": t_key,
                        "type": "0",  # finish_to_start
                        "source_id": dep.id,
                        "target_id": t.id,
                    })

        # 4. All projects for filter dropdown
        all_projects = project_model.search([], order="name", limit=500)
        project_list = [{"id": p.id, "name": p.name} for p in all_projects]

        return {
            "tasks": gantt_tasks,
            "links": links,
            "projects": project_list,
            "can_edit": can_edit,
            "can_track_time": can_track_time,
            "has_mrp": has_mrp,
            "has_purchase": has_purchase,
            "has_delivery": has_delivery,
        }

    @api.model
    def save_timeline_batch_schedule(self, updates):
        """
        Saves schedule changes (dates, progress) for a batch of projects, milestones, and tasks.
        Enables cascading drag-and-drop where moving a parent (project, milestone, parent task)
        moves all its child items together in a single atomic transaction.
        """
        self._check_gannt_lt_edit_access()
        if not updates or not isinstance(updates, list):
            return False

        user_tz = get_user_tz(self.env)
        holidays_cache = {}
        project_model = self.env["project.project"]
        milestone_model = self.env["project.milestone"]

        parsed_project_items = []
        parsed_milestone_items = []
        parsed_task_items = []

        for item in updates:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if not raw_id:
                continue
            str_id = str(raw_id).strip()
            if str_id.startswith(("po_", "mo_", "delivery_", "tasks_group_", "po_group_", "mo_group_", "delivery_group_", "mo_standalone_group")):
                continue
            if str_id.startswith("proj_"):
                parsed_project_items.append((_parse_int_id(str_id, prefix="proj_"), item))
            elif str_id.startswith("milestone_"):
                parsed_milestone_items.append((_parse_int_id(str_id, prefix="milestone_"), item))
            else:
                parsed_task_items.append((_parse_int_id(str_id, prefix="task_"), item))

        # Batch-prefetch tasks and compute memoized depths with cycle protection
        all_t_ids = list({t_id for t_id, _ in parsed_task_items})
        prefetched_tasks = self.browse(all_t_ids)
        task_by_id = {t.id: t for t in prefetched_tasks if t.exists()}
        depth_cache = {}

        def _get_depth(t_id):
            if t_id in depth_cache:
                return depth_cache[t_id]
            visited = set()
            chain = []
            curr_id = t_id
            base_depth = 0
            while curr_id and curr_id not in visited:
                if curr_id in depth_cache:
                    base_depth = depth_cache[curr_id]
                    break
                visited.add(curr_id)
                rec = task_by_id.get(curr_id)
                if rec is None:
                    rec = self.browse(curr_id)
                    if not rec.exists():
                        break
                    task_by_id[curr_id] = rec
                chain.append(curr_id)
                curr_id = rec.parent_id.id if rec.parent_id else False
            for idx, cid in enumerate(reversed(chain)):
                depth_cache[cid] = base_depth + idx
            return depth_cache.get(t_id, 0)

        # Process deepest descendant tasks first, then parent tasks
        parsed_task_items.sort(key=lambda pair: _get_depth(pair[0]), reverse=True)
        modified_tasks = self.env["project.task"]

        def _parse_dt(raw_val):
            try:
                return fields.Datetime.to_datetime(raw_val)
            except (ValueError, TypeError) as exc:
                raise ValidationError(_("Neteisingas datos formatas: %s") % (raw_val,)) from exc

        def _parse_d(raw_val):
            try:
                return fields.Date.to_date(raw_val)
            except (ValueError, TypeError) as exc:
                raise ValidationError(_("Neteisingas datos formatas: %s") % (raw_val,)) from exc

        # 1. Task / Subtask updates (with deferred hierarchy propagation until all tasks in batch are written)
        for t_id, item in parsed_task_items:
            task = task_by_id.get(t_id)
            if not task:
                continue
            start_date = item.get("start_date")
            end_date = item.get("end_date")
            progress = item.get("progress")

            vals = {}
            dt_start_local = _parse_dt(start_date) if start_date else utc_to_local_dt(self.env, task.planned_date_start, user_tz=user_tz)
            dt_end_local = _parse_dt(end_date) if end_date else utc_to_local_dt(self.env, task.planned_date_end or task.date_deadline, user_tz=user_tz)
            dt_start_utc = local_to_utc_dt(self.env, dt_start_local, user_tz=user_tz) if dt_start_local else False
            dt_end_utc = local_to_utc_dt(self.env, dt_end_local, user_tz=user_tz) if dt_end_local else False

            start_changed = bool(start_date) and (
                not task.planned_date_start or abs((dt_start_utc - task.planned_date_start).total_seconds()) > 1
            )
            end_changed = bool(end_date) and (
                not task.planned_date_end or abs((dt_end_utc - task.planned_date_end).total_seconds()) > 1
            )
            if start_changed:
                vals["planned_date_start"] = dt_start_utc
            if end_changed:
                vals["planned_date_end"] = dt_end_utc

            dates_modified = start_changed or end_changed
            if (dates_modified or ((start_date or end_date) and not task.allocated_hours)) and dt_start_local and dt_end_local:
                t_w_hours = calculate_lithuanian_working_hours(dt_start_local, dt_end_local, holidays_cache=holidays_cache)
                if t_w_hours > 0:
                    vals["allocated_hours"] = t_w_hours

            # Check if task is locked by uncompleted dependencies (03_approved and 1_done both satisfy; progress scale 0.0..1.0)
            is_locked = False
            if task.depend_on_ids:
                for dep in task.depend_on_ids:
                    if dep.state not in ("03_approved", "1_done") and (not dep.progress or dep.progress < 1.0):
                        is_locked = True
                        break

            deferred_progress = None
            touches_alloc_or_dates = (
                "allocated_hours" in vals
                or "planned_date_start" in vals
                or "planned_date_end" in vals
            )
            if is_locked:
                vals["state"] = "04_waiting_normal"
                if touches_alloc_or_dates:
                    deferred_progress = 0.0
                else:
                    vals["progress"] = 0.0
            elif progress is not None and not task.child_ids:
                try:
                    p_float = min(1.0, max(0.0, float(progress)))
                except (ValueError, TypeError) as exc:
                    raise ValidationError(_("Neteisinga progreso reikšmė: %s") % (progress,)) from exc
                if p_float >= 0.99:
                    if task.state == "03_approved":
                        vals["state"] = "03_approved"
                    else:
                        vals["state"] = "1_done"
                elif p_float < 1.0:
                    if task.state in ("1_done", "04_waiting_normal"):
                        vals["state"] = "01_in_progress"
                    task._cascade_clear_downstream_progress()
                if touches_alloc_or_dates:
                    deferred_progress = p_float
                else:
                    vals["progress"] = p_float
            elif not is_locked and task.state == "04_waiting_normal":
                vals["state"] = "01_in_progress"

            save_ctx = {
                "from_timeline_batch": True,
                "defer_hierarchy_propagation": True,
                "_gannt_lt_trusted_token": _TRUSTED_CONTEXT_TOKEN,
            }
            if "plan_is_auto" in item and item.get("plan_is_auto") is not None:
                vals["plan_is_auto"] = bool(item.get("plan_is_auto"))
                if vals["plan_is_auto"]:
                    save_ctx["gannt_auto_plan"] = True
            elif task.plan_is_auto and task.child_ids:
                active_kids = task.child_ids.filtered(lambda c: c.active)
                kid_starts = [c.planned_date_start for c in active_kids if c.planned_date_start]
                kid_ends = [c.planned_date_end for c in active_kids if c.planned_date_end]
                target_s = vals.get("planned_date_start", task.planned_date_start)
                target_e = vals.get("planned_date_end", task.planned_date_end)
                if kid_starts and kid_ends and target_s and target_e:
                    min_ks = min(kid_starts)
                    max_ke = max(kid_ends)
                    if abs((target_s - min_ks).total_seconds()) <= 60 and abs((target_e - max_ke).total_seconds()) <= 60:
                        save_ctx["gannt_auto_plan"] = True
                    else:
                        vals["plan_is_auto"] = False
                elif "planned_date_start" not in vals and "planned_date_end" not in vals:
                    save_ctx["gannt_auto_plan"] = True
                else:
                    vals["plan_is_auto"] = False

            if vals:
                task.with_context(**save_ctx).write(vals)
                modified_tasks |= task
            if deferred_progress is not None:
                # Drain hr_timesheet._compute_progress_hours queued by date/allocated_hours writes
                # so the explicit timeline progress persists instead of being overwritten.
                _ = task.progress
                task.with_context(**save_ctx, skip_hierarchy_propagation=True).write({"progress": deferred_progress})
                modified_tasks |= task

        if modified_tasks:
            modified_tasks.with_context(
                defer_hierarchy_propagation=False,
                _gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN,
            )._propagate_deadline_upward()

        # 2. Milestone updates (batch-fetch tasks across all milestones in one query; no .sudo())
        if parsed_milestone_items:
            ms_ids = [m_id for m_id, _ in parsed_milestone_items]
            ms_records = milestone_model.browse(list(set(ms_ids)))
            ms_by_id = {m.id: m for m in ms_records if m.exists()}
            ms_tasks_by_ms_id = {}
            all_ms_tasks = self.env["project.task"].search([
                ("milestone_id", "in", list(ms_by_id.keys())),
                ("active", "=", True),
            ])
            for t in all_ms_tasks:
                if t.milestone_id:
                    ms_tasks_by_ms_id.setdefault(t.milestone_id.id, []).append(t)

            for m_id, item in parsed_milestone_items:
                milestone = ms_by_id.get(m_id)
                if not milestone:
                    continue
                start_date = item.get("start_date")
                end_date = item.get("end_date")
                progress = item.get("progress")

                vals = {}
                d_start = _parse_d(start_date) if start_date else False
                d_end = _parse_d(end_date) if end_date else False
                ms_tasks = ms_tasks_by_ms_id.get(milestone.id, [])
                ms_starts = [utc_to_local_dt(self.env, t.planned_date_start, user_tz=user_tz).date() for t in ms_tasks if t.planned_date_start]
                ms_ends = [utc_to_local_dt(self.env, t.planned_date_end, user_tz=user_tz).date() for t in ms_tasks if t.planned_date_end]
                if d_start and ms_starts:
                    d_start = min(d_start, min(ms_starts))
                if d_end and ms_ends:
                    d_end = max(d_end, max(ms_ends))
                if d_start and d_end and d_end < d_start:
                    raise ValidationError(_("Gairės pabaigos data negali būti ankstesnė už pradžios datą."))
                if d_start and hasattr(milestone, "date_start"):
                    vals["date_start"] = d_start
                if d_end and hasattr(milestone, "planned_date_end"):
                    vals["planned_date_end"] = d_end
                if d_end:
                    vals["deadline"] = d_end
                if progress is not None:
                    p_val = float(progress)
                    if p_val >= 0.99:
                        vals["is_reached"] = True
                    elif p_val == 0.0:
                        vals["is_reached"] = False
                if vals:
                    milestone.write(vals)

        # 3. Project updates
        for p_id, item in parsed_project_items:
            proj = project_model.browse(p_id)
            if not proj.exists():
                continue
            start_date = item.get("start_date")
            end_date = item.get("end_date")

            vals = {}
            d_start = _parse_d(start_date) if start_date else proj.date_start
            d_end = _parse_d(end_date) if end_date else proj.date
            if start_date:
                vals["date_start"] = d_start
            if end_date:
                vals["date"] = d_end
            if d_start and d_end:
                p_w_hours = calculate_lithuanian_working_hours(d_start, d_end, holidays_cache=holidays_cache)
                if p_w_hours > 0:
                    vals["allocated_hours"] = p_w_hours
            if vals:
                try:
                    proj.write(vals)
                except AccessError:
                    if not self.env.user.has_group("GanntLT.group_gannt_lt_manager"):
                        raise
                    proj.check_access("read")
                    if proj.company_id and proj.company_id not in self.env.companies:
                        raise AccessError(_("Negalite redaguoti kitos įmonės projekto."))
                    if proj.privacy_visibility == "followers" and self.env.user.partner_id not in proj.message_partner_ids:
                        raise AccessError(_("Neturite prieigos prie šio privataus projekto."))
                    scoped_vals = {k: v for k, v in vals.items() if k in ("date_start", "date")}
                    if scoped_vals:
                        proj.sudo().write(scoped_vals)

        return True

    @api.model
    def save_task_schedule(self, task_id, start_date, end_date, progress=None):
        """Single item wrapper for backward compatibility."""
        self._check_gannt_lt_edit_access()
        return self.save_timeline_batch_schedule([{
            "id": task_id,
            "start_date": start_date,
            "end_date": end_date,
            "progress": progress,
        }])

    @api.model
    def delete_timeline_task(self, task_id):
        """Deletes a task from the timeline."""
        self._check_gannt_lt_edit_access()
        t_id = _parse_int_id(task_id, prefix="task_")
        task = self.browse(t_id)
        if task.exists():
            return task.unlink()
        return False

    @api.model
    def delete_timeline_milestone(self, milestone_id):
        """Deletes a milestone from the timeline."""
        self._check_gannt_lt_edit_access()
        m_id = _parse_int_id(milestone_id, prefix="milestone_")
        milestone = self.env["project.milestone"].browse(m_id)
        if milestone.exists():
            return milestone.unlink()
        return False

    @api.model
    def add_timeline_dependency(self, source_id, target_id):
        """Adds finish-to-start dependency."""
        self._check_gannt_lt_edit_access()
        s_id = _parse_int_id(source_id, prefix="task_")
        t_id = _parse_int_id(target_id, prefix="task_")

        source_task = self.browse(s_id)
        target_task = self.browse(t_id)
        if source_task.exists() and target_task.exists():
            new_vals = {"depend_on_ids": [(4, source_task.id)]}
            if (
                source_task.state not in ("03_approved", "1_done")
                and target_task.state not in ("03_approved", "1_done", "1_canceled")
            ):
                new_vals["state"] = "04_waiting_normal"
                new_vals["progress"] = 0.0
            target_task.write(new_vals)
            return True
        return False

    @api.model
    def remove_timeline_dependency(self, source_id, target_id):
        """Removes dependency."""
        self._check_gannt_lt_edit_access()
        s_id = _parse_int_id(source_id, prefix="task_")
        t_id = _parse_int_id(target_id, prefix="task_")

        target_task = self.browse(t_id)
        if target_task.exists():
            target_task.write({"depend_on_ids": [(3, s_id)]})
            return True
        return False

    @api.model
    def check_edit_rights(self):
        """Returns whether the current user has edit rights in the GanntLT timeline."""
        return self._has_edit_rights()

    @api.model
    def check_timer_rights(self):
        """Returns whether the current user has rights to use the work timer."""
        return self._has_timer_rights()

    @api.model
    def action_recompute_timeline_spans(self, project_id=None):
        """Recomputes container task and milestone spans for a given project or all projects."""
        self._check_gannt_lt_edit_access()
        domain = [("active", "=", True)]
        if project_id:
            domain.append(("project_id", "=", _parse_int_id(project_id, prefix="proj_")))
            tasks = self.search(domain)
        else:
            tasks = self.search(domain, limit=5000)
        if tasks:
            tasks.with_context(_gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN)._propagate_deadline_upward()
        return True

    @api.model
    def create_new_project_from_timeline(self, name, date_start=None, date_end=None):
        """Creates a new project from the timeline."""
        self._check_gannt_lt_edit_access()
        project_model = self.env["project.project"]
        project_model.check_access("create")
        vals = {"name": name or _("Naujas projektas")}
        if "billing_type" in project_model._fields:
            vals["billing_type"] = "not_billable"
        if date_start:
            vals["date_start"] = fields.Date.to_date(date_start)
        if date_end:
            vals["date"] = fields.Date.to_date(date_end)
        proj = project_model.create(vals)
        return proj.id

    @api.model
    def create_timeline_milestone(self, name, project_id, date_start=None, date_end=None):
        """Creates a new milestone from the timeline."""
        self._check_gannt_lt_edit_access()
        p_id = _parse_int_id(project_id, prefix="proj_")
        proj = self.env["project.project"].browse(p_id)
        if not proj.exists():
            raise ValidationError(_("Projektas nerastas."))
        proj.check_access("read")
        if proj.company_id and proj.company_id not in self.env.companies:
            raise AccessError(_("Negalite kurti gairių kitos įmonės projekte."))
        if proj.privacy_visibility == "followers" and self.env.user.partner_id not in proj.message_partner_ids:
            raise AccessError(_("Neturite prieigos prie šio privataus projekto."))
        ms_model = self.env["project.milestone"]
        ms_model.check_access("create")
        vals = {
            "name": name or _("Nauja gairė"),
            "project_id": p_id,
        }
        if date_start:
            vals["date_start"] = fields.Date.to_date(date_start)
        if date_end:
            d_end = fields.Date.to_date(date_end)
            vals["planned_date_end"] = d_end
            vals["deadline"] = d_end
        ms = ms_model.create(vals)
        return ms.id

    @api.model
    def create_timeline_task(self, name, project_id, start_date=None, end_date=None, milestone_id=None, parent_id=None):
        """Creates a new task from the timeline."""
        self._check_gannt_lt_edit_access()
        p_id = _parse_int_id(project_id, prefix="proj_")
        proj = self.env["project.project"].browse(p_id)
        if not proj.exists():
            raise ValidationError(_("Projektas nerastas."))
        proj.check_access("read")
        user_tz = get_user_tz(self.env)
        vals = {
            "name": name or _("Nauja užduotis"),
            "project_id": p_id,
        }
        if start_date:
            vals["planned_date_start"] = local_to_utc_dt(self.env, fields.Datetime.to_datetime(start_date), user_tz=user_tz)
        if end_date:
            vals["planned_date_end"] = local_to_utc_dt(self.env, fields.Datetime.to_datetime(end_date), user_tz=user_tz)
        if milestone_id:
            vals["milestone_id"] = _parse_int_id(milestone_id, prefix="milestone_")
        if parent_id:
            vals["parent_id"] = _parse_int_id(parent_id, prefix="task_")
        task = self.with_context(_gannt_lt_trusted_token=_TRUSTED_CONTEXT_TOKEN).create(vals)
        return task.id

    @api.model
    def delete_timeline_item(self, item_id):
        """Deletes a project, milestone, or task from the timeline by prefixed or integer ID."""
        self._check_gannt_lt_edit_access()
        str_id = str(item_id or "").strip()
        if str_id.startswith("proj_"):
            p_id = _parse_int_id(str_id, prefix="proj_")
            proj = self.env["project.project"].browse(p_id)
            if proj.exists():
                proj.check_access("unlink")
                return proj.unlink()
            return False
        if str_id.startswith("milestone_"):
            return self.delete_timeline_milestone(str_id)
        return self.delete_timeline_task(str_id)

    # GanntLT Aliases for seamless RPC calls
    @api.model
    def get_gannt_lt_data(self, project_id=None, domain=None, date_start=None, date_end=None, user_id=None, model_name=None, only_active=False, only_overdue=False, only_mo=False, only_po=False, only_delivery=False):
        return self.get_all_in_one_timeline_data(
            project_id=project_id,
            domain=domain,
            date_start=date_start,
            date_end=date_end,
            user_id=user_id,
            model_name=model_name,
            only_active=only_active,
            only_overdue=only_overdue,
            only_mo=only_mo,
            only_po=only_po,
            only_delivery=only_delivery,
        )

    @api.model
    def save_gannt_lt_task_dates(self, task_id, start_date=None, end_date=None, progress=None, state=None, cascade_shifts=None):
        return self.save_timeline_batch_schedule([{
            "id": task_id,
            "start_date": start_date,
            "end_date": end_date,
            "progress": progress,
        }])

    @api.model
    def delete_gannt_lt_task(self, task_id):
        return self.delete_timeline_task(task_id)

    @api.model
    def delete_gannt_lt_milestone(self, milestone_id):
        return self.delete_timeline_milestone(milestone_id)

    @api.model
    def add_gannt_lt_dependency(self, source_id, target_id):
        return self.add_timeline_dependency(source_id, target_id)

    @api.model
    def remove_gannt_lt_dependency(self, source_id, target_id):
        return self.remove_timeline_dependency(source_id, target_id)


