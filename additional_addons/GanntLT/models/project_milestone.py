# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ProjectMilestone(models.Model):
    _inherit = "project.milestone"

    date_start = fields.Date(string="Start Date", copy=False, index=True)
    planned_date_end = fields.Date(string="Planned End Date", copy=False, index=True)

    # NOTE: the milestone `deadline` is user data. It is NEVER derived from the planned end date or
    # from task deadlines (that used to create phantom deadlines and fake delays). Delays are computed
    # on the fly by the timeline from planned_date_end vs deadline.

    @api.constrains("date_start", "planned_date_end")
    def _check_planned_dates(self):
        for ms in self:
            if ms.date_start and ms.planned_date_end and ms.planned_date_end < ms.date_start:
                raise ValidationError(
                    _("Gairės pabaigos data negali būti ankstesnė už pradžios datą. (Planned End Date must be on or after Start Date.)")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("deadline") and not vals.get("planned_date_end"):
                vals["planned_date_end"] = vals["deadline"]
            if vals.get("planned_date_end") and not vals.get("date_start"):
                p_end = fields.Date.to_date(vals["planned_date_end"])
                if p_end:
                    vals["date_start"] = p_end - timedelta(days=7)
            elif vals.get("date_start") and not vals.get("planned_date_end"):
                p_start = fields.Date.to_date(vals["date_start"])
                if p_start:
                    vals["planned_date_end"] = p_start + timedelta(days=7)
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        res = super().write(vals)
        if any(k in vals for k in ("deadline", "planned_date_end", "date_start")):
            for ms in self:
                sync_vals = {}
                if ms.deadline and not ms.planned_date_end and "planned_date_end" not in vals:
                    sync_vals["planned_date_end"] = ms.deadline
                eff_end = sync_vals.get("planned_date_end", ms.planned_date_end)
                eff_start = ms.date_start
                if eff_end and not eff_start and "date_start" not in vals:
                    sync_vals["date_start"] = eff_end - timedelta(days=7)
                elif eff_start and not eff_end and "planned_date_end" not in vals:
                    sync_vals["planned_date_end"] = eff_start + timedelta(days=7)
                if sync_vals:
                    super(ProjectMilestone, ms).write(sync_vals)
        return res

    @api.onchange("planned_date_end")
    def _onchange_planned_date_end(self):
        for ms in self:
            if ms.planned_date_end and not ms.date_start:
                ms.date_start = ms.planned_date_end - timedelta(days=7)

    @api.onchange("deadline")
    def _onchange_deadline(self):
        for ms in self:
            if ms.deadline and not ms.planned_date_end:
                ms.planned_date_end = ms.deadline
            if ms.planned_date_end and not ms.date_start:
                ms.date_start = ms.planned_date_end - timedelta(days=7)
