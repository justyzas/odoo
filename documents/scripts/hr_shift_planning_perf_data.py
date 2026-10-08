# Performance test data for hr_shift_planning (NFR-5): 200 employees with a
# month of rotating shifts. Run it in the Odoo shell (bash):
#
#   Create:  python odoo-bin shell -c odoo.conf -d <db> < documents/scripts/hr_shift_planning_perf_data.py
#   Delete:  PERF_MODE=delete python odoo-bin shell -c odoo.conf -d <db> < documents/scripts/hr_shift_planning_perf_data.py
#
# Everything created is in the "Perf Test" department, named "Perf Test NNN",
# so it is easy to filter out in the grid and to delete again.
# Shifts are created for the current month, using the first 3 active shift
# templates: 5 working days, 2 days off, rotating template every week.

import os
from datetime import date, timedelta

EMPLOYEE_COUNT = 200
DEPARTMENT_NAME = "Perf Test"
NAME_PREFIX = "Perf Test"

mode = os.environ.get("PERF_MODE", "create")
Employee = env["hr.employee"].with_context(active_test=False)  # noqa: F821 (env is given by the shell)
Department = env["hr.department"]  # noqa: F821

if mode == "delete":
    employees = Employee.search([("name", "=like", f"{NAME_PREFIX} %")])
    print(f"Deleting {len(employees)} employees and their shifts...")
    env["hr.shift"].search([("employee_id", "in", employees.ids)]).unlink()  # noqa: F821
    employees.unlink()
    Department.search([("name", "=", DEPARTMENT_NAME)]).unlink()
else:
    templates = env["hr.shift.template"].search([], limit=3)  # noqa: F821
    if not templates:
        raise SystemExit("Create at least one shift template first (Working Hours > Templates).")
    department = Department.search([("name", "=", DEPARTMENT_NAME)], limit=1) or Department.create(
        {"name": DEPARTMENT_NAME}
    )
    existing = Employee.search_count([("name", "=like", f"{NAME_PREFIX} %")])
    print(f"Creating {EMPLOYEE_COUNT} employees ({existing} already exist)...")
    employees = Employee.create([
        {
            "name": f"{NAME_PREFIX} {existing + i + 1:03d}",
            "department_id": department.id,
            "tz": "Europe/Vilnius",
        }
        for i in range(EMPLOYEE_COUNT)
    ])

    first_day = date.today().replace(day=1)
    next_month = (first_day + timedelta(days=32)).replace(day=1)
    days = [first_day + timedelta(days=n) for n in range((next_month - first_day).days)]
    shift_values = []
    for index, employee in enumerate(employees):
        for day in days:
            day_in_cycle = (day.toordinal() + index) % 7
            if day_in_cycle >= 5:  # 2 days off
                continue
            week = (day.toordinal() + index) // 7
            template = templates[(week + index) % len(templates)]
            shift_values.append({"employee_id": employee.id, "date": day, "template_id": template.id})
    print(f"Creating {len(shift_values)} shifts for {first_day:%Y-%m}...")
    env["hr.shift"].create(shift_values)  # noqa: F821

env.cr.commit()  # noqa: F821
print("Done.")
