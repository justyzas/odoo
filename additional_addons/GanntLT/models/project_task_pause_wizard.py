from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import format_datetime
import math


class ProjectTaskPauseWizard(models.TransientModel):
    _name = "project.task.pause.wizard"
    _description = "Pristabdyti užduotį / Pause Task Wizard"

    task_id = fields.Many2one(
        "project.task",
        string="Užduotis / Task",
        required=True,
        readonly=True,
    )
    timer_start = fields.Datetime(
        string="Laikmačio pradžia / Timer Start",
        readonly=True,
    )
    duration_display = fields.Char(
        string="Užfiksuota trukmė / Elapsed Duration",
        compute="_compute_duration",
    )
    reason = fields.Text(
        string="Priežastis kodėl stabdoma / Pause Reason",
        required=True,
    )

    @api.depends("timer_start", "task_id")
    def _compute_duration(self):
        now = fields.Datetime.now()
        for rec in self:
            t_start = rec.timer_start or (rec.task_id and rec.task_id.timer_start)
            if t_start:
                diff_sec = max(0, int((now - t_start).total_seconds()))
                mins = diff_sec // 60
                hrs = mins // 60
                rem_mins = mins % 60
                rem_sec = diff_sec % 60
                if hrs > 0:
                    dur_str = f"{hrs} val. {rem_mins} min."
                elif mins > 0:
                    dur_str = f"{mins} min. {rem_sec} sek."
                else:
                    dur_str = f"{diff_sec} sek."
                h_float = round(max(0.01, diff_sec / 3600.0), 2)
                rec.duration_display = f"{dur_str} ({h_float} val.)"
            else:
                rec.duration_display = "0 sek."

    def action_confirm_pause(self):
        self.ensure_one()
        task = self.task_id
        if not task:
            return {"type": "ir.actions.act_window_close"}

        task._check_gannt_lt_timer_access()
        task.check_access("write")

        if task.active_user_id and task.active_user_id != self.env.user:
            can_manage = task._has_timer_override_rights()
            if not can_manage:
                raise UserError(
                    _("Pristabdyti užduotį gali tik ją vykdantis vartotojas (%s) arba projekto vadovas.")
                    % task.active_user_id.name
                )

        if not task.is_timer_running:
            # Timer already stopped (double click / stale dialog): never log time twice.
            return {"type": "ir.actions.act_window_close"}

        now = fields.Datetime.now()
        t_start = task.timer_start or self.timer_start or now
        diff_sec = max(0, int((now - t_start).total_seconds()))
        eff_sec = int(round((task.effective_hours or 0.0) * 3600))
        cur_total_sec = max(task.timer_total_seconds or 0, eff_sec)
        new_total_sec = cur_total_sec + diff_sec

        elapsed_min = max(1, int(math.ceil(diff_sec / 60.0 - 1e-9))) if diff_sec > 0 else 0
        elapsed_hours = elapsed_min / 60.0

        # 1. Register in Timesheets (account.analytic.line) without .sudo() bypass
        active_worker = task.active_user_id or self.env.user
        emp = False
        if active_worker == self.env.user:
            try:
                emp = active_worker.employee_id
            except AccessError:
                emp = False
        if not emp and "hr.employee" in self.env:
            emp_domain = [("user_id", "=", active_worker.id)]
            if task.company_id:
                emp_domain.append(("company_id", "in", [False, task.company_id.id]))
            emp = self.env["hr.employee"].sudo().search(emp_domain, limit=1)

        company = task.company_id or (emp and emp.company_id) or self.env.company
        if task.project_id and getattr(task.project_id, "allow_timesheets", True) and "account.analytic.line" in self.env and elapsed_hours > 0 and emp:
            self.env["account.analytic.line"].with_context(mail_notrack=True).create({
                "name": self.reason or _("Pristabdyta (Paused)"),
                "task_id": task.id,
                "project_id": task.project_id.id,
                "employee_id": emp.id,
                "user_id": active_worker.id,
                "company_id": company.id if company else False,
                "date": fields.Date.context_today(self),
                "unit_amount": elapsed_hours,
            })

        # 2. Post to Chatter with 1-line format: [Users name] Pristabdyta YYYY.MM.DD -HH:MM [Priežastis]
        user_name = self.env.user.name
        tz = self.env.user.tz or self.env.context.get("tz") or "Europe/Vilnius"
        try:
            pause_time_str = format_datetime(self.env, now, tz=tz, dt_format="yyyy.MM.dd -HH:mm")
        except Exception:
            pause_time_str = now.strftime("%Y.%m.%d -%H:%M")

        reason_clean = " ".join((self.reason or "").splitlines()).strip()
        chatter_body = f"{user_name} Pristabdyta {pause_time_str} {reason_clean}".strip()
        task.message_post(body=chatter_body, subtype_xmlid="mail.mt_note")

        # 3. Update task timer state and accumulated seconds
        pause_vals = {
            "is_timer_running": False,
            "timer_start": False,
            "timer_total_seconds": new_total_sec,
            "is_paused": True,
            "last_pause_reason": reason_clean or _("Pristabdyta"),
            "last_pause_date": now,
            "active_user_id": active_worker.id,
        }
        stop_vals, _info = task._work_stop_vals(now, new_total_sec, finished=False)
        pause_vals.update(stop_vals)

        from . import project_task as project_task_mod

        trusted_token = getattr(project_task_mod, "_TRUSTED_CONTEXT_TOKEN", None)
        write_ctx = {"gannt_auto_plan": True, "mail_notrack": True}
        prop_ctx = {"mail_notrack": True}
        if trusted_token is not None:
            write_ctx["_gannt_lt_trusted_token"] = trusted_token
            prop_ctx["_gannt_lt_trusted_token"] = trusted_token

        task.with_context(**write_ctx).write(pause_vals)
        task.with_context(**prop_ctx)._propagate_deadline_upward()

        return {"type": "ir.actions.act_window_close"}
