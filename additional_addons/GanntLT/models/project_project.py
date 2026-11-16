# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models
from odoo.tools.sql import column_exists
from .project_task import calculate_lithuanian_working_hours

_logger = logging.getLogger(__name__)


class ProjectProject(models.Model):
    _inherit = "project.project"

    date_deadline = fields.Date(
        string="Deadline",
        copy=False,
        index=True,
        tracking=True,
        help="Final deadline for the project. If later than the planned date, indicates delay.",
    )

    def _auto_init(self):
        res = super()._auto_init()
        if column_exists(self.env.cr, "project_project", "billing_type"):
            self.env.cr.execute("""
                SELECT pg_get_expr(d.adbin, d.adrelid)
                FROM pg_attrdef d
                JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'project_project'
                  AND a.attname = 'billing_type'
            """)
            row = self.env.cr.fetchone()
            current_default = row[0] if row else None
            if not current_default or "not_billable" not in str(current_default):
                self.env.cr.execute("""
                    ALTER TABLE project_project ALTER COLUMN billing_type SET DEFAULT 'not_billable'
                """)
        return res

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "billing_type" in self._fields and not res.get("billing_type"):
            res["billing_type"] = "not_billable"
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "billing_type" in self._fields and not vals.get("billing_type"):
                vals["billing_type"] = "not_billable"
            if "allocated_hours" not in vals:
                s = vals.get("date_start")
                e = vals.get("date") or vals.get("date_deadline")
                if s and e:
                    try:
                        s_dt = fields.Date.to_date(s) if isinstance(s, str) else s
                        e_dt = fields.Date.to_date(e) if isinstance(e, str) else e
                        w_h = calculate_lithuanian_working_hours(s_dt, e_dt) if (s_dt and e_dt) else 0.0
                        if w_h > 0:
                            vals["allocated_hours"] = w_h
                    except (ValueError, TypeError) as exc:
                        _logger.debug("Skipping project allocated_hours calc on create: %s", exc)
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        date_keys = ("date_start", "date", "date_deadline")
        if any(k in vals for k in date_keys) and "allocated_hours" not in vals:
            if len(self) == 1:
                rec = self[0]
                s_val = vals["date_start"] if "date_start" in vals else rec.date_start
                d_val = vals["date"] if "date" in vals else rec.date
                dl_val = vals["date_deadline"] if "date_deadline" in vals else rec.date_deadline
                e_val = d_val or dl_val
                if s_val and e_val:
                    try:
                        s_dt = fields.Date.to_date(s_val) if isinstance(s_val, str) else s_val
                        e_dt = fields.Date.to_date(e_val) if isinstance(e_val, str) else e_val
                        w_h = calculate_lithuanian_working_hours(s_dt, e_dt) if (s_dt and e_dt) else 0.0
                        if w_h > 0:
                            vals["allocated_hours"] = w_h
                    except (ValueError, TypeError) as exc:
                        _logger.debug("Skipping project allocated_hours calc on write: %s", exc)
                elif not s_val and not e_val:
                    vals["allocated_hours"] = 0.0
                return super().write(vals)

            res = super().write(vals)
            for rec in self:
                e_val = rec.date or rec.date_deadline
                if rec.date_start and e_val:
                    try:
                        s_dt = fields.Date.to_date(rec.date_start) if isinstance(rec.date_start, str) else rec.date_start
                        e_dt = fields.Date.to_date(e_val) if isinstance(e_val, str) else e_val
                        w_h = calculate_lithuanian_working_hours(s_dt, e_dt) if (s_dt and e_dt) else 0.0
                        if w_h > 0 and rec.allocated_hours != w_h:
                            super(ProjectProject, rec.with_context(mail_notrack=True)).write({"allocated_hours": w_h})
                    except (ValueError, TypeError) as exc:
                        _logger.debug("Skipping batch project allocated_hours calc on write: %s", exc)
                elif not rec.date_start and not e_val and rec.allocated_hours:
                    super(ProjectProject, rec.with_context(mail_notrack=True)).write({"allocated_hours": 0.0})
            return res
        return super().write(vals)

    def action_open_gannt_lt(self):
        """Opens GanntLT filtered for this project."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "name": f"{self.name} - GanntLT",
            "tag": "gannt_lt.action",
            "context": {
                "default_project_id": self.id,
                "active_id": self.id,
            },
        }

    def action_open_all_in_one_timeline(self):
        """Alias for backward compatibility."""
        return self.action_open_gannt_lt()

    @api.model
    def _enable_timeline_view_modes(self):
        """Safely injects 'timeline' into project window actions without clobbering third-party modes,
        and ensures no standalone Odoo menus (App Switcher or Project navbar) remain in the database.
        """
        for menu_xml_id in ("GanntLT.menu_gannt_lt_root", "GanntLT.menu_project_gannt_lt"):
            menu = self.env.ref(menu_xml_id, raise_if_not_found=False)
            if menu:
                menu.sudo().unlink()
        extra_menus = self.env["ir.ui.menu"].sudo().search([
            ("name", "in", ["GanntLT", "GanntLT Tvarkaraštis"]),
        ])
        if extra_menus:
            extra_menus.unlink()
        action_xml_ids = [
            "project.open_view_project_all",
            "project.open_view_project_all_group_stage",
        ]
        for xml_id in action_xml_ids:
            action = self.env.ref(xml_id, raise_if_not_found=False)
            if not action or not action.view_mode:
                continue
            modes = [m.strip() for m in action.view_mode.split(",") if m.strip()]
            if "timeline" not in modes:
                modes.append("timeline")
                action.write({"view_mode": ",".join(modes)})


