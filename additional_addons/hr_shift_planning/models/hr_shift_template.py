from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HrShiftTemplate(models.Model):
    _name = "hr.shift.template"
    _description = "Shift Template"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        size=3,
        help="Short code (1-3 characters) shown in the planning grid cells.",
    )
    color = fields.Char(default="#FFE08A", help="Cell background color in the planning grid.")
    hour_from = fields.Float(string="Start", required=True, default=6.0)
    hour_to = fields.Float(
        string="End",
        required=True,
        default=14.0,
        help="If the end is earlier than or equal to the start, the shift ends on the next day.",
    )
    break_minutes = fields.Integer(string="Break (minutes)", default=0)
    duration = fields.Float(
        string="Duration (hours)",
        compute="_compute_duration",
        store=True,
        help="Working hours of the shift, excluding the break.",
    )
    is_overnight = fields.Boolean(
        string="Ends Next Day",
        compute="_compute_duration",
        store=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )

    _code_company_uniq = models.Constraint(
        "unique (code, company_id)",
        "The shift template code must be unique per company.",
    )

    @api.depends("hour_from", "hour_to", "break_minutes")
    def _compute_duration(self):
        for template in self:
            span = template._get_span_hours()
            template.is_overnight = template.hour_to <= template.hour_from
            template.duration = max(span - template.break_minutes / 60.0, 0.0)

    def _get_span_hours(self):
        """Length of the shift in hours including the break. An end that is
        earlier than or equal to the start means the shift ends on the next day."""
        self.ensure_one()
        span = self.hour_to - self.hour_from
        if span <= 0:
            span += 24.0
        return span

    @api.constrains("code")
    def _check_code(self):
        for template in self:
            if not (template.code or "").strip():
                raise ValidationError(self.env._("The shift template code cannot be empty."))

    @api.constrains("hour_from", "hour_to")
    def _check_hours(self):
        for template in self:
            for hour in (template.hour_from, template.hour_to):
                if hour < 0 or hour >= 24:
                    raise ValidationError(
                        self.env._("Shift start and end must be between 00:00 and 23:59.")
                    )

    @api.constrains("break_minutes", "hour_from", "hour_to")
    def _check_break(self):
        for template in self:
            if template.break_minutes < 0:
                raise ValidationError(self.env._("The break cannot be negative."))
            if template.break_minutes / 60.0 >= template._get_span_hours():
                raise ValidationError(self.env._("The break must be shorter than the shift."))
