# Agent Rules for GanntLT (`AGENTS.md`)

You MUST read and strictly enforce the UI and architecture rules in `custom_addons/GanntLT/UI.md` (and below) before modifying any file in this repository.

## Mandatory UI & Navigation Invariants (NEVER VIOLATE)

1. **View Switcher Only (3-Bars `Timeline` Icon)**:
   - The GanntLT chart must **ONLY** be accessible through the top-right Odoo **View Switcher** button (`view_type=timeline`, the 3 horizontal bars icon `fa-tasks` with tooltip `"Timeline"`).
2. **NO Odoo Menu Items (`<menuitem>` / `ir.ui.menu`)**:
   - **NEVER** add `GanntLT` to the Odoo App Switcher menu (`menu_gannt_lt_root`).
   - **NEVER** add `GanntLT Tvarkaraštis` (or any GanntLT link) to the Project top navigation bar (`menu_project_gannt_lt` / `project.menu_main_pm`).
   - Do **NOT** add any `<menuitem>` tags to `views/timeline_menus.xml` or any other XML file, even during "completeness" or "audit" passes.
3. **NO Project Form Smart Button (`oe_stat_button`)**:
   - **NEVER** add a `GanntLT Tvarkaraštis` smart button (`oe_stat_button` / `action_open_gannt_lt`) inside `//div[@name='button_box']` in `views/project_project_views.xml` (`project.edit_project`).
4. **3-Tier Access Control**:
   - Preserve the 3-tier security groups in `security/gannt_lt_security.xml`:
     - `GanntLT.group_gannt_lt_user` (`Can View`): read-only Gantt, timer hidden.
     - `GanntLT.group_gannt_lt_worker` (`Can Track Time`): read-only Gantt, timer visible/usable on tasks.
     - `GanntLT.group_gannt_lt_manager` (`Can Edit`): full Gantt editing and timer access.
5. **OWL QWeb Expressions**:
   - Do **NOT** call JavaScript globals like `String(...)`, `Number(...)`, or `Boolean(...)` inside OWL QWeb templates (`static/src/**/*.xml`), as OWL compiles bare identifiers to `ctx.<ident>(...)` and throws `TypeError: ctx.String is not a function`. Use string concatenation (`'' + val`) or component methods instead.
6. **Keep Release `.zip` in Sync with Git Releases**:
   - Whenever committing/pushing a version update or release to Git, ALWAYS rebuild the single versioned archive `c:\Users\Beno E16\Downloads\Odoo\GanntLT_v<major.minor>.zip` (e.g., `GanntLT_v3.0.zip`, never create duplicate `GanntLT.zip`) and upload it to the GitHub Release at the same time.
