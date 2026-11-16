# -*- coding: utf-8 -*-
from . import models
from . import controllers
import logging
from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def uninstall_hook(env):
    """Cleanly removes 'timeline' from view_mode of Odoo project window actions

    and restores schema defaults modified at module init so views and models
    continue to function seamlessly after GanntLT is uninstalled.
    """
    actions_to_clean = [
        "project.action_view_task",
        "project.project_task_action_from_partner",
        "project.act_project_project_2_project_task_all",
        "project.action_view_all_task",
        "project.open_view_project_all",
        "project.open_view_project_all_group_stage",
    ]
    seen_ids = set()
    actions = env["ir.actions.act_window"]
    for xml_id in actions_to_clean:
        action = env.ref(xml_id, raise_if_not_found=False)
        if action and action.id not in seen_ids:
            seen_ids.add(action.id)
            actions |= action
    extra_actions = env["ir.actions.act_window"].search([
        ("view_mode", "ilike", "timeline"),
        ("res_model", "in", ["project.task", "project.project"]),
    ])
    for action in extra_actions:
        if action.id not in seen_ids:
            seen_ids.add(action.id)
            actions |= action

    for action in actions:
        if action.view_mode:
            modes = [m.strip() for m in action.view_mode.split(",") if m.strip() and m.strip() != "timeline"]
            new_mode = ",".join(modes)
            if new_mode != action.view_mode:
                action.write({"view_mode": new_mode})
                _logger.info("GanntLT uninstall_hook: Removed 'timeline' from action %s (new view_mode: %s)", action.name, action.view_mode)

    if column_exists(env.cr, "project_project", "billing_type"):
        env.cr.execute("""
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = 'project_project'
              AND a.attname = 'billing_type'
        """)
        row = env.cr.fetchone()
        if row and row[0] and "not_billable" in str(row[0]):
            env.cr.execute("ALTER TABLE project_project ALTER COLUMN billing_type DROP DEFAULT")
            _logger.info("GanntLT uninstall_hook: Dropped SQL default on project_project.billing_type")
