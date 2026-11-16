# GanntLT UI & Navigation Rules (`UI.md`)

> **MANDATORY RULES FOR ALL DEVELOPERS AND AI AGENTS**
> These rules are non-negotiable. Never violate or revert any rule below during feature development, refactoring, or code audits.

## 1. Single Entry Point: Top-Right View Switcher (`Timeline` / 3-Bars Icon Only)
- The Gantt chart (`GanntLT`) must **ONLY** be accessed via the Odoo Control Panel **View Switcher** button in the top-right corner (the 3 horizontal bars icon `fa-tasks` with tooltip `"Timeline"`, view type `timeline`, next to Kanban, List, Calendar, and Activity).
- Registered via:
  - `project_project_timeline` (`ir.ui.view` of type `timeline` on `project.project` in `views/project_project_views.xml`) and `project.project._enable_timeline_view_modes()`.
  - `project_task_timeline` (`ir.ui.view` of type `timeline` on `project.task` in `views/project_task_views.xml`) and `project.task._enable_timeline_view_modes()`.

## 2. Strictly Forbidden: Odoo Menu Items (`ir.ui.menu` / `<menuitem>`)
- **NEVER** add a top-level Odoo App Switcher `<menuitem>` (e.g., `menu_gannt_lt_root` or any root menu item for `GanntLT`).
- **NEVER** add a menu item inside the Project app's top navigation bar (e.g., `menu_project_gannt_lt` / `"GanntLT Tvarkaraštis"` under `project.menu_main_pm`).
- `views/timeline_menus.xml` must **NOT** contain any `<menuitem>` tags.

## 3. Strictly Forbidden: Project Form Smart Button (`oe_stat_button`)
- **NEVER** add a `GanntLT` / `GanntLT Tvarkaraštis` smart button (`<button class="oe_stat_button" name="action_open_gannt_lt" ...>`) inside `//div[@name='button_box']` on the Project Form view (`project.edit_project` in `views/project_project_views.xml`).
- `edit_project_inherit_gannt_lt` in `views/project_project_views.xml` must only add the `date_deadline` (`Terminas (Deadline)`) field.

## 4. Three-Tier Permission Model
- **`Can View` (`GanntLT.group_gannt_lt_user`)**:
  - 100% read-only Gantt timeline (no dragging, no resizing, no link editing, no project/milestone/task creation or deletion).
  - Work timer buttons (`Pradėti`, `Pristabdyti`, `Baigti`, `Atnaujinti`) and `task_timer_widget` are completely hidden on Task forms and Sub-task lists.
- **`Can Track Time` (`GanntLT.group_gannt_lt_worker`)**:
  - 100% read-only Gantt timeline (same Gantt restrictions as `Can View`).
  - Work timer buttons and `task_timer_widget` are visible and active on Task forms and Sub-task lists.
- **`Can Edit` (`GanntLT.group_gannt_lt_manager`)**:
  - Full interactive Gantt editing and full work timer access.
