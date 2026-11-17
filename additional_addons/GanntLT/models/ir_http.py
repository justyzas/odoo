# -*- coding: utf-8 -*-
import hashlib
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        user = self.env.user
        if user and not user._is_public():
            try:
                gannt_tier = (
                    "edit"
                    if user.has_group("GanntLT.group_gannt_lt_manager")
                    else "worker"
                    if user.has_group("GanntLT.group_gannt_lt_worker")
                    else "view"
                    if user.has_group("GanntLT.group_gannt_lt_user")
                    else "none"
                )
            except ValueError:
                return info
            base_hash = info.get("registry_hash") or ""
            info["registry_hash"] = hashlib.sha256(
                f"{base_hash}:gannt_lt:{gannt_tier}".encode("utf-8")
            ).hexdigest()[:32]
        return info
