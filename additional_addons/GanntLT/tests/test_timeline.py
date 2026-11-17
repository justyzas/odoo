import io
import json
import re
import secrets
import zipfile
from datetime import datetime, timedelta, date, time
from unittest.mock import patch
import xlsxwriter

from odoo import fields, http
from odoo.tests.common import TransactionCase, HttpCase
from odoo.exceptions import ValidationError, UserError, AccessError
from odoo.addons.GanntLT.models.project_task import (
    calculate_lithuanian_working_hours,
    get_lithuanian_holidays,
    ceil_minutes_between,
)
from odoo.addons.GanntLT.controllers.main import (
    partition_tasks_by_project,
    render_project_sheet,
    create_workbook_formats,
    sanitize_excel_str,
    sanitize_sheet_name,
    get_task_depth,
    count_working_days,
)


class TestGanntLTTimeline(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref("base.group_user")
        group_project_user = cls.env.ref("project.group_project_user")
        group_worker = cls.env.ref("GanntLT.group_gannt_lt_worker")
        project_vals = {
            "name": "Test GanntLT Unit Project",
            "privacy_visibility": "employees",
        }
        if "billing_type" in cls.env["project.project"]._fields:
            project_vals["billing_type"] = "not_billable"
        cls.project = cls.env["project.project"].create(project_vals)
        cls.user_a = cls.env["res.users"].create({
            "name": "Worker A",
            "login": "worker_a_test",
            "email": "worker_a@example.com",
            "group_ids": [(6, 0, [group_user.id, group_project_user.id, group_worker.id])],
        })
        cls.user_b = cls.env["res.users"].create({
            "name": "Worker B",
            "login": "worker_b_test",
            "email": "worker_b@example.com",
            "group_ids": [(6, 0, [group_user.id, group_project_user.id, group_worker.id])],
        })

    def test_01_working_hours_zero_duration(self):
        """Zero duration task must return 0.0 working hours."""
        dt = datetime(2026, 10, 16, 0, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(dt, dt), 0.0)

        dt_work = datetime(2026, 10, 16, 8, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(dt_work, dt_work), 0.0)

    def test_02_working_hours_multi_year_holidays(self):
        """Multi-year range must include holidays in all intermediate years."""
        # Check that get_lithuanian_holidays returns 2025 holidays
        h2025 = get_lithuanian_holidays(2025)
        self.assertIn(date(2025, 2, 16), h2025)
        self.assertIn(date(2025, 12, 25), h2025)

        # Single holiday date (Feb 16, 2025 - Sunday, but test weekday holiday like Jan 1, 2025 Wednesday)
        jan1_2025_start = datetime(2025, 1, 1, 8, 0, 0)
        jan1_2025_end = datetime(2025, 1, 1, 17, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(jan1_2025_start, jan1_2025_end), 0.0)

        # Spanning from Dec 30, 2024 to Jan 3, 2026 includes 2025 holidays
        s = datetime(2024, 12, 30, 8, 0, 0)
        e = datetime(2026, 1, 5, 17, 0, 0)
        hours = calculate_lithuanian_working_hours(s, e)
        self.assertGreater(hours, 0.0)

    def test_03_batch_write_allocated_hours_isolation(self):
        """Batch write on multiple tasks must not bleed task 1's duration to all tasks."""
        t1 = self.env["project.task"].create({
            "name": "Task 1",
            "project_id": self.project.id,
            "planned_date_start": datetime(2026, 10, 19, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 19, 17, 0, 0),
            "allocated_hours": 8.0,
        })
        t2 = self.env["project.task"].create({
            "name": "Task 2",
            "project_id": self.project.id,
            "planned_date_start": datetime(2026, 10, 19, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 23, 17, 0, 0),
            "allocated_hours": 40.0,
        })

        # Batch write changing state or an unrelated field
        tasks = t1 | t2
        tasks.write({"description": "Updated batch"})
        self.assertEqual(t1.allocated_hours, 8.0)
        self.assertEqual(t2.allocated_hours, 40.0)

    def test_04_save_gannt_lt_task_dates_alias(self):
        """save_gannt_lt_task_dates RPC alias must successfully update task schedule."""
        task = self.env["project.task"].create({
            "name": "Task for RPC Alias",
            "project_id": self.project.id,
        })
        new_start = "2026-10-20 08:00:00"
        new_end = "2026-10-20 17:00:00"
        res = self.env["project.task"].save_gannt_lt_task_dates(
            task.id, start_date=new_start, end_date=new_end, progress=0.5
        )
        self.assertTrue(res)
        task.invalidate_recordset()
        self.assertTrue(task.planned_date_start)
        self.assertTrue(task.planned_date_end)

    def test_05_hierarchy_propagation_and_reopen(self):
        """Early finish cascade to parent, reopen, and conflict resolution."""
        p_start = datetime(2026, 10, 20, 8, 0, 0)
        p_end = datetime(2026, 10, 20, 17, 0, 0)

        parent = self.env["project.task"].create({
            "name": "Parent Container",
            "project_id": self.project.id,
            "planned_date_start": p_start,
            "planned_date_end": p_end,
            "plan_is_auto": False,
        })
        child1 = self.env["project.task"].create({
            "name": "Child 1",
            "project_id": self.project.id,
            "parent_id": parent.id,
            "planned_date_start": p_start,
            "planned_date_end": p_end,
            "plan_is_auto": False,
        })
        child2 = self.env["project.task"].create({
            "name": "Child 2",
            "project_id": self.project.id,
            "parent_id": parent.id,
            "planned_date_start": p_start,
            "planned_date_end": p_end,
            "plan_is_auto": False,
        })

        # Finish child1 early (14:00)
        early_14 = datetime(2026, 10, 20, 14, 0, 0)
        child1.action_timer_finish(force_now=early_14)
        self.assertEqual(child1.state, "03_approved")
        self.assertEqual(child1.actual_finish_date, early_14)

        # Parent remains in_progress because child2 is still running
        parent.invalidate_recordset()
        self.assertEqual(parent.state, "01_in_progress")

        # Finish child2 early (15:00)
        early_15 = datetime(2026, 10, 20, 15, 0, 0)
        child2.action_timer_finish(force_now=early_15)

        # Both children finished early -> parent gets auto-promoted to approved with actual_finish_date = 15:00
        parent.invalidate_recordset()
        self.assertEqual(parent.state, "03_approved")
        self.assertEqual(parent.actual_finish_date, early_15)

        # Reopen child2 via action_timer_reopen
        child2.action_timer_reopen()
        self.assertEqual(child2.state, "01_in_progress")
        self.assertFalse(child2.actual_finish_date)

        # Parent must be reopened back to in_progress and clear finish dates
        parent.invalidate_recordset()
        self.assertEqual(parent.state, "01_in_progress")
        self.assertFalse(parent.actual_finish_date)

    def test_06_post_approval_immutability(self):
        """Approved task schedule cannot be shrunk or moved on timeline, but can be extended."""
        s = datetime(2026, 10, 26, 8, 0, 0)
        e = datetime(2026, 10, 26, 17, 0, 0)
        task = self.env["project.task"].create({
            "name": "Approved Anti-Cheat Task",
            "project_id": self.project.id,
            "planned_date_start": s,
            "planned_date_end": e,
            "allocated_hours": 8.0,
        })
        # Approve task
        task.write({"state": "03_approved"})
        self.assertTrue(task.has_been_approved)
        self.assertEqual(task.approved_baseline_duration, 8.0)
        self.assertEqual(task.approved_planned_date_start, s)
        self.assertEqual(task.approved_planned_date_end, e)

        # 1. Attempt to move planned_date_start -> must raise ValidationError
        next_day_s = datetime(2026, 10, 27, 8, 0, 0)
        with self.assertRaises(ValidationError):
            task.write({"planned_date_start": next_day_s})

        # 2. Attempt to shrink planned_date_end earlier (e.g. 12:00 = 4h) -> must raise ValidationError
        earlier_e = datetime(2026, 10, 26, 12, 0, 0)
        with self.assertRaises(ValidationError):
            task.write({"planned_date_end": earlier_e})

        # 3. Attempt to shrink allocated_hours below baseline -> must raise ValidationError
        with self.assertRaises(ValidationError):
            task.write({"allocated_hours": 4.0})

        # 4. Extending task planned_date_end is permitted (e.g. when late or authorized extension)
        extended_e = datetime(2026, 10, 27, 17, 0, 0)
        task.write({"planned_date_end": extended_e})
        self.assertEqual(task.planned_date_end, extended_e)

    def test_07_exclusive_worker_lock(self):
        """Only the active worker or a manager can manage the timer on a running task."""
        task = self.env["project.task"].create({
            "name": "Exclusive Worker Task",
            "project_id": self.project.id,
            "user_ids": [(6, 0, [self.user_a.id, self.user_b.id])],
        })

        # Worker A starts task
        task.with_user(self.user_a).action_timer_start()
        self.assertTrue(task.is_timer_running)
        self.assertEqual(task.active_user_id, self.user_a)
        self.assertFalse(task.is_paused)

        # Worker B attempts to start -> blocked
        with self.assertRaises(UserError):
            task.with_user(self.user_b).action_timer_start()

        # Worker B attempts to pause -> blocked
        with self.assertRaises(UserError):
            task.with_user(self.user_b).action_timer_pause()

        # Worker B attempts to finish -> blocked
        with self.assertRaises(UserError):
            task.with_user(self.user_b).action_timer_finish()

        # Worker B attempts to edit schedule -> blocked
        with self.assertRaises(UserError):
            task.with_user(self.user_b).write({"planned_date_start": datetime(2026, 10, 28, 8, 0, 0)})

        # Worker A finishes task
        task.with_user(self.user_a).action_timer_finish()
        self.assertFalse(task.is_timer_running)
        self.assertFalse(task.active_user_id)
        self.assertEqual(task.state, "03_approved")

    def test_08_pause_wizard_state_and_reason(self):
        """Pausing task logs reason, sets is_paused=True, and locks continuation to active worker."""
        task = self.env["project.task"].create({
            "name": "Pausable Task",
            "project_id": self.project.id,
            "user_ids": [(6, 0, [self.user_a.id, self.user_b.id])],
        })

        # Worker A starts timer
        task.with_user(self.user_a).action_timer_start()

        # Worker A pauses via wizard
        wizard = self.env["project.task.pause.wizard"].with_user(self.user_a).create({
            "task_id": task.id,
            "timer_start": task.timer_start,
            "reason": "Pietų pertrauka (Lunch break)",
        })
        wizard.action_confirm_pause()

        task.invalidate_recordset()
        self.assertFalse(task.is_timer_running)
        self.assertTrue(task.is_paused)
        self.assertEqual(task.last_pause_reason, "Pietų pertrauka (Lunch break)")
        self.assertEqual(task.active_user_id, self.user_a)

        # Worker B attempts to start/resume -> blocked
        with self.assertRaises(UserError):
            task.with_user(self.user_b).action_timer_start()

        # Worker A resumes
        task.with_user(self.user_a).action_timer_start()
        self.assertTrue(task.is_timer_running)
        self.assertFalse(task.is_paused)
        self.assertEqual(task.active_user_id, self.user_a)

    def test_09_excel_export_sheet_and_filtering(self):
        """Excel generation builds outline levels, statuses, dates, and strict project filtering."""
        proj_2_vals = {
            "name": "Another Unrelated Project",
            "privacy_visibility": "employees",
        }
        if "billing_type" in self.env["project.project"]._fields:
            proj_2_vals["billing_type"] = "not_billable"
        proj_2 = self.env["project.project"].create(proj_2_vals)
        t_unrelated = self.env["project.task"].create({
            "name": "Unrelated Task in Project 2",
            "project_id": proj_2.id,
            "planned_date_start": datetime(2026, 10, 1, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 5, 17, 0, 0),
        })

        milestone = self.env["project.milestone"].create({
            "name": "Phase 1: Foundation",
            "project_id": self.project.id,
            "deadline": date(2026, 10, 15),
        })

        t_parent = self.env["project.task"].create({
            "name": "Site Preparation",
            "project_id": self.project.id,
            "milestone_id": milestone.id,
            "planned_date_start": datetime(2026, 10, 1, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 10, 17, 0, 0),
            "user_ids": [(6, 0, [self.user_a.id])],
        })

        t_sub = self.env["project.task"].create({
            "name": "Excavation Work",
            "project_id": self.project.id,
            "parent_id": t_parent.id,
            "planned_date_start": datetime(2026, 10, 2, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 6, 17, 0, 0),
            "date_deadline": datetime(2026, 10, 8, 17, 0, 0),
            "user_ids": [(6, 0, [self.user_b.id])],
        })

        t_sub_sub = self.env["project.task"].create({
            "name": "Soil Testing",
            "project_id": self.project.id,
            "parent_id": t_sub.id,
            "planned_date_start": datetime(2026, 10, 2, 8, 0, 0),
            "planned_date_end": datetime(2026, 10, 4, 17, 0, 0),
            "actual_finish_date": datetime(2026, 10, 3, 17, 0, 0),
            "state": "1_done",
        })

        # Fetch timeline data filtered by self.project
        data = self.env["project.task"].get_all_in_one_timeline_data(project_id=self.project.id)
        tasks = data.get("tasks", [])
        self.assertTrue(tasks)

        # Strict filtering check
        task_names = [t.get("text") for t in tasks]
        self.assertIn("Site Preparation", task_names)
        self.assertIn("Excavation Work", task_names)
        self.assertIn("Soil Testing", task_names)
        self.assertNotIn("Unrelated Task in Project 2", task_names)

        # Hierarchy depth check
        task_by_id = {t["id"]: t for t in tasks}
        p_row = next(t for t in tasks if t["text"] == "Site Preparation")
        c_row = next(t for t in tasks if t["text"] == "Excavation Work")
        cc_row = next(t for t in tasks if t["text"] == "Soil Testing")

        self.assertGreaterEqual(get_task_depth(p_row, task_by_id), 1)
        self.assertGreater(get_task_depth(c_row, task_by_id), get_task_depth(p_row, task_by_id))
        self.assertGreater(get_task_depth(cc_row, task_by_id), get_task_depth(c_row, task_by_id))

        # Test sheet name sanitization (stripping illegal characters and leading/trailing single quotes)
        clean_name = sanitize_sheet_name(" 'Mano [Projektas]: 2026' ", set())
        self.assertFalse(clean_name.startswith("'"))
        self.assertFalse(clean_name.endswith("'"))
        self.assertNotIn("[", clean_name)
        self.assertNotIn(":", clean_name)

        # Test working days count across weekend and holiday
        h_cache = {2026: get_lithuanian_holidays(2026)}
        # Friday Oct 23 to Monday Oct 26: 2 working days (skips Saturday and Sunday)
        self.assertEqual(count_working_days(date(2026, 10, 23), date(2026, 10, 26), h_cache), 2)

        # Test partition helper
        partitions = partition_tasks_by_project(tasks)
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0][0], self.project.name)

        # Generate workbook in memory
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        formats = create_workbook_formats(wb)
        holidays_cache = {}
        render_project_sheet(wb, "Test_Project", self.project.name, tasks, formats, holidays_cache)
        wb.close()

        xlsx_bytes = output.getvalue()
        self.assertGreater(len(xlsx_bytes), 1000)

        with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as z:
            self.assertIn("xl/workbook.xml", z.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
            sheet_xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn('outlineLevel="', sheet_xml)
            self.assertIn('state="frozen"', sheet_xml)

    def test_11_lithuanian_working_hours_lunch_and_caps(self):
        """Comprehensive verification of lunch deduction (12:45-13:45) and 8.0h daily cap."""
        # Standard full day 08:00 - 17:00 (9 clock hours - 1h lunch = exactly 8.0h)
        d_std_s = datetime(2026, 10, 19, 8, 0, 0)
        d_std_e = datetime(2026, 10, 19, 17, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_std_s, d_std_e), 8.0)

        # Early finish at 16:30 (8.5 clock hours - 1h lunch = exactly 7.5h, NOT 8.5h!)
        d_early_e = datetime(2026, 10, 19, 16, 30, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_std_s, d_early_e), 7.5)

        # Morning only: 08:00 - 12:00 (4.0h, no lunch overlap)
        d_morn_e = datetime(2026, 10, 19, 12, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_std_s, d_morn_e), 4.0)

        # Afternoon only: 13:45 - 17:00 (3.25h, no lunch overlap)
        d_aft_s = datetime(2026, 10, 19, 13, 45, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_aft_s, d_std_e), 3.25)

        # Spanning lunch: 12:00 - 14:00 (2h total - 1h lunch = 1.0h)
        d_lunch_s = datetime(2026, 10, 19, 12, 0, 0)
        d_lunch_e = datetime(2026, 10, 19, 14, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_lunch_s, d_lunch_e), 1.0)

        # Entirely within lunch: 12:45 - 13:45 (0.0h)
        d_in_lunch_s = datetime(2026, 10, 19, 12, 45, 0)
        d_in_lunch_e = datetime(2026, 10, 19, 13, 45, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_in_lunch_s, d_in_lunch_e), 0.0)

        # Partial lunch overlap: 08:00 - 13:00 (5h gross - 15m lunch [12:45-13:00] = 4.75h)
        d_part_lunch = datetime(2026, 10, 19, 13, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_std_s, d_part_lunch), 4.75)

        # Daily cap: 07:00 - 19:00 (capped at 8.0h per day)
        d_overtime_s = datetime(2026, 10, 19, 7, 0, 0)
        d_overtime_e = datetime(2026, 10, 19, 19, 0, 0)
        self.assertEqual(calculate_lithuanian_working_hours(d_overtime_s, d_overtime_e), 8.0)

        # Multi-day with boundary lunch deductions:
        # Oct 19 (Mon) 09:00 - 17:00 -> 7.0h (8h gross - 1h lunch)
        # Oct 20 (Tue) 08:00 - 17:00 -> 8.0h (full day)
        # Oct 21 (Wed) 08:00 - 16:30 -> 7.5h (8.5h gross - 1h lunch)
        # Total = 7.0 + 8.0 + 7.5 = 22.5h
        m_start = datetime(2026, 10, 19, 9, 0, 0)
        m_end = datetime(2026, 10, 21, 16, 30, 0)
        self.assertEqual(calculate_lithuanian_working_hours(m_start, m_end), 22.5)

        # String datetime and date inputs support
        self.assertEqual(calculate_lithuanian_working_hours("2026-10-19 08:00:00", "2026-10-19 17:00:00"), 8.0)
        self.assertEqual(calculate_lithuanian_working_hours("2026-10-19", "2026-10-19"), 8.0)
        self.assertEqual(calculate_lithuanian_working_hours("2026-10-19", "2026-10-20"), 16.0)

    def test_12_reopen_clears_red_delay_tail(self):
        """When a late task is reopened or moved to in_progress, auto-deadlines and red tails are cleared."""
        s = datetime(2026, 10, 20, 8, 0, 0)
        e = datetime(2026, 10, 20, 12, 0, 0)
        task = self.env["project.task"].create({
            "name": "Late Finishing Task",
            "project_id": self.project.id,
            "planned_date_start": s,
            "planned_date_end": e,
            "plan_is_auto": False,
        })

        # 1. Finish late at 13:30 (triggers auto deadline and red delay tail)
        finish_late = datetime(2026, 10, 20, 13, 30, 0)
        stop_vals, info = task._work_stop_vals(finish_late, 5.5 * 3600, finished=True)
        self.assertTrue(stop_vals.get("deadline_is_auto"))
        self.assertEqual(stop_vals.get("date_deadline"), finish_late)
        stop_vals["state"] = "03_approved"
        task.write(stop_vals)

        # Verify timeline data shows red delay tail
        timeline = self.env["project.task"].get_all_in_one_timeline_data(domain=[("id", "=", task.id)])
        t_item = next(item for item in timeline["tasks"] if item["id"] == f"task_{task.id}")
        self.assertTrue(t_item["has_deadline_delay"])
        self.assertEqual(t_item["delay_minutes"], 90)

        # 2. Reopen via action_timer_reopen -> must clear auto deadline and red delay tail
        task.action_timer_reopen()
        self.assertEqual(task.state, "01_in_progress")
        self.assertFalse(task.date_deadline)
        self.assertFalse(task.deadline_is_auto)

        timeline_reopened = self.env["project.task"].get_all_in_one_timeline_data(domain=[("id", "=", task.id)])
        t_reopened_item = next(item for item in timeline_reopened["tasks"] if item["id"] == f"task_{task.id}")
        self.assertFalse(t_reopened_item["has_deadline_delay"])
        self.assertEqual(t_reopened_item["delay_minutes"], 0)

        # 3. Finish late again and step back to 01_in_progress via write()
        stop_vals2, _ = task._work_stop_vals(finish_late, 5.5 * 3600, finished=True)
        stop_vals2["state"] = "03_approved"
        task.write(stop_vals2)
        self.assertTrue(task.deadline_is_auto)

        task.write({"state": "01_in_progress"})
        self.assertFalse(task.date_deadline)
        self.assertFalse(task.deadline_is_auto)

        # 4. Finish late again and step back to 02_changes_requested via write()
        stop_vals3, _ = task._work_stop_vals(finish_late, 5.5 * 3600, finished=True)
        stop_vals3["state"] = "03_approved"
        task.write(stop_vals3)
        self.assertTrue(task.deadline_is_auto)

        task.write({"state": "02_changes_requested"})
        self.assertFalse(task.date_deadline)
        self.assertFalse(task.deadline_is_auto)

        timeline_cr = self.env["project.task"].get_all_in_one_timeline_data(domain=[("id", "=", task.id)])
        t_cr_item = next(item for item in timeline_cr["tasks"] if item["id"] == f"task_{task.id}")
        self.assertFalse(t_cr_item["has_deadline_delay"])

        # 5. Finish late again and step back to 04_waiting_normal via write()
        stop_vals4, _ = task._work_stop_vals(finish_late, 5.5 * 3600, finished=True)
        stop_vals4["state"] = "03_approved"
        task.write(stop_vals4)
        self.assertTrue(task.deadline_is_auto)

        task.write({"state": "04_waiting_normal"})
        self.assertFalse(task.date_deadline)
        self.assertFalse(task.deadline_is_auto)

        timeline_wn = self.env["project.task"].get_all_in_one_timeline_data(domain=[("id", "=", task.id)])
        t_wn_item = next(item for item in timeline_wn["tasks"] if item["id"] == f"task_{task.id}")
        self.assertFalse(t_wn_item["has_deadline_delay"])

    def test_13_baseline_duration_holiday_fallback(self):
        """Holiday / weekend fallback caps duration to standard 8.0h and permits extensions."""
        # Nov 2, 2026 is All Souls' Day in Lithuania (public holiday)
        h_s = datetime(2026, 11, 2, 8, 0, 0)
        h_e = datetime(2026, 11, 2, 17, 0, 0)

        task_holiday = self.env["project.task"].create({
            "name": "Holiday Task Without Allocated Hours",
            "project_id": self.project.id,
            "planned_date_start": h_s,
            "planned_date_end": h_e,
        })
        task_holiday.write({"state": "03_approved"})
        # Must be capped to 8.0h (NOT raw 9.0h clock difference!)
        self.assertEqual(task_holiday.approved_baseline_duration, 8.0)

        # Extending to next day (Nov 3 is working day: +8h = 16h total) must succeed
        next_day_e = datetime(2026, 11, 3, 17, 0, 0)
        task_holiday.write({"planned_date_end": next_day_e})
        self.assertEqual(task_holiday.planned_date_end, next_day_e)

        # Holiday task with explicit allocated_hours
        task_holiday_alloc = self.env["project.task"].create({
            "name": "Holiday Task With Allocated Hours",
            "project_id": self.project.id,
            "planned_date_start": h_s,
            "planned_date_end": h_e,
            "allocated_hours": 6.0,
        })
        task_holiday_alloc.write({"state": "03_approved"})
        self.assertEqual(task_holiday_alloc.approved_baseline_duration, 6.0)

        # Attempting to shorten an approved holiday task (from 17:00 to 12:00) must raise ValidationError
        short_e = datetime(2026, 11, 2, 12, 0, 0)
        with self.assertRaises(ValidationError):
            task_holiday.write({"planned_date_end": short_e})

        # Legacy task with approved_baseline_duration = 0.0 on holiday must ALSO be protected
        task_holiday_legacy = self.env["project.task"].create({
            "name": "Holiday Task Legacy Approved",
            "project_id": self.project.id,
            "planned_date_start": h_s,
            "planned_date_end": h_e,
        })
        task_holiday_legacy.write({"state": "03_approved"})
        task_holiday_legacy.write({"approved_baseline_duration": 0.0})
        with self.assertRaises(ValidationError):
            task_holiday_legacy.write({"planned_date_end": short_e})

    def test_14_batch_schedule_sudo_permissions(self):
        """Users with GanntLT Can Edit rights can save project and milestone dates in batch schedule."""
        group_gannt_edit = self.env.ref("GanntLT.group_gannt_lt_manager")
        self.user_a.write({"group_ids": [(4, group_gannt_edit.id)]})

        milestone = self.env["project.milestone"].create({
            "name": "Test Milestone for Batch Sudo",
            "project_id": self.project.id,
            "deadline": date(2026, 10, 15),
        })

        updates = [
            {
                "id": f"proj_{self.project.id}",
                "start_date": "2026-10-01",
                "end_date": "2026-10-31",
            },
            {
                "id": f"milestone_{milestone.id}",
                "start_date": "2026-10-01",
                "end_date": "2026-10-20",
                "progress": 1.0,
            },
        ]

        # Call save_timeline_batch_schedule as user_a (who is not project manager)
        self.env["project.task"].with_user(self.user_a).save_timeline_batch_schedule(updates)

        self.project.invalidate_recordset()
        milestone.invalidate_recordset()
        self.assertEqual(self.project.date_start, date(2026, 10, 1))
        self.assertEqual(self.project.date, date(2026, 10, 31))
        self.assertEqual(milestone.deadline, date(2026, 10, 20))
        self.assertTrue(milestone.is_reached)

        # Call save_timeline_batch_schedule as user_b (who has only Can View rights) -> must raise AccessError
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(self.user_b).save_timeline_batch_schedule(updates)

    def test_15_large_scale_hierarchy_and_status_stress_test(self):
        """Stress test with multi-level deep hierarchy, 50+ tasks, resizing, and status transitions."""
        p_vals = {"name": "Large Scale Stress Project", "privacy_visibility": "employees"}
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        large_proj = self.env["project.project"].create(p_vals)

        # Create 5 Parent tasks
        parents = []
        base_s = datetime(2026, 11, 2, 8, 0, 0)
        for i in range(5):
            p = self.env["project.task"].create({
                "name": f"Parent Task {i+1}",
                "project_id": large_proj.id,
                "planned_date_start": base_s + timedelta(days=i * 5),
                "planned_date_end": base_s + timedelta(days=i * 5 + 4, hours=9),
                "plan_is_auto": False,
            })
            parents.append(p)

        # Under each parent, create 3 child tasks (15 children)
        children = []
        for p in parents:
            for j in range(3):
                c = self.env["project.task"].create({
                    "name": f"{p.name} - Child {j+1}",
                    "project_id": large_proj.id,
                    "parent_id": p.id,
                    "planned_date_start": p.planned_date_start + timedelta(days=j),
                    "planned_date_end": p.planned_date_start + timedelta(days=j, hours=8),
                    "plan_is_auto": False,
                })
                children.append(c)

        # Under each child, create 2 grandchild tasks (30 grandchildren, total 50 tasks)
        grandchildren = []
        for c in children:
            for k in range(2):
                gc = self.env["project.task"].create({
                    "name": f"{c.name} - GC {k+1}",
                    "project_id": large_proj.id,
                    "parent_id": c.id,
                    "planned_date_start": c.planned_date_start + timedelta(hours=k * 4),
                    "planned_date_end": c.planned_date_start + timedelta(hours=k * 4 + 4),
                    "plan_is_auto": False,
                })
                grandchildren.append(gc)

        total_created = len(parents) + len(children) + len(grandchildren)
        self.assertEqual(total_created, 50)

        # 1. Batch resize: simulate dragging and resizing multiple tasks in one batch update
        batch_updates = []
        for idx, gc in enumerate(grandchildren[:15]):
            new_s = (base_s + timedelta(days=idx, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            new_e = (base_s + timedelta(days=idx, hours=7)).strftime("%Y-%m-%d %H:%M:%S")
            batch_updates.append({
                "id": f"task_{gc.id}",
                "start_date": new_s,
                "end_date": new_e,
                "progress": 0.5,
            })
        self.env["project.task"].save_timeline_batch_schedule(batch_updates)

        # 2. Dynamic status transitions across hierarchy:
        # Move grandchildren through statuses:
        # - 5 approved
        # - 5 in_progress
        # - 5 waiting
        # - 5 changes_requested
        # - 5 canceled
        # - 5 done
        for gc in grandchildren[0:5]:
            gc.write({"state": "03_approved"})
        for gc in grandchildren[5:10]:
            gc.write({"state": "01_in_progress"})
        for gc in grandchildren[10:15]:
            gc.write({"state": "04_waiting_normal"})
        for gc in grandchildren[15:20]:
            gc.write({"state": "02_changes_requested"})
        for gc in grandchildren[20:25]:
            gc.write({"state": "1_canceled"})
        for gc in grandchildren[25:30]:
            gc.write({"state": "1_done"})

        # Recompute all containers up the hierarchy
        for c in children:
            c._recompute_container()
        for p in parents:
            p._recompute_container()

        # 3. Verify get_all_in_one_timeline_data serialization under scale
        timeline_data = self.env["project.task"].get_all_in_one_timeline_data(project_id=large_proj.id)
        tasks_res = timeline_data.get("tasks", [])
        task_items = [t for t in tasks_res if str(t.get("id")).startswith("task_")]
        proj_items = [t for t in tasks_res if str(t.get("id")).startswith("proj_")]
        self.assertEqual(len(task_items), 50)
        self.assertEqual(len(proj_items), 1)
        self.assertEqual(len(tasks_res), 51)

        # Validate structure of each serialized task item
        for t in task_items:
            self.assertIn("id", t)
            self.assertIn("text", t)
            self.assertIn("start_date", t)
            self.assertIn("end_date", t)
            self.assertIn("progress", t)
            self.assertIn("color", t)
            self.assertIn("state", t)

        for p in proj_items:
            self.assertIn("id", p)
            self.assertIn("text", p)
            self.assertIn("start_date", p)
            self.assertIn("end_date", p)
            self.assertIn("is_project", p)

        # Cleanup
        large_proj.unlink()

    def test_16_billing_type_fallback_and_default_handling(self):
        """Creating a project without billing_type defaults to not_billable without NotNullViolation."""
        # 1. Test create() without passing billing_type
        proj_no_bt = self.env["project.project"].create({"name": "Project Without Billing Type Explicitly"})
        if "billing_type" in self.env["project.project"]._fields:
            self.assertEqual(proj_no_bt.billing_type, "not_billable")

        # 2. Test default_get()
        defs = self.env["project.project"].default_get(["name", "billing_type"])
        if "billing_type" in self.env["project.project"]._fields:
            self.assertEqual(defs.get("billing_type"), "not_billable")

        proj_no_bt.unlink()

    def test_17_overdue_past_task_and_project_flags(self):
        """Tasks and projects past-due and incomplete correctly get is_overdue_past=True, and only_overdue filters them."""
        now = datetime.now()
        p_vals = {"name": "Overdue Test Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        proj = self.env["project.project"].create(p_vals)

        # Task 1: in the past, incomplete
        t_past = self.env["project.task"].create({
            "name": "Overdue Incomplete Task",
            "project_id": proj.id,
            "planned_date_start": now - timedelta(days=5),
            "planned_date_end": now - timedelta(days=2),
            "state": "01_in_progress",
            "progress": 0.3,
        })

        # Task 2: in the past, but completed
        t_done = self.env["project.task"].create({
            "name": "Past Completed Task",
            "project_id": proj.id,
            "planned_date_start": now - timedelta(days=6),
            "planned_date_end": now - timedelta(days=3),
            "state": "1_done",
            "progress": 1.0,
        })

        # Task 3: in the future, incomplete
        t_future = self.env["project.task"].create({
            "name": "Future On-Track Task",
            "project_id": proj.id,
            "planned_date_start": now + timedelta(days=2),
            "planned_date_end": now + timedelta(days=5),
            "state": "01_in_progress",
            "progress": 0.0,
        })

        # Future-only project
        p_future_vals = {"name": "Future Only Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_future_vals["billing_type"] = "not_billable"
        proj_future = self.env["project.project"].create(p_future_vals)
        t_future_only = self.env["project.task"].create({
            "name": "Future Task Only",
            "project_id": proj_future.id,
            "planned_date_start": now + timedelta(days=10),
            "planned_date_end": now + timedelta(days=15),
            "state": "01_in_progress",
            "progress": 0.0,
        })

        # Project 3: in the past, incomplete (genuinely overdue project)
        p_past_vals = {"name": "Past Overdue Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_past_vals["billing_type"] = "not_billable"
        proj_past = self.env["project.project"].create(p_past_vals)
        t_past_only = self.env["project.task"].create({
            "name": "Past Incomplete Task",
            "project_id": proj_past.id,
            "planned_date_start": now - timedelta(days=15),
            "planned_date_end": now - timedelta(days=10),
            "state": "01_in_progress",
            "progress": 0.3,
        })

        # Query all timeline data for proj (ends in the future)
        data = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj.id)
        tasks_by_id = {t["id"]: t for t in data["tasks"]}

        # Verify task flags
        self.assertTrue(tasks_by_id[f"task_{t_past.id}"]["is_overdue_past"])
        self.assertFalse(tasks_by_id[f"task_{t_done.id}"]["is_overdue_past"])
        self.assertFalse(tasks_by_id[f"task_{t_future.id}"]["is_overdue_past"])

        # Option A verification: proj ends in the future, so its own row has is_overdue_past=False (no fire icon)
        self.assertFalse(tasks_by_id[f"proj_{proj.id}"]["is_overdue_past"])
        self.assertTrue(tasks_by_id[f"proj_{proj.id}"]["has_overdue_tasks"])

        # Query timeline data for proj_past (ends in the past)
        data_past = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj_past.id)
        tasks_by_id_past = {t["id"]: t for t in data_past["tasks"]}
        self.assertTrue(tasks_by_id_past[f"proj_{proj_past.id}"]["is_overdue_past"])

        # Test only_overdue=True filter: returns proj_past, but NOT proj (ends in future) or proj_future
        overdue_data = self.env["project.task"].get_all_in_one_timeline_data(only_overdue=True)
        overdue_ids = {t["id"] for t in overdue_data["tasks"]}

        self.assertIn(f"proj_{proj_past.id}", overdue_ids)
        self.assertNotIn(f"proj_{proj.id}", overdue_ids)
        self.assertNotIn(f"proj_{proj_future.id}", overdue_ids)

        # Cleanup
        proj.unlink()
        proj_future.unlink()
        proj_past.unlink()

    def test_18_approved_vs_done_parent_hierarchy_lifecycle(self):
        """
        Tests the full Approved (03_approved) vs Done (1_done) hierarchy & timer lifecycle:
        1. Worker presses 'Baigti' (action_timer_finish) on a leaf task -> sets state to '03_approved' (NOT '1_done').
        2. Parent task cannot be manually marked '1_done' while any child/descendant is '03_approved' or '01_in_progress'.
        3. When all children are '03_approved' or '1_done' (with at least one '03_approved'), parent is '03_approved' in both DB and Timeline.
        4. When reviewer sets '02_changes_requested' on a child, all parent levels revert to '01_in_progress'.
        5. Only when ALL non-canceled descendants are '1_done' does the parent cascade to '1_done' in both DB and Timeline.
        """
        from datetime import datetime
        p_vals = {"name": "Hierarchy Lifecycle Test Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        proj = self.env["project.project"].create(p_vals)

        parent = self.env["project.task"].create({
            "name": "1. Projektavimo etapas",
            "project_id": proj.id,
            "planned_date_start": datetime(2026, 5, 4, 5, 0, 0),
            "planned_date_end": datetime(2026, 5, 8, 14, 0, 0),
            "state": "01_in_progress",
        })
        sub_elec = self.env["project.task"].create({
            "name": "1.1 Elektros dalis",
            "project_id": proj.id,
            "parent_id": parent.id,
            "planned_date_start": datetime(2026, 5, 4, 5, 0, 0),
            "planned_date_end": datetime(2026, 5, 6, 14, 0, 0),
            "state": "1_done",
            "progress": 1.0,
        })
        sub_mech = self.env["project.task"].create({
            "name": "1.2 Konstrukcinė dalis",
            "project_id": proj.id,
            "parent_id": parent.id,
            "planned_date_start": datetime(2026, 5, 6, 5, 0, 0),
            "planned_date_end": datetime(2026, 5, 8, 14, 0, 0),
            "state": "01_in_progress",
        })
        leaf_3d = self.env["project.task"].create({
            "name": "1.2.1 3D karkaso modelis",
            "project_id": proj.id,
            "parent_id": sub_mech.id,
            "planned_date_start": datetime(2026, 5, 6, 5, 0, 0),
            "planned_date_end": datetime(2026, 5, 8, 14, 0, 0),
            "state": "01_in_progress",
        })

        # Step 1: Cannot finish parent timer or mark parent 03_approved / 1_done while leaf_3d is 01_in_progress
        with self.assertRaises(ValidationError):
            parent.write({"state": "1_done"})
        with self.assertRaises(ValidationError):
            parent.write({"state": "03_approved"})
        with self.assertRaises(ValidationError):
            parent.action_timer_finish()

        # Step 2: Worker starts timer and presses 'Baigti' (action_timer_finish) on leaf_3d
        leaf_3d.action_timer_start()
        self.assertTrue(leaf_3d.is_timer_running)
        leaf_3d.action_timer_finish()
        leaf_3d.invalidate_recordset()
        sub_mech.invalidate_recordset()
        parent.invalidate_recordset()

        # Verify 'Baigti' set state to '03_approved' (NOT '1_done') and cascaded to sub_mech and parent
        self.assertEqual(leaf_3d.state, "03_approved")
        self.assertEqual(sub_mech.state, "03_approved")
        self.assertEqual(parent.state, "03_approved")

        # Verify Timeline and DB states match (both '03_approved')
        data = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj.id)
        t_map = {t["id"]: t for t in data["tasks"]}
        self.assertEqual(t_map[f"task_{parent.id}"]["state"], "03_approved")
        self.assertEqual(t_map[f"task_{sub_mech.id}"]["state"], "03_approved")
        self.assertEqual(t_map[f"task_{leaf_3d.id}"]["state"], "03_approved")

        # Step 3: Attempting to mark parent or sub_mech as '1_done' while leaf_3d is '03_approved' MUST raise ValidationError
        with self.assertRaises(ValidationError):
            parent.write({"state": "1_done"})
        with self.assertRaises(ValidationError):
            sub_mech.write({"state": "1_done"})

        # Step 4: Reviewer requests changes ('02_changes_requested') on leaf_3d -> parents revert to '01_in_progress'
        leaf_3d.write({"state": "02_changes_requested"})
        sub_mech.invalidate_recordset()
        parent.invalidate_recordset()
        self.assertEqual(sub_mech.state, "01_in_progress")
        self.assertEqual(parent.state, "01_in_progress")

        # Step 5: Worker fixes and presses 'Baigti' again -> '03_approved', then reviewer approves to '1_done'
        leaf_3d.action_timer_start()
        leaf_3d.action_timer_finish()
        self.assertEqual(leaf_3d.state, "03_approved")
        self.assertEqual(parent.state, "03_approved")

        leaf_3d.write({"state": "1_done"})
        sub_mech.invalidate_recordset()
        parent.invalidate_recordset()
        self.assertEqual(leaf_3d.state, "1_done")
        self.assertEqual(sub_mech.state, "1_done")
        self.assertEqual(parent.state, "1_done")

        data_done = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj.id)
        t_map_done = {t["id"]: t for t in data_done["tasks"]}
        self.assertEqual(t_map_done[f"task_{parent.id}"]["state"], "1_done")
        self.assertEqual(t_map_done[f"task_{sub_mech.id}"]["state"], "1_done")
        self.assertEqual(t_map_done[f"task_{leaf_3d.id}"]["state"], "1_done")

        proj.unlink()

    def test_19_resizing_parent_milestone_and_dependency_batch_bugs(self):
        """
        Tests fixes for Bugs #1, #2, #3, #4:
        - Bug #1: Expanding a parent task with plan_is_auto=True via save_timeline_batch_schedule persists manual span.
        - Bug #2: Shrinking or moving a milestone with >=2 tasks in save_timeline_batch_schedule updates milestone dates without fake delay tail.
        - Bug #3: Resizing/moving a task whose predecessor is '03_approved' does not lock it into '04_waiting_normal'.
        - Bug #4: add_timeline_dependency locks unfinished target when source is unfinished, blocks cycles, and preserves completed target state.
        """
        from datetime import datetime, date
        from odoo.addons.GanntLT.models.project_task import local_to_utc_dt, utc_to_local_dt

        p_vals = {"name": "Batch & Resize Regression Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        proj = self.env["project.project"].create(p_vals)

        # 1. Bug #1: Parent task with plan_is_auto=True expanded wider than its subtasks
        parent = self.env["project.task"].create({
            "name": "Auto Parent Task",
            "project_id": proj.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 4, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 6, 17, 0, 0)),
            "plan_is_auto": True,
        })
        c1 = self.env["project.task"].create({
            "name": "Subtask 1",
            "project_id": proj.id,
            "parent_id": parent.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 4, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 5, 17, 0, 0)),
        })
        c2 = self.env["project.task"].create({
            "name": "Subtask 2",
            "project_id": proj.id,
            "parent_id": parent.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 5, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 6, 17, 0, 0)),
        })
        parent.write({"plan_is_auto": True})
        self.assertTrue(parent.plan_is_auto)

        self.env["project.task"].save_timeline_batch_schedule([{
            "id": f"task_{parent.id}",
            "start_date": "2026-05-04 08:00:00",
            "end_date": "2026-05-11 17:00:00",
        }])
        parent.invalidate_recordset()
        self.assertFalse(parent.plan_is_auto)
        self.assertEqual(
            utc_to_local_dt(self.env, parent.planned_date_end).strftime("%Y-%m-%d %H:%M:%S"),
            "2026-05-11 17:00:00",
        )

        # 2. Bug #2: Milestone with 2 tasks shrunk via batch schedule (milestone listed before tasks)
        ms = self.env["project.milestone"].create({
            "name": "Test Milestone",
            "project_id": proj.id,
            "date_start": date(2026, 5, 4),
            "planned_date_end": date(2026, 5, 15),
            "deadline": date(2026, 5, 15),
        })
        mt1 = self.env["project.task"].create({
            "name": "MS Task 1",
            "project_id": proj.id,
            "milestone_id": ms.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 4, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 8, 17, 0, 0)),
        })
        mt2 = self.env["project.task"].create({
            "name": "MS Task 2",
            "project_id": proj.id,
            "milestone_id": ms.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 11, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 15, 17, 0, 0)),
        })
        self.env["project.task"].save_timeline_batch_schedule([
            {
                "id": f"milestone_{ms.id}",
                "start_date": "2026-05-04 08:00:00",
                "end_date": "2026-05-12 17:00:00",
            },
            {
                "id": f"task_{mt1.id}",
                "start_date": "2026-05-04 08:00:00",
                "end_date": "2026-05-08 17:00:00",
            },
            {
                "id": f"task_{mt2.id}",
                "start_date": "2026-05-11 08:00:00",
                "end_date": "2026-05-12 17:00:00",
            },
        ])
        ms.invalidate_recordset()
        self.assertEqual(ms.date_start, date(2026, 5, 4))
        self.assertEqual(ms.planned_date_end, date(2026, 5, 12))
        self.assertEqual(ms.deadline, date(2026, 5, 12))

        # 3. Bug #3: Task with '03_approved' predecessor stays '01_in_progress' on batch schedule save
        pred_app = self.env["project.task"].create({
            "name": "Approved Pred",
            "project_id": proj.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 4, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 5, 17, 0, 0)),
            "state": "03_approved",
            "progress": 0.0,
        })
        succ_app = self.env["project.task"].create({
            "name": "Active Succ",
            "project_id": proj.id,
            "planned_date_start": local_to_utc_dt(self.env, datetime(2026, 5, 6, 8, 0, 0)),
            "planned_date_end": local_to_utc_dt(self.env, datetime(2026, 5, 7, 17, 0, 0)),
            "state": "01_in_progress",
            "depend_on_ids": [(4, pred_app.id)],
        })
        self.env["project.task"].save_timeline_batch_schedule([{
            "id": f"task_{succ_app.id}",
            "start_date": "2026-05-06 08:00:00",
            "end_date": "2026-05-08 17:00:00",
        }])
        succ_app.invalidate_recordset()
        self.assertEqual(succ_app.state, "01_in_progress")

        # 4. Bug #4: add_timeline_dependency state rules & cycle check
        t_unfinished = self.env["project.task"].create({
            "name": "Unfinished Source",
            "project_id": proj.id,
            "state": "01_in_progress",
        })
        t_done_target = self.env["project.task"].create({
            "name": "Done Target",
            "project_id": proj.id,
            "state": "1_done",
            "progress": 1.0,
        })
        self.env["project.task"].add_timeline_dependency(f"task_{t_unfinished.id}", f"task_{t_done_target.id}")
        t_done_target.invalidate_recordset()
        self.assertEqual(t_done_target.state, "1_done")
        with self.assertRaises(ValidationError):
            self.env["project.task"].add_timeline_dependency(f"task_{t_done_target.id}", f"task_{t_unfinished.id}")

        (c1 | c2 | parent | mt1 | mt2 | succ_app | pred_app | t_done_target | t_unfinished).unlink()
        ms.unlink()
        proj.unlink()

    def test_20_project_tasks_purchase_mrp_delivery_hierarchy(self):
        """Verify strict section tree ordering for installed integrations: Tasks -> PO -> MO -> Delivery."""
        p_vals = {"name": "Test Hierarchy Project"}
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        proj = self.env["project.project"].create(p_vals)

        task = self.env["project.task"].create({
            "name": "Design Task",
            "project_id": proj.id,
            "planned_date_start": "2026-05-04 08:00:00",
            "planned_date_end": "2026-05-08 17:00:00",
        })

        partner = self.env["res.partner"].create({"name": "Test Supplier"})
        has_po = "purchase.order" in self.env and "project_id" in self.env["purchase.order"]._fields
        prod = self.env["product.product"].search([], limit=1) if "product.product" in self.env else False
        has_mo = "mrp.production" in self.env and "project_id" in self.env["mrp.production"]._fields and bool(prod)
        pt_out = (
            self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1)
            if "stock.picking.type" in self.env
            else False
        )
        has_do = "stock.picking" in self.env and "project_id" in self.env["stock.picking"]._fields and bool(pt_out)

        po = mo = delivery = False
        if has_po:
            po = self.env["purchase.order"].create({
                "name": "TEST_PO_01",
                "partner_id": partner.id,
                "project_id": proj.id,
                "date_order": "2026-05-01 08:00:00",
                "date_planned": "2026-05-15 17:00:00",
                "state": "purchase",
            })

        if has_mo:
            mo = self.env["mrp.production"].create({
                "name": "TEST_MO_01",
                "product_id": prod.id,
                "product_uom_id": prod.uom_id.id,
                "product_qty": 1.0,
                "project_id": proj.id,
                "date_start": "2026-05-11 08:00:00",
                "date_deadline": "2026-05-25 17:00:00",
                "state": "progress",
            })

        if has_do:
            delivery = self.env["stock.picking"].create({
                "name": "TEST_DO_01",
                "picking_type_id": pt_out.id,
                "partner_id": partner.id,
                "project_id": proj.id,
                "location_id": pt_out.default_location_src_id.id,
                "location_dest_id": pt_out.default_location_dest_id.id,
                "scheduled_date": "2026-05-26 10:00:00",
            })

        tl = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj.id)
        task_ids = [t["id"] for t in tl["tasks"]]
        task_map = {t["id"]: t for t in tl["tasks"]}

        tg_id = f"tasks_group_{proj.id}"
        self.assertIn(tg_id, task_ids)
        self.assertEqual(task_map[f"task_{task.id}"]["parent"], tg_id)
        expected_sections = [tg_id]

        if po:
            po_id = f"po_group_{proj.id}"
            self.assertIn(po_id, task_ids)
            expected_sections.append(po_id)
            self.assertEqual(task_map[f"po_{po.id}"]["parent"], po_id)
            self.assertTrue(task_map[f"po_{po.id}"]["is_po"])
            self.assertTrue(task_map[f"po_{po.id}"]["readonly"])

        if mo:
            mo_id = f"mo_group_{proj.id}"
            self.assertIn(mo_id, task_ids)
            expected_sections.append(mo_id)
            self.assertEqual(task_map[f"mo_{mo.id}"]["parent"], mo_id)
            self.assertTrue(task_map[f"mo_{mo.id}"]["is_mo"])
            self.assertTrue(task_map[f"mo_{mo.id}"]["readonly"])

        if delivery:
            do_id = f"delivery_group_{proj.id}"
            self.assertIn(do_id, task_ids)
            expected_sections.append(do_id)
            self.assertEqual(task_map[f"delivery_{delivery.id}"]["parent"], do_id)
            self.assertTrue(task_map[f"delivery_{delivery.id}"]["is_delivery"])
            self.assertTrue(task_map[f"delivery_{delivery.id}"]["readonly"])

        # Assert strict relative ordering: Tasks -> PO -> MO -> Delivery
        section_indices = [task_ids.index(sec_id) for sec_id in expected_sections]
        self.assertEqual(section_indices, sorted(section_indices))

        # Cleanup
        if delivery:
            try:
                delivery.sudo().write({"state": "cancel"})
                delivery.sudo().unlink()
            except Exception:
                pass

        if mo:
            try:
                mo.sudo().write({"state": "cancel"})
                mo.sudo().unlink()
            except Exception:
                pass

        if po:
            try:
                po.sudo().button_cancel()
                po.sudo().unlink()
            except Exception:
                pass

        task.unlink()
        partner.unlink()
        proj.unlink()

    def test_21_only_mo_production_filter(self):
        """Verify only_mo=True returns only manufacturing orders across the company."""
        if "mrp.production" not in self.env:
            self.skipTest("mrp module is not installed")
        prod = self.env["product.product"].search([], limit=1)
        mo_standalone = self.env["mrp.production"].create({
            "name": "TEST_SMO_01",
            "product_id": prod.id,
            "product_uom_id": prod.uom_id.id,
            "product_qty": 1.0,
            "project_id": False,
            "date_start": "2026-06-01 08:00:00",
            "date_deadline": "2026-06-10 17:00:00",
            "state": "progress",
        })

        tl = self.env["project.task"].get_all_in_one_timeline_data(only_mo=True)
        smo_item = next((t for t in tl["tasks"] if t["id"] == f"mo_{mo_standalone.id}"), None)
        self.assertIsNotNone(smo_item)
        self.assertTrue(smo_item["is_mo"])
        self.assertTrue(smo_item["readonly"])
        self.assertFalse(smo_item["project_id"])

        for t in tl["tasks"]:
            self.assertTrue(t.get("is_mo"), f"Non-MO item found in only_mo filter: {t['id']}")
            self.assertTrue(t.get("readonly"))

        try:
            mo_standalone.sudo().write({"state": "cancel"})
            mo_standalone.sudo().unlink()
        except Exception:
            pass

    def test_22_three_tier_permissions_view_worker_edit(self):
        """Verify 3-tier access: Can View (readonly, no timer), Can Track Time (timer allowed, no Gantt edits), Can Edit (full access)."""
        from odoo.exceptions import AccessError

        group_view = self.env.ref("GanntLT.group_gannt_lt_user")
        group_worker = self.env.ref("GanntLT.group_gannt_lt_worker")
        group_manager = self.env.ref("GanntLT.group_gannt_lt_manager")
        group_proj_user = self.env.ref("project.group_project_user")

        # 1. User with Can View only
        user_viewer = self.env["res.users"].create({
            "name": "Test Viewer Only",
            "login": "test_viewer_only",
            "email": "viewer@armetlina.lt",
            "group_ids": [(6, 0, [group_proj_user.id, group_view.id])],
        })
        tl_viewer = self.env["project.task"].with_user(user_viewer).get_all_in_one_timeline_data(project_id=self.project.id)
        self.assertFalse(tl_viewer["can_edit"])
        self.assertFalse(tl_viewer["can_track_time"])

        task_test = self.env["project.task"].create({
            "name": "Test Permission Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-06-01 08:00:00",
            "planned_date_end": "2026-06-05 17:00:00",
        })

        # Can View cannot start work timer
        with self.assertRaises(AccessError):
            task_test.with_user(user_viewer).action_timer_start()

        # Can View cannot save batch schedule
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(user_viewer).save_timeline_batch_schedule([{
                "id": f"task_{task_test.id}",
                "start_date": "2026-06-02",
                "end_date": "2026-06-06",
            }])

        # 2. User with Can Track Time (Worker)
        user_worker = self.env["res.users"].create({
            "name": "Test Worker Tracker",
            "login": "test_worker_tracker",
            "email": "worker@armetlina.lt",
            "group_ids": [(6, 0, [group_proj_user.id, group_worker.id])],
        })
        tl_worker = self.env["project.task"].with_user(user_worker).get_all_in_one_timeline_data(project_id=self.project.id)
        self.assertFalse(tl_worker["can_edit"], "Worker must NOT have can_edit in Gantt")
        self.assertTrue(tl_worker["can_track_time"], "Worker MUST have can_track_time")

        # Worker CAN start work timer
        task_test.with_user(user_worker).action_timer_start()
        self.assertTrue(task_test.is_timer_running)
        self.assertEqual(task_test.active_user_id.id, user_worker.id)

        # Worker CAN pause and finish timer
        task_test.with_user(user_worker).action_timer_finish()
        self.assertFalse(task_test.is_timer_running)

        # Worker CANNOT edit Gantt schedule
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(user_worker).save_timeline_batch_schedule([{
                "id": f"task_{task_test.id}",
                "start_date": "2026-06-02",
                "end_date": "2026-06-06",
            }])

        # 3. User with Can Edit (Manager)
        user_manager = self.env["res.users"].create({
            "name": "Test Gantt Manager",
            "login": "test_gantt_manager",
            "email": "manager@armetlina.lt",
            "group_ids": [(6, 0, [group_proj_user.id, group_manager.id])],
        })
        tl_mgr = self.env["project.task"].with_user(user_manager).get_all_in_one_timeline_data(project_id=self.project.id)
        self.assertTrue(tl_mgr["can_edit"])
        self.assertTrue(tl_mgr["can_track_time"])

        task_mgr = self.env["project.task"].create({
            "name": "Task For Manager",
            "project_id": self.project.id,
            "planned_date_start": "2026-06-01 08:00:00",
            "planned_date_end": "2026-06-05 17:00:00",
        })

        # Manager CAN save batch schedule on unapproved task
        res = self.env["project.task"].with_user(user_manager).save_timeline_batch_schedule([{
            "id": f"task_{task_mgr.id}",
            "start_date": "2026-06-02",
            "end_date": "2026-06-06",
        }])
        self.assertTrue(res)

    def _make_user(self, login_prefix, group_xmlids):
        grp_ids = []
        for xmlid in group_xmlids:
            grp = self.env.ref(xmlid, raise_if_not_found=False)
            if grp:
                grp_ids.append(grp.id)
        token = secrets.token_hex(4)
        return self.env["res.users"].create({
            "name": f"Test {login_prefix} {token}",
            "login": f"{login_prefix}_{token}",
            "email": f"{login_prefix}_{token}@example.com",
            "password": secrets.token_urlsafe(12),
            "group_ids": [(6, 0, grp_ids)],
        })

    def test_23_context_spoofing_defense(self):
        """Spoofed context flags without _gannt_lt_trusted_token must not bypass security or anti-cheating."""
        user_mgr = self._make_user(
            "spoof_mgr",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_manager"],
        )
        task_app = self.env["project.task"].create({
            "name": "Spoof Guard Approved Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-06-08 08:00:00",
            "planned_date_end": "2026-06-12 17:00:00",
            "allocated_hours": 40.0,
            "state": "01_in_progress",
        })
        task_app.with_user(self.user_a).action_timer_start()
        task_app.with_user(self.user_a).action_timer_finish()
        task_app.invalidate_recordset()
        self.assertTrue(task_app.has_been_approved)

        spoof_ctx = {
            "from_timer_finish": True,
            "skip_hierarchy_propagation": True,
            "gannt_admin_override": True,
        }

        with self.assertRaises(AccessError):
            task_app.with_user(self.user_a).with_context(**spoof_ctx).write({
                "planned_date_start": "2026-06-09 08:00:00",
            })
        with self.assertRaises(AccessError):
            task_app.with_user(self.user_a).with_context(**spoof_ctx).write({"has_been_approved": False})

        with self.assertRaises(AccessError):
            task_app.with_user(user_mgr).with_context(**spoof_ctx).write({"has_been_approved": False})
        with self.assertRaises(ValidationError):
            task_app.with_user(user_mgr).with_context(**spoof_ctx).write({
                "planned_date_start": "2026-06-09 08:00:00",
            })
        with self.assertRaises(ValidationError):
            task_app.with_user(user_mgr).with_context(**spoof_ctx).write({
                "planned_date_end": "2026-06-09 17:00:00",
            })
        with self.assertRaises(ValidationError):
            task_app.with_user(user_mgr).with_context(**spoof_ctx).write({
                "allocated_hours": 4.0,
            })

    def test_24_three_tier_direct_write_enforcement(self):
        """Direct write() strictly enforces Can View, Can Track Time, and approval-lock field immutability."""
        user_viewer = self._make_user(
            "tier_viewer",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_user"],
        )
        user_mgr = self._make_user(
            "tier_mgr",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_manager"],
        )
        task_pred = self.env["project.task"].create({
            "name": "Tier Predecessor",
            "project_id": self.project.id,
        })
        task = self.env["project.task"].create({
            "name": "Tier Write Enforcement Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-07-06 08:00:00",
            "planned_date_end": "2026-07-10 17:00:00",
            "state": "01_in_progress",
        })

        user_no_gantt = self._make_user(
            "tier_no_gantt",
            ["base.group_user", "project.group_project_manager"],
        )
        # Tier 0 (GanntLT = No): blocked from Gantt timeline & RPCs, but CAN edit all standard Odoo project/task fields & statuses
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(user_no_gantt).get_all_in_one_timeline_data(project_id=self.project.id)
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(user_no_gantt).save_timeline_batch_schedule([{
                "id": f"task_{task.id}",
                "start_date": "2026-07-07",
                "end_date": "2026-07-11",
            }])
        self.assertTrue(task.with_user(user_no_gantt).can_edit_gannt_dates)
        self.assertTrue(self.project.with_user(user_no_gantt).can_edit_gannt_dates)
        task.with_user(user_no_gantt).write({
            "date_deadline": "2026-07-12 17:00:00",
            "allocated_hours": 12.0,
            "state": "1_done",
        })
        self.assertEqual(task.state, "1_done")
        task.with_user(user_no_gantt).write({"state": "01_in_progress"})
        self.project.with_user(user_no_gantt).write({
            "date_start": "2026-07-01",
            "date": "2026-07-31",
            "last_update_status": "on_track",
        })
        self.assertEqual(self.project.last_update_status, "on_track")

        # Tier 1: Can View blocked on schedule and timer writes, but CAN mark standard Odoo task state Done/Approved
        for vals in (
            {"is_timer_running": True},
            {"planned_date_start": "2026-07-07 08:00:00"},
        ):
            with self.assertRaises(AccessError):
                task.with_user(user_viewer).write(vals)
        task.with_user(user_viewer).write({"name": "Viewer Neutral Rename"})
        self.assertEqual(task.name, "Viewer Neutral Rename")
        task.with_user(user_viewer).write({"state": "1_done"})
        self.assertEqual(task.state, "1_done")
        task.with_user(user_viewer).write({"state": "01_in_progress"})
        self.assertEqual(task.state, "01_in_progress")

        # Tier 2: Can Track Time blocked on schedule and approval-lock writes
        for vals in (
            {"planned_date_start": "2026-07-07 08:00:00"},
            {"planned_date_end": "2026-07-13 17:00:00"},
            {"allocated_hours": 16.0},
            {"depend_on_ids": [(4, task_pred.id)]},
            {"has_been_approved": True},
            {"approved_baseline_duration": 1.0},
        ):
            with self.assertRaises(AccessError):
                task.with_user(self.user_a).write(vals)

        # Tier 3: Can Edit still blocked from directly forging approval-lock fields
        for vals in (
            {"has_been_approved": True},
            {"first_approved_date": "2026-07-06 10:00:00"},
            {"approved_planned_date_start": "2026-07-06 08:00:00"},
            {"approved_planned_date_end": "2026-07-10 17:00:00"},
            {"approved_baseline_duration": 2.0},
        ):
            with self.assertRaises(AccessError):
                task.with_user(user_mgr).write(vals)

    def test_25_pause_wizard_security_and_analytic_date(self):
        """Pause wizard enforces timer ACL, active-user lock, context_today analytic line, and idempotency."""
        user_viewer = self._make_user(
            "pw_viewer",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_user"],
        )
        hr_emp = self.env["hr.employee"].sudo()
        for worker in (self.user_a, self.user_b):
            if not hr_emp.search([("user_id", "=", worker.id)], limit=1):
                hr_emp.create({"name": worker.name, "user_id": worker.id, "company_id": self.env.company.id})

        proj_ts_vals = {"name": "Pause Wizard Timesheet Project", "privacy_visibility": "employees"}
        if "billing_type" in self.env["project.project"]._fields:
            proj_ts_vals["billing_type"] = "not_billable"
        if "allow_timesheets" in self.env["project.project"]._fields:
            proj_ts_vals["allow_timesheets"] = True
        proj_ts = self.env["project.project"].create(proj_ts_vals)

        task = self.env["project.task"].create({
            "name": "Pause Wizard Task",
            "project_id": proj_ts.id,
            "user_ids": [(6, 0, [self.user_a.id])],
            "planned_date_start": "2026-08-03 08:00:00",
            "planned_date_end": "2026-08-05 17:00:00",
            "state": "01_in_progress",
        })
        task.with_user(self.user_a).action_timer_start()
        t_past = fields.Datetime.now() - timedelta(minutes=30)
        task.sudo().write({"timer_start": t_past})

        # Viewer must fail ACL / timer access check
        with self.assertRaises(AccessError):
            wiz_v = self.env["project.task.pause.wizard"].with_user(user_viewer).create({
                "task_id": task.id,
                "timer_start": t_past,
                "reason": "Unauthorized pause",
            })
            wiz_v.action_confirm_pause()

        # Different active worker must hit active-user lock (UserError)
        with self.assertRaises(UserError):
            wiz_b = self.env["project.task.pause.wizard"].with_user(self.user_b).create({
                "task_id": task.id,
                "timer_start": t_past,
                "reason": "Other worker pause attempt",
            })
            wiz_b.action_confirm_pause()

        # Active worker confirms pause and creates exactly one analytic line dated context_today
        wiz_env_a = self.env["project.task.pause.wizard"].with_user(self.user_a)
        wiz_a = wiz_env_a.create({
            "task_id": task.id,
            "timer_start": t_past,
            "reason": "Material restock",
        })
        expected_today = fields.Date.context_today(wiz_a)
        res = wiz_a.action_confirm_pause()
        self.assertEqual(res.get("type"), "ir.actions.act_window_close")
        task.invalidate_recordset()
        self.assertFalse(task.is_timer_running)
        self.assertTrue(task.is_paused)
        self.assertEqual(task.last_pause_reason, "Material restock")

        lines = self.env["account.analytic.line"].sudo().search([("task_id", "=", task.id)])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines.date, expected_today)
        self.assertGreater(lines.unit_amount, 0.0)

        # Calling stale wizard again must NOT create duplicate analytic line
        wiz_a.action_confirm_pause()
        lines_after = self.env["account.analytic.line"].sudo().search([("task_id", "=", task.id)])
        self.assertEqual(len(lines_after), 1)

    def test_26_cross_company_and_followers_idor_defense(self):
        """Gantt manager in Company A cannot read, edit, create milestones, or delete Company B or private follower projects."""
        user_viewer = self._make_user(
            "idor_viewer",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_user"],
        )
        user_mgr = self._make_user(
            "idor_mgr",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_manager"],
        )
        comp_b = self.env["res.company"].sudo().create({"name": "Company B IDOR Test"})
        proj_b_vals = {
            "name": "Company B Secret Project",
            "company_id": comp_b.id,
            "privacy_visibility": "employees",
        }
        proj_priv_vals = {
            "name": "Company A Private Followers Project",
            "company_id": self.env.company.id,
            "privacy_visibility": "followers",
        }
        if "billing_type" in self.env["project.project"]._fields:
            proj_b_vals["billing_type"] = "not_billable"
            proj_priv_vals["billing_type"] = "not_billable"
        proj_comp_b = self.env["project.project"].sudo().create(proj_b_vals)
        proj_priv = self.env["project.project"].sudo().create(proj_priv_vals)
        # Ensure mgr partner is not an accidental follower on private project
        proj_priv.message_unsubscribe([user_mgr.partner_id.id])

        task_priv = self.env["project.task"].sudo().create({
            "name": "Private Followers Task",
            "project_id": proj_priv.id,
        })
        task_priv.message_unsubscribe([user_mgr.partner_id.id])

        mgr_task_env = self.env["project.task"].with_user(user_mgr)
        for p_bad in (proj_comp_b, proj_priv):
            with self.assertRaises(AccessError):
                mgr_task_env.save_timeline_batch_schedule([{
                    "id": f"proj_{p_bad.id}",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-10",
                }])
            with self.assertRaises(AccessError):
                mgr_task_env.delete_timeline_item(f"proj_{p_bad.id}")

        with self.assertRaises(AccessError):
            mgr_task_env.delete_timeline_item(f"task_{task_priv.id}")
        with self.assertRaises(AccessError):
            mgr_task_env.create_timeline_milestone("IDOR MS", proj_priv.id, "2026-08-01", "2026-08-05")

        self.assertTrue(proj_comp_b.sudo().exists())
        self.assertTrue(proj_priv.sudo().exists())
        self.assertTrue(task_priv.sudo().exists())

        # Non-managers cannot call action_recompute_timeline_spans
        for non_mgr in (user_viewer, self.user_a):
            with self.assertRaises(AccessError):
                self.env["project.task"].with_user(non_mgr).action_recompute_timeline_spans(project_id=self.project.id)

        # Read IDOR check (must run after mutation guards so all ACL assertions execute):
        for p_bad in (proj_comp_b, proj_priv):
            tl = mgr_task_env.get_all_in_one_timeline_data(project_id=p_bad.id)
            tl_ids = {t["id"] for t in tl["tasks"]}
            self.assertNotIn(f"proj_{p_bad.id}", tl_ids)
            self.assertNotIn(f"task_{task_priv.id}", tl_ids)

    def test_27_progress_scale_roundtrip(self):
        """0.0..1.0 progress scale roundtrips accurately through write(), batch RPC, and timeline payload."""
        hr_emp = self.env["hr.employee"].sudo()
        emp_a = hr_emp.search([("user_id", "=", self.user_a.id)], limit=1)
        if not emp_a:
            emp_a = hr_emp.create({"name": self.user_a.name, "user_id": self.user_a.id, "company_id": self.env.company.id})

        proj_prog_vals = {"name": "Progress Roundtrip Project", "privacy_visibility": "employees"}
        if "billing_type" in self.env["project.project"]._fields:
            proj_prog_vals["billing_type"] = "not_billable"
        if "allow_timesheets" in self.env["project.project"]._fields:
            proj_prog_vals["allow_timesheets"] = True
        proj_prog = self.env["project.project"].create(proj_prog_vals)

        t_prog = self.env["project.task"].create({
            "name": "Progress Scale Task",
            "project_id": proj_prog.id,
            "planned_date_start": "2026-10-05 08:00:00",
            "planned_date_end": "2026-10-09 17:00:00",
            "allocated_hours": 10.0,
            "state": "01_in_progress",
        })
        self.env["account.analytic.line"].sudo().create({
            "name": "45% logged effort",
            "task_id": t_prog.id,
            "project_id": proj_prog.id,
            "employee_id": emp_a.id,
            "user_id": self.user_a.id,
            "date": date(2026, 10, 6),
            "unit_amount": 4.5,
        })
        t_prog.invalidate_recordset()
        self.assertAlmostEqual(t_prog.progress, 0.45, places=2)

        tl0 = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj_prog.id)
        item0 = next(t for t in tl0["tasks"] if t["id"] == f"task_{t_prog.id}")
        self.assertAlmostEqual(item0["progress"], 0.45, places=2)
        self.assertEqual(item0["progress_percent"], 45)

        t_prog.write({"progress": 0.55})
        self.assertAlmostEqual(t_prog.progress, 0.55, places=2)

        self.env["project.task"].save_timeline_batch_schedule([{
            "id": f"task_{t_prog.id}",
            "progress": 0.65,
        }])
        self.env.flush_all()
        t_prog.invalidate_recordset()
        self.assertAlmostEqual(t_prog.progress, 0.65, places=2)
        self.assertAlmostEqual(t_prog.allocated_hours, 10.0, places=2)
        self.assertEqual(t_prog.state, "01_in_progress")

        tl = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj_prog.id)
        item = next(t for t in tl["tasks"] if t["id"] == f"task_{t_prog.id}")
        self.assertAlmostEqual(item["progress"], 0.65, places=2)
        self.assertEqual(item["progress_percent"], 65)

        # Updating both dates and progress together must recalculate allocated_hours WITHOUT clobbering progress
        self.env["project.task"].save_timeline_batch_schedule([{
            "id": f"task_{t_prog.id}",
            "start_date": "2026-10-05 08:00:00",
            "end_date": "2026-10-12 17:00:00",
            "progress": 0.72,
        }])
        self.env.flush_all()
        t_prog.invalidate_recordset()
        self.assertAlmostEqual(t_prog.progress, 0.72, places=2)
        self.assertGreater(t_prog.allocated_hours, 40.0)

        self.env["project.task"].save_timeline_batch_schedule([{
            "id": f"task_{t_prog.id}",
            "progress": 1.0,
        }])
        self.env.flush_all()
        t_prog.invalidate_recordset()
        self.assertAlmostEqual(t_prog.progress, 1.0, places=2)
        self.assertEqual(t_prog.state, "1_done")
        tl_done = self.env["project.task"].get_all_in_one_timeline_data(project_id=proj_prog.id)
        item_done = next(t for t in tl_done["tasks"] if t["id"] == f"task_{t_prog.id}")
        self.assertAlmostEqual(item_done["progress"], 1.0, places=2)
        self.assertEqual(item_done["progress_percent"], 100)

    def test_30_manual_and_auto_deadline_early_finish(self):
        """Early finish preserves manual deadline (deadline_is_auto=False) and clears auto deadline (deadline_is_auto=True)."""
        # Manual deadline preserved on early finish
        manual_dl = datetime(2026, 10, 20, 17, 0, 0)
        t_manual = self.env["project.task"].create({
            "name": "Manual Deadline Preserved Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-10-05 08:00:00",
            "planned_date_end": "2026-10-16 17:00:00",
            "date_deadline": manual_dl,
            "state": "01_in_progress",
        })
        self.assertFalse(t_manual.deadline_is_auto)
        t_manual.with_user(self.user_a).action_timer_finish(force_now=datetime(2026, 10, 8, 12, 0, 0))
        t_manual.invalidate_recordset()
        self.assertEqual(t_manual.date_deadline, manual_dl)
        self.assertFalse(t_manual.deadline_is_auto)

        # Auto deadline cleared on early finish
        t_auto = self.env["project.task"].create({
            "name": "Auto Deadline Cleared Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-10-05 08:00:00",
            "planned_date_end": "2026-10-16 17:00:00",
            "state": "01_in_progress",
        })
        t_auto.sudo().with_context(gannt_auto_plan=True).write({
            "date_deadline": datetime(2026, 10, 22, 17, 0, 0),
            "deadline_is_auto": True,
        })
        t_auto.with_user(self.user_a).action_timer_finish(force_now=datetime(2026, 10, 8, 12, 0, 0))
        t_auto.invalidate_recordset()
        self.assertFalse(t_auto.date_deadline)
        self.assertFalse(t_auto.deadline_is_auto)

    def test_28_empty_domain_readonly_get_and_timer_state_guards(self):
        """Empty domain returns empty tasks, get_all_in_one performs 0 DB writes, and timer finish/reopen guards work."""
        empty_tl = self.env["project.task"].get_all_in_one_timeline_data(
            domain=[("name", "=", "__NON_EXISTENT_GANNT_LT_PROJECT_999__")]
        )
        self.assertEqual(empty_tl["tasks"], [])
        self.assertEqual(empty_tl["links"], [])

        # Verify get_all_in_one_timeline_data performs zero SQL writes even on missing allocated_hours
        self.env["project.task"].create({
            "name": "Readonly Query Audit Task",
            "project_id": self.project.id,
            "planned_date_start": "2026-11-02 08:00:00",
            "planned_date_end": "2026-11-04 17:00:00",
        })
        self.env.flush_all()

        write_sql = []
        orig_execute = self.env.cr.execute

        def spy_execute(query, params=None, log_exceptions=True):
            q_str = query.code if hasattr(query, "code") else str(query)
            q_upper = q_str.lstrip().upper()
            if q_upper.startswith(("INSERT", "UPDATE", "DELETE")):
                write_sql.append(q_str[:120])
            return orig_execute(query, params=params, log_exceptions=log_exceptions)

        with patch.object(self.env.cr, "execute", side_effect=spy_execute):
            ro_tl = self.env["project.task"].get_all_in_one_timeline_data(project_id=self.project.id)
        self.assertTrue(ro_tl["tasks"])
        self.assertEqual(write_sql, [], f"Expected zero DB writes on read, saw: {write_sql}")

        # action_timer_finish returns False on canceled task
        t_cancel = self.env["project.task"].create({
            "name": "Canceled Finish Guard Task",
            "project_id": self.project.id,
            "state": "1_canceled",
        })
        self.assertFalse(t_cancel.with_user(self.user_a).action_timer_finish())
        self.assertEqual(t_cancel.state, "1_canceled")

        # action_timer_reopen enforces active worker lock
        t_reopen = self.env["project.task"].create({
            "name": "Reopen Lock Task",
            "project_id": self.project.id,
            "state": "01_in_progress",
        })
        t_reopen.with_user(self.user_a).action_timer_start()
        with self.assertRaises(UserError):
            t_reopen.with_user(self.user_b).action_timer_reopen()
        self.assertTrue(t_reopen.with_user(self.user_a).action_timer_reopen())
        t_reopen.invalidate_recordset()
        self.assertEqual(t_reopen.state, "01_in_progress")
        self.assertFalse(t_reopen.is_timer_running)

    def test_29_milestone_constraints_and_cascade_cycle_guards(self):
        """Milestone enforces planned_date_end >= date_start, supports multi-record write, and cascades resist cycles."""
        ms_model = self.env["project.milestone"]
        with self.assertRaises(ValidationError):
            ms_model.create({
                "name": "Invalid Reversed Milestone",
                "project_id": self.project.id,
                "date_start": "2026-09-10",
                "planned_date_end": "2026-09-05",
            })

        ms1 = ms_model.create({
            "name": "MS Sync 1",
            "project_id": self.project.id,
            "date_start": "2026-09-05",
            "planned_date_end": "2026-09-15",
        })
        ms2 = ms_model.create({
            "name": "MS Sync 2",
            "project_id": self.project.id,
            "date_start": "2026-09-05",
            "deadline": "2026-09-18",
        })
        self.assertFalse(ms1.deadline)
        self.assertEqual(ms2.planned_date_end, date(2026, 9, 18))

        # Multi-record write succeeds
        (ms1 | ms2).write({"date_start": "2026-09-02"})
        self.assertEqual(ms1.date_start, date(2026, 9, 2))
        self.assertEqual(ms2.date_start, date(2026, 9, 2))
        with self.assertRaises(ValidationError):
            (ms1 | ms2).write({"planned_date_end": "2026-09-01"})

        # Cycle guards on _cascade_clear_downstream_progress and _cascade_planned_dates_to_unscheduled_descendants
        t_cyc_a = self.env["project.task"].create({
            "name": "Cycle Dep A",
            "project_id": self.project.id,
            "state": "03_approved",
        })
        t_cyc_b = self.env["project.task"].create({
            "name": "Cycle Dep B",
            "project_id": self.project.id,
            "state": "03_approved",
        })
        dep_field = self.env["project.task"]._fields["depend_on_ids"]
        rel_tbl = dep_field.relation
        col1 = dep_field.column1
        col2 = dep_field.column2
        self.env.cr.execute(
            f"INSERT INTO {rel_tbl} ({col1}, {col2}) VALUES (%s, %s), (%s, %s) ON CONFLICT DO NOTHING",
            (t_cyc_a.id, t_cyc_b.id, t_cyc_b.id, t_cyc_a.id),
        )
        self.env.invalidate_all()
        t_cyc_a._cascade_clear_downstream_progress()
        self.env.cr.execute(
            f"DELETE FROM {rel_tbl} WHERE ({col1} = %s AND {col2} = %s) OR ({col1} = %s AND {col2} = %s)",
            (t_cyc_a.id, t_cyc_b.id, t_cyc_b.id, t_cyc_a.id),
        )

        p_cyc_a = self.env["project.task"].create({"name": "Cycle Parent A", "project_id": self.project.id})
        p_cyc_b = self.env["project.task"].create({"name": "Cycle Parent B", "project_id": self.project.id})
        self.env.cr.execute(
            "UPDATE project_task SET parent_id = %s WHERE id = %s",
            (p_cyc_b.id, p_cyc_a.id),
        )
        self.env.cr.execute(
            "UPDATE project_task SET parent_id = %s WHERE id = %s",
            (p_cyc_a.id, p_cyc_b.id),
        )
        self.env.invalidate_all()
        p_cyc_a._cascade_planned_dates_to_unscheduled_descendants(
            datetime(2026, 11, 2, 8, 0, 0),
            datetime(2026, 11, 3, 17, 0, 0),
        )
        self.env.cr.execute(
            "UPDATE project_task SET parent_id = NULL WHERE id IN %s",
            ((p_cyc_a.id, p_cyc_b.id),),
        )
        self.env.invalidate_all()

    def test_31_project_completion_deadline_and_early_finish(self):
        """Completing a project late adds red delay tail; completing early adds green early tail; reopening clears auto fields."""
        today = fields.Date.context_today(self.project)
        Project = self.env["project.project"]
        Task = self.env["project.task"]
        Stage = self.env["project.project.stage"]
        Update = self.env["project.update"]

        stage_in_prog = Stage.create({"name": "In Progress Test", "fold": False})
        stage_done = Stage.create({"name": "Done Test", "fold": True})

        def mark_done(proj):
            Update.create({"name": "Done Update", "project_id": proj.id, "status": "done"})

        def mark_reopen(proj):
            Update.create({"name": "Reopen Update", "project_id": proj.id, "status": "on_track"})

        # 1. Late project completion via project.update(status='done') (planned end in the past, tasks finished on time)
        p_late = Project.create({
            "name": "Proj Late Complete",
            "date_start": today - timedelta(days=20),
            "date": today - timedelta(days=5),
        })
        Task.create({
            "name": "On Time Task",
            "project_id": p_late.id,
            "planned_date_start": datetime.combine(today - timedelta(days=15), datetime.min.time()) + timedelta(hours=8),
            "planned_date_end": datetime.combine(today - timedelta(days=10), datetime.min.time()) + timedelta(hours=17),
            "state": "1_done",
        })
        mark_done(p_late)
        self.assertEqual(p_late.actual_finish_date, today)
        self.assertEqual(p_late.date_deadline, today)
        self.assertTrue(p_late.deadline_is_auto)
        tl_late = Task.get_all_in_one_timeline_data(project_id=p_late.id)
        p_row_late = next(r for r in tl_late["tasks"] if r["id"] == f"proj_{p_late.id}")
        self.assertTrue(p_row_late["has_deadline_delay"])
        self.assertFalse(p_row_late["has_early_finish"])

        # Reopening clears actual_finish_date and auto deadline
        mark_reopen(p_late)
        self.assertFalse(p_late.actual_finish_date)
        self.assertFalse(p_late.date_deadline)
        self.assertFalse(p_late.deadline_is_auto)

        # 2. Early project completion via closed stage_id (planned end in the future, no delayed tasks)
        p_early = Project.create({
            "name": "Proj Early Complete",
            "date_start": today - timedelta(days=10),
            "date": today + timedelta(days=7),
            "stage_id": stage_in_prog.id,
        })
        p_early.write({"stage_id": stage_done.id})
        self.assertEqual(p_early.actual_finish_date, today)
        self.assertFalse(p_early.date_deadline)
        tl_early = Task.get_all_in_one_timeline_data(project_id=p_early.id)
        p_row_early = next(r for r in tl_early["tasks"] if r["id"] == f"proj_{p_early.id}")
        self.assertFalse(p_row_early["has_deadline_delay"])
        self.assertTrue(p_row_early["has_early_finish"])
        self.assertGreater(p_row_early["early_days"], 0)

        # 3. Same-day project completion (today == date -> neither late nor early)
        p_exact = Project.create({
            "name": "Proj Exact Day Complete",
            "date_start": today - timedelta(days=5),
            "date": today,
        })
        mark_done(p_exact)
        tl_exact = Task.get_all_in_one_timeline_data(project_id=p_exact.id)
        p_row_exact = next(r for r in tl_exact["tasks"] if r["id"] == f"proj_{p_exact.id}")
        self.assertFalse(p_row_exact["has_deadline_delay"])
        self.assertFalse(p_row_exact["has_early_finish"])

        # 4. Lateness dominance: early project completion + delayed task -> delay dominates!
        p_dom = Project.create({
            "name": "Proj Delay Dominance",
            "date_start": today - timedelta(days=10),
            "date": today + timedelta(days=5),
        })
        Task.create({
            "name": "Delayed Child Task",
            "project_id": p_dom.id,
            "planned_date_start": datetime.combine(today - timedelta(days=8), datetime.min.time()) + timedelta(hours=8),
            "planned_date_end": datetime.combine(today - timedelta(days=2), datetime.min.time()) + timedelta(hours=17),
            "date_deadline": datetime.combine(today + timedelta(days=10), datetime.min.time()) + timedelta(hours=17),
            "state": "1_done",
        })
        mark_done(p_dom)
        tl_dom = Task.get_all_in_one_timeline_data(project_id=p_dom.id)
        p_row_dom = next(r for r in tl_dom["tasks"] if r["id"] == f"proj_{p_dom.id}")
        self.assertTrue(p_row_dom["has_deadline_delay"])
        self.assertFalse(p_row_dom["has_early_finish"])

        # 5. Manual deadline preserved on reopen
        manual_dl = today + timedelta(days=20)
        p_man = Project.create({
            "name": "Proj Manual Deadline",
            "date_start": today - timedelta(days=10),
            "date": today + timedelta(days=5),
            "date_deadline": manual_dl,
        })
        mark_done(p_man)
        mark_reopen(p_man)
        self.assertEqual(p_man.date_deadline, manual_dl)
        self.assertFalse(p_man.actual_finish_date)

    def test_32_editor_only_deadlines_and_continuous_tail(self):
        """Only Can Edit can edit deadlines/dates; completed projects lock dates; open-ended items extend to Today as continuous."""
        today = fields.Date.context_today(self.project)
        Project = self.env["project.project"]
        Task = self.env["project.task"]
        Update = self.env["project.update"]

        worker_user = self._make_user("dl_worker", ["base.group_user", "project.group_project_manager", "GanntLT.group_gannt_lt_worker"])
        editor_user = self._make_user("dl_editor", ["base.group_user", "project.group_project_manager", "GanntLT.group_gannt_lt_manager"])

        # 1. Open-ended continuous project & task (no end date, no deadline, created in past)
        p_cont = Project.with_user(editor_user).create({
            "name": "Continuous 2026 Project",
            "date_start": today - timedelta(days=60),
        })
        t_cont = Task.with_user(editor_user).create({
            "name": "Continuous 2026 Task",
            "project_id": p_cont.id,
            "planned_date_start": datetime.combine(today - timedelta(days=45), datetime.min.time()) + timedelta(hours=8),
        })

        # Non-editor (Can Track Time + Project Manager) cannot edit deadlines or dates directly
        with self.assertRaises(AccessError):
            t_cont.with_user(worker_user).write({"date_deadline": datetime.combine(today + timedelta(days=5), datetime.min.time())})
        with self.assertRaises(AccessError):
            p_cont.with_user(worker_user).write({"date_deadline": today + timedelta(days=5)})
        with self.assertRaises(AccessError):
            p_cont.with_user(worker_user).write({"date": today + timedelta(days=5)})
        self.assertFalse(t_cont.with_user(worker_user).can_edit_gannt_dates)
        self.assertTrue(t_cont.with_user(editor_user).can_edit_gannt_dates)

        # Continuous timeline bars extend up to today without false overdue flags
        tl_cont = Task.with_user(worker_user).get_all_in_one_timeline_data(project_id=p_cont.id)
        p_row = next(r for r in tl_cont["tasks"] if r["id"] == f"proj_{p_cont.id}")
        t_row = next(r for r in tl_cont["tasks"] if r["id"] == f"task_{t_cont.id}")
        self.assertTrue(p_row["is_continuous"])
        self.assertFalse(p_row["is_overdue_past"])
        self.assertGreaterEqual(p_row["end_date"][:10], today.strftime("%Y-%m-%d"))
        self.assertTrue(t_row["is_continuous"])
        self.assertFalse(t_row["is_overdue_past"])
        self.assertGreaterEqual(t_row["end_date"][:10], today.strftime("%Y-%m-%d"))

        # Adding a planned end date converts a previously continuous project back to a fixed-schedule project
        p_cont.with_user(editor_user).write({"date": today + timedelta(days=10)})
        tl_fixed_fut = Task.with_user(editor_user).get_all_in_one_timeline_data(project_id=p_cont.id)
        p_row_fut = next(r for r in tl_fixed_fut["tasks"] if r["id"] == f"proj_{p_cont.id}")
        self.assertFalse(p_row_fut["is_continuous"])
        self.assertFalse(p_row_fut["is_overdue_past"])

        p_cont.with_user(editor_user).write({"date": today - timedelta(days=5)})
        tl_fixed_past = Task.with_user(editor_user).get_all_in_one_timeline_data(project_id=p_cont.id)
        p_row_past = next(r for r in tl_fixed_past["tasks"] if r["id"] == f"proj_{p_cont.id}")
        self.assertFalse(p_row_past["is_continuous"])
        self.assertTrue(p_row_past["is_overdue_past"])

        # Worker CAN finish task via state='1_done' (Atlikta): saves end date, keeps start date, stops being continuous
        orig_t_start = t_cont.planned_date_start
        t_cont.with_user(worker_user).write({"state": "1_done"})
        self.assertEqual(t_cont.state, "1_done")
        self.assertTrue(t_cont.has_been_approved)
        self.assertEqual(t_cont.planned_date_start, orig_t_start)
        self.assertTrue(t_cont.planned_date_end)
        self.assertTrue(t_cont.actual_finish_date)
        tl_after_task_done = Task.with_user(worker_user).get_all_in_one_timeline_data(project_id=p_cont.id)
        t_row_done = next(r for r in tl_after_task_done["tasks"] if r["id"] == f"task_{t_cont.id}")
        self.assertFalse(t_row_done["is_continuous"])
        self.assertTrue(t_row_done["is_done"])

        # Infinite project marked Complete saves date=today and stops being continuous
        p_inf_done = Project.with_user(editor_user).create({
            "name": "Infinite To Complete Project",
            "date_start": today - timedelta(days=30),
        })
        Update.create({"name": "Complete Infinite", "project_id": p_inf_done.id, "status": "done"})
        self.assertEqual(p_inf_done.date, today)
        self.assertEqual(p_inf_done.actual_finish_date, today)
        tl_inf_done = Task.with_user(editor_user).get_all_in_one_timeline_data(project_id=p_inf_done.id)
        p_row_inf_done = next(r for r in tl_inf_done["tasks"] if r["id"] == f"proj_{p_inf_done.id}")
        self.assertFalse(p_row_inf_done["is_continuous"])
        self.assertTrue(p_row_inf_done["is_done"])

        # Completed project locks dates against reversal even for Can Edit
        p_done = Project.with_user(editor_user).create({
            "name": "Locked Done Project",
            "date_start": today - timedelta(days=20),
            "date": today - timedelta(days=5),
        })
        Update.create({"name": "Complete", "project_id": p_done.id, "status": "done"})
        self.assertTrue(p_done.has_been_approved)
        with self.assertRaises(ValidationError):
            p_done.with_user(editor_user).write({"date_start": today - timedelta(days=10)})
        with self.assertRaises(ValidationError):
            p_done.with_user(editor_user).write({"date": today - timedelta(days=10)})
        with self.assertRaises(ValidationError):
            p_done.with_user(editor_user).write({"date_deadline": False})

    def test_33_overdue_on_hold_vs_on_track(self):
        """Projects on_track past planned date stay overdue; on_hold projects are paused (not overdue)."""
        today = fields.Date.today()
        past_start = today - timedelta(days=20)
        past_end = today - timedelta(days=5)
        Project = self.env["project.project"]
        Task = self.env["project.task"]

        proj = Project.create({
            "name": "On Track Past Due Project",
            "date_start": past_start,
            "date": past_end,
            "last_update_status": "on_track",
        })
        Task.create({
            "name": "Incomplete Past Task",
            "project_id": proj.id,
            "planned_date_start": fields.Datetime.to_datetime(past_start),
            "planned_date_end": fields.Datetime.to_datetime(past_end),
            "state": "01_in_progress",
        })

        # 1. on_track past planned date -> MUST be overdue (warn PM with faint red tail)
        data1 = Task.get_all_in_one_timeline_data(project_id=proj.id)
        row1 = next(r for r in data1["tasks"] if r["id"] == f"proj_{proj.id}")
        self.assertTrue(row1["is_overdue_past"])
        self.assertFalse(row1["is_done"])

        overdue1 = Task.get_all_in_one_timeline_data(only_overdue=True)
        self.assertIn(f"proj_{proj.id}", {r["id"] for r in overdue1["tasks"]})

        # 2. Put project on_hold -> NOT overdue while paused
        proj.write({"last_update_status": "on_hold"})
        data2 = Task.get_all_in_one_timeline_data(project_id=proj.id)
        row2 = next(r for r in data2["tasks"] if r["id"] == f"proj_{proj.id}")
        self.assertFalse(row2["is_overdue_past"])

        overdue2 = Task.get_all_in_one_timeline_data(only_overdue=True)
        self.assertNotIn(f"proj_{proj.id}", {r["id"] for r in overdue2["tasks"]})

        # 3. Resume to on_track -> overdue again
        proj.write({"last_update_status": "on_track"})
        data3 = Task.get_all_in_one_timeline_data(project_id=proj.id)
        row3 = next(r for r in data3["tasks"] if r["id"] == f"proj_{proj.id}")
        self.assertTrue(row3["is_overdue_past"])

        # 4. Complete project -> no longer overdue_past, becomes done with solid delay tail
        upd = self.env["project.update"].create({
            "name": "Done late",
            "project_id": proj.id,
            "status": "done",
        })
        data4 = Task.get_all_in_one_timeline_data(project_id=proj.id)
        row4 = next(r for r in data4["tasks"] if r["id"] == f"proj_{proj.id}")
        self.assertFalse(row4["is_overdue_past"])
        self.assertTrue(row4["is_done"])
        self.assertTrue(row4["has_deadline_delay"])

    def test_34_milestone_reached_late_and_early_tail(self):
        """Marking an overdue milestone reached converts it to a solid red delay tail; early reached shows early finish."""
        today = fields.Date.today()
        past_start = today - timedelta(days=20)
        past_end = today - timedelta(days=5)
        future_start = today + timedelta(days=2)
        future_end = today + timedelta(days=10)
        Project = self.env["project.project"]
        Task = self.env["project.task"]
        Milestone = self.env["project.milestone"]

        proj = Project.create({
            "name": "Milestone Tail Project",
            "date_start": past_start,
            "date": future_end,
            "allow_milestones": True,
        })
        m_late = Milestone.create({
            "name": "Late Milestone",
            "project_id": proj.id,
            "date_start": past_start,
            "planned_date_end": past_end,
            "deadline": past_end,
        })
        m_early = Milestone.create({
            "name": "Early Milestone",
            "project_id": proj.id,
            "date_start": future_start,
            "planned_date_end": future_end,
            "deadline": future_end,
        })

        # Before reaching: m_late is overdue_past
        d_before = Task.get_all_in_one_timeline_data(project_id=proj.id)
        r_late_before = next(r for r in d_before["tasks"] if r["id"] == f"milestone_{m_late.id}")
        self.assertTrue(r_late_before["is_overdue_past"])
        self.assertFalse(r_late_before["has_deadline_delay"])

        # Mark both reached
        m_late.toggle_is_reached(True)
        m_early.toggle_is_reached(True)
        self.assertEqual(m_late.deadline, today)
        self.assertEqual(m_late.planned_date_end, past_end)

        d_after = Task.get_all_in_one_timeline_data(project_id=proj.id)
        r_late_after = next(r for r in d_after["tasks"] if r["id"] == f"milestone_{m_late.id}")
        self.assertFalse(r_late_after["is_overdue_past"])
        self.assertTrue(r_late_after["is_done"])
        self.assertTrue(r_late_after["has_deadline_delay"])
        self.assertGreater(r_late_after["delay_days"], 0)

        r_early_after = next(r for r in d_after["tasks"] if r["id"] == f"milestone_{m_early.id}")
        self.assertTrue(r_early_after["is_done"])
        self.assertFalse(r_early_after["has_deadline_delay"])
        self.assertTrue(r_early_after["has_early_finish"])
        self.assertGreater(r_early_after["early_days"], 0)

        # Un-reach m_late -> restores deadline back to planned_date_end and reverts to overdue_past
        m_late.toggle_is_reached(False)
        self.assertEqual(m_late.deadline, past_end)
        d_reopened = Task.get_all_in_one_timeline_data(project_id=proj.id)
        r_late_reopened = next(r for r in d_reopened["tasks"] if r["id"] == f"milestone_{m_late.id}")
        self.assertTrue(r_late_reopened["is_overdue_past"])
        self.assertFalse(r_late_reopened["has_deadline_delay"])


