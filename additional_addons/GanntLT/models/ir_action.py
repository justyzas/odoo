# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.addons.base.models.ir_actions import VIEW_TYPES
from .ir_ui_view import TIMELINE_VIEW

if TIMELINE_VIEW not in VIEW_TYPES:
    VIEW_TYPES.append(TIMELINE_VIEW)


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def _get_action_dict(self):
        result = super()._get_action_dict()
        if (
            self.env.uid
            and self.env.uid != 1
            and not self.env.user.has_group("GanntLT.group_gannt_lt_user")
            and result.get("res_model") in ("project.project", "project.task")
        ):
            if result.get("views"):
                result["views"] = [v for v in result["views"] if v[1] != "timeline"]
            if result.get("view_mode"):
                modes = [
                    m.strip()
                    for m in result["view_mode"].split(",")
                    if m.strip() and m.strip() != "timeline"
                ]
                result["view_mode"] = ",".join(modes)
        return result


class ActWindowView(models.Model):
    _inherit = "ir.actions.act_window.view"

    view_mode = fields.Selection(
        selection_add=[TIMELINE_VIEW], ondelete={"timeline": "cascade"}
    )
