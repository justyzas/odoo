from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError


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
        readonly=False,
    )
    end_datetime = fields.Datetime(
        string="End",
        required=True,
        compute="_compute_from_template",
        store=True,
        readonly=False,
    )
    break_minutes = fields.Integer(
        string="Break (minutes)",
        compute="_compute_from_template",
        store=True,
        readonly=False,
    )
    duration = fields.Float(
        string="Duration (hours)",
        compute="_compute_duration",
        store=True,
        help="Working hours of the shift, excluding the break.",
    )
    is_custom = fields.Boolean(
        string="Custom Time",
        compute="_compute_is_custom",
        store=True,
        help="The shift time differs from its template.",
    )
    company_id = fields.Many2one(
        "res.company",
        compute="_compute_company_id",
        store=True,
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

    def _get_template_values(self):
        """Start/end (naive UTC) and break the template gives for this shift's
        employee and date, or None when there is no template or date."""
        self.ensure_one()
        template = self.template_id
        if not template or not self.date:
            return None
        tz = self._get_tz()
        start_local = datetime.combine(self.date, _float_to_time(template.hour_from))
        end_date = self.date + timedelta(days=1) if template.is_overnight else self.date
        end_local = datetime.combine(end_date, _float_to_time(template.hour_to))
        return {
            "start_datetime": tz.localize(start_local).astimezone(pytz.utc).replace(tzinfo=None),
            "end_datetime": tz.localize(end_local).astimezone(pytz.utc).replace(tzinfo=None),
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
