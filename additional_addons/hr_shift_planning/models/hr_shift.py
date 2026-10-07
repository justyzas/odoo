from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .res_config_settings import DEFAULT_LIMITS


def _float_to_time(hours):
    """Convert a float hour (e.g. 6.5) to a time (06:30)."""
    total_minutes = round(hours * 60)
    return time(total_minutes // 60 % 24, total_minutes % 60)


class HrShift(models.Model):
    _name = "hr.shift"
    _description = "Employee Shift"
    _order = "date desc, employee_id"
    _check_company_auto = True

    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    date = fields.Date(
        required=True,
        index=True,
        help="Day the shift starts on. A night shift belongs to the day it starts.",
    )
    template_id = fields.Many2one(
        "hr.shift.template",
        string="Template",
        ondelete="restrict",
        check_company=True,
    )
    start_datetime = fields.Datetime(
        string="Start",
        required=True,
        compute="_compute_from_template",
        store=True,
        precompute=True,
        readonly=False,
    )
    end_datetime = fields.Datetime(
        string="End",
        required=True,
        compute="_compute_from_template",
        store=True,
        precompute=True,
        readonly=False,
    )
    break_minutes = fields.Integer(
        string="Break (minutes)",
        compute="_compute_from_template",
        store=True,
        precompute=True,
        readonly=False,
    )
    duration = fields.Float(
        string="Duration (hours)",
        compute="_compute_duration",
        store=True,
        precompute=True,
        help="Working hours of the shift, excluding the break.",
    )
    is_custom = fields.Boolean(
        string="Custom Time",
        compute="_compute_is_custom",
        store=True,
        precompute=True,
        help="The shift time differs from its template.",
    )
    company_id = fields.Many2one(
        "res.company",
        compute="_compute_company_id",
        store=True,
        precompute=True,
        index=True,
    )

    _employee_date_uniq = models.Constraint(
        "unique (employee_id, date)",
        "An employee can only have one shift per day.",
    )

    @api.depends("employee_id")
    def _compute_company_id(self):
        # Fall back to the current company so that a new line already has a
        # company: the employee and template dropdowns are filtered by it.
        for shift in self:
            shift.company_id = shift.employee_id.company_id or self.env.company

    @api.depends("employee_id", "date", "template_id")
    def _compute_display_name(self):
        for shift in self:
            parts = [shift.employee_id.name or "", str(shift.date or "")]
            if shift.template_id:
                parts.append(shift.template_id.code)
            shift.display_name = " - ".join(p for p in parts if p)

    def _get_tz(self):
        self.ensure_one()
        return pytz.timezone(self.employee_id.tz or self.env.user.tz or "UTC")

    def _get_time_values(self, hour_from, hour_to):
        """Start/end (naive UTC) of a shift working from ``hour_from`` to
        ``hour_to`` (local float hours) on this shift's date, in the employee's
        timezone. An end earlier than or equal to the start is on the next day."""
        self.ensure_one()
        tz = self._get_tz()
        start_local = datetime.combine(self.date, _float_to_time(hour_from))
        end_date = self.date + timedelta(days=1) if hour_to <= hour_from else self.date
        end_local = datetime.combine(end_date, _float_to_time(hour_to))
        return {
            "start_datetime": tz.localize(start_local).astimezone(pytz.utc).replace(tzinfo=None),
            "end_datetime": tz.localize(end_local).astimezone(pytz.utc).replace(tzinfo=None),
        }

    def _get_template_values(self):
        """Start/end (naive UTC) and break the template gives for this shift's
        employee and date, or None when there is no template or date."""
        self.ensure_one()
        template = self.template_id
        if not template or not self.date:
            return None
        return {
            **self._get_time_values(template.hour_from, template.hour_to),
            "break_minutes": template.break_minutes,
        }

    @api.depends("employee_id", "date", "template_id")
    def _compute_from_template(self):
        # Only depends on the template link, not on its hours: editing a template
        # must not change shifts that are already planned.
        for shift in self:
            values = shift._get_template_values()
            if values:
                shift.update(values)

    @api.depends("start_datetime", "end_datetime", "break_minutes")
    def _compute_duration(self):
        for shift in self:
            if shift.start_datetime and shift.end_datetime:
                span = (shift.end_datetime - shift.start_datetime).total_seconds() / 3600.0
                shift.duration = max(span - shift.break_minutes / 60.0, 0.0)
            else:
                shift.duration = 0.0

    @api.depends("start_datetime", "end_datetime", "break_minutes", "template_id")
    def _compute_is_custom(self):
        for shift in self:
            values = shift._get_template_values()
            shift.is_custom = bool(values) and any(
                shift[name] != value for name, value in values.items()
            )

    @api.model
    def get_planning_data(self, date_from, date_to):
        """Everything the planning grid needs for a date range, in one call.

        :param date_from: first day, ``YYYY-MM-DD``
        :param date_to: last day (inclusive), ``YYYY-MM-DD``

        Shifts are also returned for the 7 days before and the day after the
        range: the labour code checks look at the previous 7 days and at the
        rest before the next shift, and "copy previous week" may read the
        week before the first day.
        """
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        company_ids = self.env.companies.ids

        # hr.employee.public: readable by every internal user, unlike hr.employee
        employees = self.env["hr.employee.public"].search_read(
            [("company_id", "in", company_ids)],
            ["name", "job_title", "department_id"],
            order="name",
        )
        shifts = self.search_read(
            [
                ("date", ">=", date_from - timedelta(days=7)),
                ("date", "<=", date_to + timedelta(days=1)),
                ("employee_id", "in", [e["id"] for e in employees]),
            ],
            [
                "employee_id", "date", "template_id", "start_datetime", "end_datetime",
                "break_minutes", "duration", "is_custom",
            ],
        )
        Template = self.env["hr.shift.template"].with_context(active_test=False)
        used_template_ids = {s["template_id"][0] for s in shifts if s["template_id"]}
        templates = Template.search(
            ["|", ("active", "=", True), ("id", "in", list(used_template_ids))]
        ).read(["name", "code", "color", "hour_from", "hour_to", "break_minutes", "duration", "active"])

        return {
            "employees": [
                {
                    "id": e["id"],
                    "name": e["name"],
                    "job_title": e["job_title"] or "",
                    "department": e["department_id"][1] if e["department_id"] else "",
                }
                for e in employees
            ],
            "shifts": [
                {
                    "id": s["id"],
                    "employee_id": s["employee_id"][0],
                    "date": fields.Date.to_string(s["date"]),
                    "template_id": s["template_id"][0] if s["template_id"] else False,
                    "start": fields.Datetime.to_string(s["start_datetime"]),
                    "end": fields.Datetime.to_string(s["end_datetime"]),
                    "break_minutes": s["break_minutes"],
                    "duration": s["duration"],
                    "is_custom": s["is_custom"],
                }
                for s in shifts
            ],
            "templates": templates,
            "holidays": self._get_public_holidays(date_from, date_to),
            "can_edit": self.env.user.has_group("hr_shift_planning.group_shift_planner"),
            "limits": self._get_limits(),
        }

    @api.model
    def _get_limits(self):
        """Labour code warning limits, in hours (see the HR settings)."""
        # sudo: system parameters are not readable by planners and viewers
        params = self.env["ir.config_parameter"].sudo()
        limits = {}
        for name, default in DEFAULT_LIMITS.items():
            try:
                limits[name] = float(params.get_param(f"hr_shift_planning.{name}", default))
            except ValueError:
                limits[name] = default
        return limits

    @api.model
    def save_planning_changes(self, changes):
        """Apply the changes made in the planning grid, in one call.

        :param changes: list of ``{"employee_id": int, "date": "YYYY-MM-DD",
            "template_id": int | False, "custom": {...}}``:

            - with ``custom`` (``{"hour_from": float, "hour_to": float,
              "break_minutes": int}``, local hours): a shift with that time,
              linked to ``template_id`` if given;
            - otherwise ``template_id`` sets a shift with the template's time,
              and ``template_id: False`` removes the shift of that employee
              on that day.
        """
        if not changes:
            return True
        keys = {(c["employee_id"], fields.Date.to_date(c["date"])) for c in changes}
        existing = {
            (shift.employee_id.id, shift.date): shift
            for shift in self.search([
                ("employee_id", "in", list({k[0] for k in keys})),
                ("date", "in", list({k[1] for k in keys})),
            ])
        }
        to_delete = self.browse()
        to_create = []
        for change in changes:
            key = (change["employee_id"], fields.Date.to_date(change["date"]))
            shift = existing.get(key)
            template_id = change.get("template_id") or False
            custom = change.get("custom")
            if custom:
                record = shift or self.new({"employee_id": key[0], "date": key[1]})
                values = {
                    "template_id": template_id,
                    "break_minutes": custom.get("break_minutes") or 0,
                    **record._get_time_values(custom["hour_from"], custom["hour_to"]),
                }
                if shift:
                    shift.write(values)
                else:
                    to_create.append({"employee_id": key[0], "date": key[1], **values})
            elif not template_id:
                to_delete |= shift or self.browse()
            elif shift:
                shift.template_id = template_id
                # Also resets a custom time when the same template is applied again
                shift.write(shift._get_template_values())
            else:
                to_create.append({
                    "employee_id": key[0],
                    "date": key[1],
                    "template_id": template_id,
                })
        to_delete.unlink()
        self.create(to_create)
        return True

    @api.model
    def _get_public_holidays(self, date_from, date_to):
        """Public holidays (global time off of the company working schedule)
        as ``[{"date": "YYYY-MM-DD", "name": ...}]`` in the user's timezone."""
        tz = pytz.timezone(self.env.user.tz or "UTC")
        start_utc = tz.localize(datetime.combine(date_from, time.min)).astimezone(pytz.utc)
        end_utc = tz.localize(datetime.combine(date_to, time.max)).astimezone(pytz.utc)
        calendar_ids = self.env.companies.resource_calendar_id.ids
        leaves = self.env["resource.calendar.leaves"].search([
            ("resource_id", "=", False),
            ("calendar_id", "in", calendar_ids + [False]),
            ("company_id", "in", self.env.companies.ids + [False]),
            ("date_from", "<=", end_utc.replace(tzinfo=None)),
            ("date_to", ">=", start_utc.replace(tzinfo=None)),
        ])
        holidays = {}
        for leave in leaves:
            day = pytz.utc.localize(leave.date_from).astimezone(tz).date()
            last_day = pytz.utc.localize(leave.date_to - timedelta(seconds=1)).astimezone(tz).date()
            while day <= last_day:
                if date_from <= day <= date_to:
                    holidays.setdefault(fields.Date.to_string(day), leave.name)
                day += timedelta(days=1)
        return [{"date": d, "name": n} for d, n in sorted(holidays.items())]

    @api.constrains("start_datetime", "end_datetime", "break_minutes")
    def _check_times(self):
        for shift in self:
            if shift.end_datetime <= shift.start_datetime:
                raise ValidationError(self.env._("The shift end must be after its start."))
            if shift.break_minutes < 0:
                raise ValidationError(self.env._("The break cannot be negative."))
            span = (shift.end_datetime - shift.start_datetime).total_seconds() / 3600.0
            if shift.break_minutes / 60.0 >= span:
                raise ValidationError(self.env._("The break must be shorter than the shift."))

    @api.constrains("date", "start_datetime", "employee_id")
    def _check_start_on_date(self):
        for shift in self:
            tz = shift._get_tz()
            start_local = pytz.utc.localize(shift.start_datetime).astimezone(tz)
            if start_local.date() != shift.date:
                raise ValidationError(
                    self.env._(
                        "The shift of %(employee)s must start on %(date)s.",
                        employee=shift.employee_id.name,
                        date=shift.date,
                    )
                )