class TestGanntLTExportHttp(HttpCase):

    def _create_and_auth_user(self, login_prefix, group_xmlids):
        grp_ids = []
        for xmlid in group_xmlids:
            grp = self.env.ref(xmlid, raise_if_not_found=False)
            if grp:
                grp_ids.append(grp.id)
        token = secrets.token_hex(4)
        pw = secrets.token_urlsafe(16)
        user = self.env["res.users"].create({
            "name": f"HTTP {login_prefix} {token}",
            "login": f"{login_prefix}_{token}",
            "email": f"{login_prefix}_{token}@example.com",
            "password": pw,
            "group_ids": [(6, 0, grp_ids)],
        })
        self.authenticate(user.login, pw)
        return user

    def test_10_controller_export_excel_endpoint(self):
        """HTTP POST /gannt_lt/export_excel returns 200 with valid .xlsx binary and filename."""
        self._create_and_auth_user(
            "http_mgr",
            ["base.group_user", "project.group_project_manager", "GanntLT.group_gannt_lt_manager"],
        )
        csrf_token = http.Request.csrf_token(self)
        proj = self.env["project.project"].search([], limit=1)
        if not proj:
            p_vals = {"name": "Http Test Project"}
            if "billing_type" in self.env["project.project"]._fields:
                p_vals["billing_type"] = "not_billable"
            proj = self.env["project.project"].create(p_vals)

        # Call endpoint with JSON data payload (matching @web/core/network/download)
        response = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"project_id": proj.id, "domain": []}),
                "csrf_token": csrf_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Content-Type"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        cd = response.headers.get("Content-Disposition", "")
        self.assertIn("attachment;", cd)
        self.assertIn(".xlsx", cd)
        self.assertGreater(len(response.content), 1000)

        with zipfile.ZipFile(io.BytesIO(response.content), "r") as z:
            self.assertIn("xl/workbook.xml", z.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())

        # Test controller with null domain and raw parameters to ensure robust fallback
        resp2 = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "project_id": proj.id,
                "domain": "invalid_json_string",
                "csrf_token": csrf_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertGreater(len(resp2.content), 1000)

    def test_11_controller_export_excel_overdue_filter(self):
        """HTTP POST /gannt_lt/export_excel with only_overdue=True returns 200 with Veluojantys_projektai filename."""
        self._create_and_auth_user(
            "http_overdue",
            ["base.group_user", "project.group_project_manager", "GanntLT.group_gannt_lt_manager"],
        )
        csrf_token = http.Request.csrf_token(self)

        response = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"only_overdue": True, "domain": []}),
                "csrf_token": csrf_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 200)
        cd = response.headers.get("Content-Disposition", "")
        self.assertIn("Veluojantys_projektai", cd)
        self.assertIn(".xlsx", cd)

    def test_12_controller_export_excel_csrf_and_acl(self):
        """Verify /gannt_lt/export_excel rejects missing CSRF (400), portal users (403), and non-GanntLT users (403)."""
        self._create_and_auth_user(
            "http_csrf_mgr",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_user"],
        )
        no_csrf_resp = self.url_open(
            "/gannt_lt/export_excel",
            data={"data": json.dumps({"domain": []})},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(no_csrf_resp.status_code, 400)

        # Portal user with valid CSRF must get 403 Forbidden
        self._create_and_auth_user("http_portal", ["base.group_portal"])
        portal_resp = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"domain": []}),
                "csrf_token": http.Request.csrf_token(self),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(portal_resp.status_code, 403)

        # Internal employee without GanntLT role must get 403 Forbidden
        self._create_and_auth_user("http_internal_no_gantt", ["base.group_user", "project.group_project_user"])
        internal_resp = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"domain": []}),
                "csrf_token": http.Request.csrf_token(self),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(internal_resp.status_code, 403)

    def test_13_controller_export_excel_only_mo_formula_and_grid_shading(self):
        """Verify only_mo export, spreadsheet formula neutralization, and weekend/working blank grid shading."""
        self.assertEqual(sanitize_excel_str("=1+1"), "'=1+1")
        self.assertEqual(sanitize_excel_str("@SUM(A1:A2)"), "'@SUM(A1:A2)")

        assignee_user = self.env["res.users"].create({
            "name": "@SUM(A1:A2)",
            "login": f"formula_user_{secrets.token_hex(4)}",
            "email": f"formula_{secrets.token_hex(4)}@example.com",
            "password": secrets.token_urlsafe(12),
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("project.group_project_user").id])],
        })
        p_vals = {
            "name": "=1+1 Formula Project",
            "privacy_visibility": "employees",
            "date_start": "2026-06-01",
            "date": "2026-06-07",
        }
        if "billing_type" in self.env["project.project"]._fields:
            p_vals["billing_type"] = "not_billable"
        proj = self.env["project.project"].create(p_vals)
        self.env["project.task"].create({
            "name": "+CMD() Task",
            "project_id": proj.id,
            "user_ids": [(6, 0, [assignee_user.id])],
            "planned_date_start": "2026-06-01 08:00:00",
            "planned_date_end": "2026-06-03 17:00:00",
        })

        created_mo = None
        if "mrp.production" in self.env:
            prod = self.env["product.product"].search([], limit=1)
            if prod:
                created_mo = self.env["mrp.production"].create({
                    "name": f"TEST_HTTP_MO_{secrets.token_hex(3)}",
                    "product_id": prod.id,
                    "product_uom_id": prod.uom_id.id,
                    "product_qty": 1.0,
                    "project_id": proj.id,
                    "date_start": "2026-06-02 08:00:00",
                    "date_deadline": "2026-06-05 17:00:00",
                    "state": "progress",
                })

        self._create_and_auth_user(
            "http_viewer_exp",
            ["base.group_user", "project.group_project_user", "GanntLT.group_gannt_lt_user", "mrp.group_mrp_user"],
        )
        csrf_token = http.Request.csrf_token(self)

        # 1. only_mo HTTP export
        mo_resp = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"only_mo": True, "domain": []}),
                "csrf_token": csrf_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(mo_resp.status_code, 200)
        self.assertIn("Visi_gamybos_uzsakymai", mo_resp.headers.get("Content-Disposition", ""))
        with zipfile.ZipFile(io.BytesIO(mo_resp.content), "r") as z_mo:
            self.assertIn("xl/worksheets/sheet1.xml", z_mo.namelist())
            if created_mo and "xl/sharedStrings.xml" in z_mo.namelist():
                self.assertIn(created_mo.name.encode("utf-8"), z_mo.read("xl/sharedStrings.xml"))

        # 2. Formula neutralization + <=180d blank grid shading
        resp = self.url_open(
            "/gannt_lt/export_excel",
            data={
                "data": json.dumps({"project_id": proj.id, "domain": []}),
                "csrf_token": csrf_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.content), "r") as z:
            sheet_xml = z.read("xl/worksheets/sheet1.xml")
            styles_xml = z.read("xl/styles.xml")
            shared_xml = z.read("xl/sharedStrings.xml") if "xl/sharedStrings.xml" in z.namelist() else b""
            # Zero real Excel formulas emitted
            self.assertNotIn(b"<f>", sheet_xml)
            self.assertNotIn(b"<f ", sheet_xml)
            # Leading formula characters neutralized with apostrophe prefix
            self.assertIn(b"'=1+1 Formula Project", shared_xml)
            self.assertIn(b"'@SUM(A1:A2)", shared_xml)
            # Weekend/non-working blank grid shading format present in workbook styles & blank cells rendered
            self.assertIn(b"F8FAFC", styles_xml)
            self.assertRegex(sheet_xml.decode("utf-8"), r'<c r="[A-Z]+\d+" s="\d+"/>')

        # 3. >180d blank grid shading branch in render_project_sheet
        long_buf = io.BytesIO()
        long_wb = xlsxwriter.Workbook(long_buf, {"in_memory": True, "strings_to_formulas": False})
        long_formats = create_workbook_formats(long_wb)
        render_project_sheet(
            long_wb,
            "LongSpan",
            "Long Span Project",
            [{
                "id": "task_1",
                "text": "Long Range Task",
                "start_date": "2026-01-05 08:00:00",
                "end_date": "2026-09-15 17:00:00",
                "is_project": False,
                "is_milestone": False,
            }],
            long_formats,
            {},
        )
        long_wb.close()
        with zipfile.ZipFile(io.BytesIO(long_buf.getvalue()), "r") as z_long:
            long_sheet = z_long.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn(b"F8FAFC", z_long.read("xl/styles.xml"))
            self.assertRegex(long_sheet, r'<c r="[A-Z]+4" s="\d+"/>')




