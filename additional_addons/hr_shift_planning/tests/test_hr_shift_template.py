from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestHrShiftTemplate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["hr.shift.template"]
        cls.planner = new_test_user(
            cls.env, login="shift_planner", groups="hr_shift_planning.group_shift_planner"
        )
        cls.viewer = new_test_user(
            cls.env, login="shift_viewer", groups="hr_shift_planning.group_shift_viewer"
        )
        cls.user = new_test_user(cls.env, login="shift_plain_user", groups="base.group_user")

    def _create(self, **vals):
        values = {"name": "Morning", "code": "M", "hour_from": 6.0, "hour_to": 14.0}
        values.update(vals)
        return self.Template.create(values)

    def test_duration_day_shift(self):
        template = self._create(break_minutes=30)
        self.assertAlmostEqual(template.duration, 7.5)
        self.assertFalse(template.is_overnight)

    def test_duration_overnight_shift(self):
        template = self._create(code="N", hour_from=22.0, hour_to=6.0, break_minutes=30)
        self.assertAlmostEqual(template.duration, 7.5)
        self.assertTrue(template.is_overnight)

    def test_duration_recomputed_on_change(self):
        template = self._create()
        template.hour_to = 12.0
        self.assertAlmostEqual(template.duration, 6.0)

    def test_code_unique_per_company(self):
        self._create()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._create(name="Another")

    def test_code_same_in_other_company(self):
        other_company = self.env["res.company"].create({"name": "Other Company"})
        self._create()
        self._create(company_id=other_company.id)

    def test_invalid_hours(self):
        with self.assertRaises(ValidationError):
            self._create(hour_from=25.0)

    def test_break_longer_than_shift(self):
        with self.assertRaises(ValidationError):
            self._create(hour_from=6.0, hour_to=7.0, break_minutes=60)

    def test_planner_can_manage(self):
        template = self._create().with_user(self.planner)
        template.write({"name": "Morning shift"})
        self.Template.with_user(self.planner).create(
            {"name": "Evening", "code": "E", "hour_from": 14.0, "hour_to": 22.0}
        )
        template.unlink()

    def test_viewer_read_only(self):
        template = self._create()
        self.assertEqual(template.with_user(self.viewer).name, "Morning")
        with self.assertRaises(AccessError):
            template.with_user(self.viewer).write({"name": "Changed"})
        with self.assertRaises(AccessError):
            self.Template.with_user(self.viewer).create(
                {"name": "Evening", "code": "E", "hour_from": 14.0, "hour_to": 22.0}
            )

    def test_plain_user_no_access(self):
        template = self._create()
        with self.assertRaises(AccessError):
            template.with_user(self.user).read(["name"])
