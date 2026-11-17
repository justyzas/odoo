from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Defaults from the Lithuanian Labour Code: at least 11 consecutive hours of
# rest between shifts, at most 12 working hours per shift and 48 per 7 days.
DEFAULT_LIMITS = {
    "min_rest_hours": 11.0,
    "max_shift_hours": 12.0,
    "max_week_hours": 48.0,
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shift_min_rest_hours = fields.Float(
        string="Minimum Rest Between Shifts",
        config_parameter="hr_shift_planning.min_rest_hours",
        default=DEFAULT_LIMITS["min_rest_hours"],
    )
    shift_max_shift_hours = fields.Float(
        string="Maximum Shift Length",
        config_parameter="hr_shift_planning.max_shift_hours",
        default=DEFAULT_LIMITS["max_shift_hours"],
    )
    shift_max_week_hours = fields.Float(
        string="Maximum Hours per 7 Days",
        config_parameter="hr_shift_planning.max_week_hours",
        default=DEFAULT_LIMITS["max_week_hours"],
    )

    @api.constrains("shift_min_rest_hours", "shift_max_shift_hours", "shift_max_week_hours")
    def _check_shift_limits(self):
        for settings in self:
            if min(
                settings.shift_min_rest_hours,
                settings.shift_max_shift_hours,
                settings.shift_max_week_hours,
            ) < 0:
                raise ValidationError(self.env._("Shift planning limits cannot be negative."))
