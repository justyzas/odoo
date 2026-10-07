{
    "name": "Employee Shift Planning",
    "version": "19.0.1.0.0",
    "category": "Human Resources/Employees",
    "summary": "Plan employee shifts in a monthly grid using shift templates",
    "author": "SEFU, MB developers",
    "license": "LGPL-3",
    "depends": ["hr"],
    "data": [
        "security/hr_shift_planning_security.xml",
        "security/ir.model.access.csv",
        "views/hr_shift_template_views.xml",
        "views/hr_shift_views.xml",
        "views/hr_shift_planning_actions.xml",
        "views/hr_shift_planning_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hr_shift_planning/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "hr_shift_planning/static/tests/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
