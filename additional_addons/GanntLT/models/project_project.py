# -*- coding: utf-8 -*-
import logging
from datetime import datetime, time
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
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
    actual_finish_date = fields.Date(
        string="Faktinė pabaiga",
        copy=False,
        readonly=True,
        help="Actual completion date recorded when the project is marked Done / Complete.",
    )
    deadline_is_auto = fields.Boolean(
        string="Auto Deadline",
        default=False,
        copy=False,
        help="True when date_deadline was automatically set by completing the project late.",
    )
    has_been_approved = fields.Boolean(
        string="Patvirtintas planas (Užrakinta)",
        default=False,
        copy=False,
        readonly=True,
        help="Latches once the project is completed so planned dates cannot be reversed manually.",
    )
    can_edit_gannt_dates = fields.Boolean(
        compute="_compute_can_edit_gannt_dates",
        string="Can Edit GanntLT Dates",
    )

    @api.depends_context("uid")
    def _compute_can_edit_gannt_dates(self):
        task_env = self.env["project.task"]
        # Non-Gantt users (GanntLT = No) keep standard Odoo editability on project forms.
        can_edit = task_env._has_edit_rights() or not task_env._has_view_rights()
        for rec in self:
            rec.can_edit_gannt_dates = can_edit

    def _is_project_completed(self):
        """Returns True when the project is in a Done/Complete status or closed stage."""
        self.ensure_one()
        if (self.last_update_status or "") == "done":
            return True
        stage = self.stage_id
        if stage:
            if getattr(stage, "is_closed", False) or getattr(stage, "fold", False):
                return True
            st_name = (stage.name or "").lower()
            if any(k in st_name for k in ("done", "closed", "completed", "atlikt", "užbaigt")):
                return True
        return False

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
        is_auto_finish = bool(self.env.context.get("gannt_project_auto_finish"))
        was_done = {rec.id: rec._is_project_completed() for rec in self} if not is_auto_finish else {}

        task_env = self.env["project.task"]
        if not is_auto_finish and task_env._has_view_rights() and any(k in vals for k in ("date_start", "date", "date_deadline")):
            task_env._check_gannt_lt_edit_access()

        if not is_auto_finish:
            for rec in self:
                if not rec.has_been_approved:
                    continue
                if "date_start" in vals:
                    new_s = fields.Date.to_date(vals["date_start"]) if vals.get("date_start") else False
                    if not new_s and rec.date_start:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Pradžios datos išvalyti negalima.") % rec.name)
                    if new_s and rec.date_start and new_s != rec.date_start:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Pradžios datos perkelti negalima.") % rec.name)
                if "date" in vals:
                    new_e = fields.Date.to_date(vals["date"]) if vals.get("date") else False
                    if not new_e and rec.date:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Pabaigos datos išvalyti negalima.") % rec.name)
                    if new_e and rec.date and new_e < rec.date:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Pabaigos datos sutrumpinti negalima.") % rec.name)
                if "date_deadline" in vals:
                    new_dl = fields.Date.to_date(vals["date_deadline"]) if vals.get("date_deadline") else False
                    if not new_dl and rec.date_deadline:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Termino (Deadline) išvalyti negalima.") % rec.name)
                    if new_dl and rec.date_deadline and new_dl < rec.date_deadline:
                        raise ValidationError(_("Projektas „%s“ jau buvo užbaigtas. Termino (Deadline) sutrumpinti negalima.") % rec.name)

        if "date_deadline" in vals and not is_auto_finish and "deadline_is_auto" not in vals:
            vals["deadline_is_auto"] = False

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
                res = super().write(vals)
            else:
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
        else:
            res = super().write(vals)

        if not is_auto_finish and ("last_update_status" in vals or "last_update_id" in vals or "stage_id" in vals or "date" in vals):
            today = fields.Date.context_today(self)
            for rec in self:
                now_done = rec._is_project_completed()
                prev_done = was_done.get(rec.id, False)
                if now_done and (not prev_done or not rec.actual_finish_date or not rec.has_been_approved):
                    upd = {"actual_finish_date": rec.actual_finish_date or today, "has_been_approved": True}
                    fin_date = upd["actual_finish_date"]
                    if not rec.date:
                        upd["date"] = fin_date
                    elif rec.date and fin_date > rec.date:
                        if not rec.date_deadline or rec.deadline_is_auto or fin_date > rec.date_deadline:
                            upd["date_deadline"] = fin_date
                            upd["deadline_is_auto"] = True
                    elif rec.date and fin_date <= rec.date and rec.deadline_is_auto:
                        upd["date_deadline"] = False
                        upd["deadline_is_auto"] = False
                    super(ProjectProject, rec.with_context(gannt_project_auto_finish=True, mail_notrack=True)).write(upd)
                elif not now_done and prev_done:
                    upd = {}
                    if rec.actual_finish_date:
                        upd["actual_finish_date"] = False
                    if rec.deadline_is_auto:
                        upd["date_deadline"] = False
                        upd["deadline_is_auto"] = False
                    if upd:
                        super(ProjectProject, rec.with_context(gannt_project_auto_finish=True, mail_notrack=True)).write(upd)
        return res

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


