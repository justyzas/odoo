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
