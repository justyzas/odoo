from datetime import date, datetime

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestHrShift(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Shift = cls.env["hr.shift"]
        cls.employee = cls.env["hr.employee"].create(
            {"name": "Shift Employee", "tz": "Europe/Vilnius"}
        )
        Template = cls.env["hr.shift.template"]
        cls.morning = Template.create(
            {"name": "Morning", "code": "M", "hour_from": 6.0, "hour_to": 14.0, "break_minutes": 30}
        )
        cls.night = Template.create(
            {"name": "Night", "code": "N", "hour_from": 22.0, "hour_to": 6.0, "break_minutes": 30}
        )
        cls.planner = new_test_user(
            cls.env, login="shift_planner2", groups="hr_shift_planning.group_shift_planner"
        )
        cls.viewer = new_test_user(
            cls.env, login="shift_viewer2", groups="hr_shift_planning.group_shift_viewer"
        )
        cls.user = new_test_user(cls.env, login="shift_plain_user2", groups="base.group_user")

    def _create(self, template=None, day=date(2026, 10, 15), **vals):
        values = {
            "employee_id": self.employee.id,
            "date": day,
            "template_id": (template or self.morning).id,
        }
        values.update(vals)
        return self.Shift.create(values)

    def test_day_shift_from_template(self):
        # Vilnius is UTC+3 in October (summer time)
        shift = self._create()
        self.assertEqual(shift.start_datetime, datetime(2026, 10, 15, 3, 0))
        self.assertEqual(shift.end_datetime, datetime(2026, 10, 15, 11, 0))
        self.assertEqual(shift.break_minutes, 30)
        self.assertAlmostEqual(shift.duration, 7.5)
        self.assertFalse(shift.is_custom)

    def test_day_shift_winter_time(self):
        # Vilnius is UTC+2 in December
        shift = self._create(day=date(2026, 12, 1))
        self.assertEqual(shift.start_datetime, datetime(2026, 12, 1, 4, 0))

    def test_night_shift_ends_next_day(self):
        shift = self._create(template=self.night)
        self.assertEqual(shift.start_datetime, datetime(2026, 10, 15, 19, 0))
        self.assertEqual(shift.end_datetime, datetime(2026, 10, 16, 3, 0))
        self.assertAlmostEqual(shift.duration, 7.5)

    def test_one_shift_per_day(self):
        self._create()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._create(template=self.night)

    def test_custom_time(self):
        shift = self._create()
        shift.end_datetime = datetime(2026, 10, 15, 9, 0)
        self.assertTrue(shift.is_custom)
        self.assertAlmostEqual(shift.duration, 5.5)

    def test_shift_without_template(self):
        shift = self.Shift.create({
            "employee_id": self.employee.id,
            "date": date(2026, 10, 15),
            "start_datetime": datetime(2026, 10, 15, 3, 0),
            "end_datetime": datetime(2026, 10, 15, 9, 0),
        })
        self.assertAlmostEqual(shift.duration, 6.0)
        self.assertFalse(shift.is_custom)

    def test_template_change_keeps_planned_shifts(self):
        shift = self._create()
        self.morning.hour_from = 7.0
        self.assertEqual(shift.start_datetime, datetime(2026, 10, 15, 3, 0))

    def test_template_in_use_cannot_be_deleted(self):
        self._create()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.morning.unlink()

    def test_end_before_start(self):
        with self.assertRaises(ValidationError):
            self.Shift.create({
                "employee_id": self.employee.id,
                "date": date(2026, 10, 15),
                "start_datetime": datetime(2026, 10, 15, 9, 0),
                "end_datetime": datetime(2026, 10, 15, 3, 0),
            })

    def test_start_must_be_on_date(self):
        with self.assertRaises(ValidationError):
            self.Shift.create({
                "employee_id": self.employee.id,
                "date": date(2026, 10, 16),
                "start_datetime": datetime(2026, 10, 15, 3, 0),
                "end_datetime": datetime(2026, 10, 15, 9, 0),
            })

    def test_planner_can_manage(self):
        shift = self.Shift.with_user(self.planner).create({
            "employee_id": self.employee.id,
            "date": date(2026, 10, 15),
            "template_id": self.morning.id,
        })
        shift.template_id = self.night
        shift.unlink()

    def test_viewer_read_only(self):
        shift = self._create()
        self.assertEqual(shift.with_user(self.viewer).template_id, self.morning)
        with self.assertRaises(AccessError):
            shift.with_user(self.viewer).write({"template_id": self.night.id})

    def test_plain_user_no_access(self):
        shift = self._create()
        with self.assertRaises(AccessError):
            shift.with_user(self.user).read(["date"])

    def test_planning_data(self):
        shift = self._create()
        self._create(day=date(2026, 11, 2))  # outside the requested range
        self.env["resource.calendar.leaves"].create({
            "name": "Test Holiday",
            "calendar_id": self.env.company.resource_calendar_id.id,
            "date_from": datetime(2026, 10, 19, 21, 0),
            "date_to": datetime(2026, 10, 20, 20, 59, 59),
        })
        self.viewer.tz = "Europe/Vilnius"
        data = self.Shift.with_user(self.viewer).get_planning_data("2026-10-01", "2026-10-31")

        self.assertIn(self.employee.id, [e["id"] for e in data["employees"]])
        self.assertEqual([s["id"] for s in data["shifts"]], [shift.id])
        self.assertEqual(data["shifts"][0]["date"], "2026-10-15")
        self.assertEqual(data["shifts"][0]["start"], "2026-10-15 03:00:00")
        self.assertIn(self.morning.id, [t["id"] for t in data["templates"]])
        self.assertIn({"date": "2026-10-20", "name": "Test Holiday"}, data["holidays"])

    def test_planning_data_includes_archived_used_template(self):
        self._create()
        self.morning.active = False
        data = self.Shift.get_planning_data("2026-10-01", "2026-10-31")
        self.assertIn(self.morning.id, [t["id"] for t in data["templates"]])

    def test_planning_data_plain_user_no_access(self):
        with self.assertRaises(AccessError):
            self.Shift.with_user(self.user).get_planning_data("2026-10-01", "2026-10-31")

    def test_planning_data_includes_neighbour_days(self):
        before = self._create(day=date(2026, 9, 25))  # 6 days before October
        self._create(day=date(2026, 9, 24))  # 7 days before: not needed
        after = self._create(day=date(2026, 11, 1))  # day after the month
        data = self.Shift.get_planning_data("2026-10-01", "2026-10-31")
        self.assertEqual({s["id"] for s in data["shifts"]}, {before.id, after.id})

    def test_planning_data_limits(self):
        data = self.Shift.with_user(self.viewer).get_planning_data("2026-10-01", "2026-10-31")
        self.assertEqual(
            data["limits"], {"min_rest_hours": 11.0, "max_shift_hours": 12.0, "max_week_hours": 48.0}
        )
        settings = self.env["res.config.settings"].create({"shift_max_shift_hours": 10.0})
        settings.execute()
        data = self.Shift.with_user(self.viewer).get_planning_data("2026-10-01", "2026-10-31")
        self.assertEqual(data["limits"]["max_shift_hours"], 10.0)

    def test_planning_data_can_edit(self):
        Shift = self.Shift
        self.assertTrue(Shift.with_user(self.planner).get_planning_data("2026-10-01", "2026-10-31")["can_edit"])
        self.assertFalse(Shift.with_user(self.viewer).get_planning_data("2026-10-01", "2026-10-31")["can_edit"])

    def test_save_planning_changes(self):
        replaced = self._create()  # 10-15: morning -> night
        removed = self._create(day=date(2026, 10, 16))  # 10-16: removed
        custom = self._create(day=date(2026, 10, 17))  # 10-17: custom -> template time again
        custom.end_datetime = datetime(2026, 10, 17, 9, 0)
        self.assertTrue(custom.is_custom)

        self.Shift.with_user(self.planner).save_planning_changes([
            {"employee_id": self.employee.id, "date": "2026-10-15", "template_id": self.night.id},
            {"employee_id": self.employee.id, "date": "2026-10-16", "template_id": False},
            {"employee_id": self.employee.id, "date": "2026-10-17", "template_id": self.morning.id},
            {"employee_id": self.employee.id, "date": "2026-10-18", "template_id": self.morning.id},
            {"employee_id": self.employee.id, "date": "2026-10-19", "template_id": False},  # nothing there
        ])

        self.assertEqual(replaced.template_id, self.night)
        self.assertEqual(replaced.start_datetime, datetime(2026, 10, 15, 19, 0))
        self.assertFalse(removed.exists())
        self.assertEqual(custom.end_datetime, datetime(2026, 10, 17, 11, 0))
        self.assertFalse(custom.is_custom)
        created = self.Shift.search([("employee_id", "=", self.employee.id), ("date", "=", "2026-10-18")])
        self.assertEqual(created.template_id, self.morning)

    def test_save_planning_changes_custom_time(self):
        existing = self._create()  # 10-15 morning -> morning 06:00-12:00
        self.Shift.with_user(self.planner).save_planning_changes([
            {
                "employee_id": self.employee.id, "date": "2026-10-15", "template_id": self.morning.id,
                "custom": {"hour_from": 6.0, "hour_to": 12.0, "break_minutes": 15},
            },
            {
                "employee_id": self.employee.id, "date": "2026-10-16", "template_id": False,
                "custom": {"hour_from": 20.0, "hour_to": 2.5, "break_minutes": 0},
            },
        ])
        self.assertEqual(existing.template_id, self.morning)
        self.assertEqual(existing.start_datetime, datetime(2026, 10, 15, 3, 0))
        self.assertEqual(existing.end_datetime, datetime(2026, 10, 15, 9, 0))
        self.assertEqual(existing.break_minutes, 15)
        self.assertAlmostEqual(existing.duration, 5.75)
        self.assertTrue(existing.is_custom)

        created = self.Shift.search([("employee_id", "=", self.employee.id), ("date", "=", "2026-10-16")])
        self.assertFalse(created.template_id)
        self.assertEqual(created.start_datetime, datetime(2026, 10, 16, 17, 0))
        self.assertEqual(created.end_datetime, datetime(2026, 10, 16, 23, 30))
        self.assertAlmostEqual(created.duration, 6.5)
        self.assertFalse(created.is_custom)

    def test_save_planning_changes_viewer_denied(self):
        with self.assertRaises(AccessError):
            self.Shift.with_user(self.viewer).save_planning_changes([
                {"employee_id": self.employee.id, "date": "2026-10-15", "template_id": self.morning.id},
            ])
