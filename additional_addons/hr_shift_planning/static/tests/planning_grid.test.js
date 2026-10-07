import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { describe, expect, test } from "@odoo/hoot";
import { click, queryAll } from "@odoo/hoot-dom";
import { animationFrame, mockDate } from "@odoo/hoot-mock";
import { defineActions, getService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { WebClient } from "@web/webclient/webclient";

describe.current.tags("desktop");

defineActions([
    {
        id: 1,
        name: "Planning",
        tag: "hr_shift_planning.planning_grid",
        type: "ir.actions.client",
    },
]);
defineMailModels();

const PLANNING_DATA = {
    employees: [
        { id: 1, name: "Ann", job_title: "Operator", department: "Production" },
        { id: 2, name: "Bob", job_title: "", department: "Warehouse" },
    ],
    templates: [
        { id: 10, name: "Morning", code: "M", color: "#FFE08A", duration: 7.5, active: true },
    ],
    shifts: [
        {
            id: 100,
            employee_id: 1,
            date: "2026-10-15",
            template_id: 10,
            start: "2026-10-15 03:00:00",
            end: "2026-10-15 11:00:00",
            break_minutes: 30,
            duration: 7.5,
            is_custom: false,
        },
    ],
    holidays: [{ date: "2026-10-20", name: "Test holiday" }],
};

async function openGrid(requests = []) {
    onRpc("hr.shift", "get_planning_data", ({ args }) => {
        requests.push(args);
        return PLANNING_DATA;
    });
    await mountWithCleanup(WebClient);
    await getService("action").doAction(1);
}

test("renders employees, days of the month and shifts", async () => {
    mockDate("2026-10-07 10:00:00");
    const requests = [];
    await openGrid(requests);

    expect(requests).toEqual([["2026-10-01", "2026-10-31"]]);
    expect(".o_shift_month").toHaveText(/October 2026/i);
    expect(".o_shift_employee_name").toHaveCount(2);
    expect(".o_shift_day_header").toHaveCount(31);
    expect(".o_shift_cell").toHaveCount(62);
    expect(".o_shift_chip").toHaveCount(1);
    expect('.o_shift_cell[data-employee-id="1"][data-day="2026-10-15"] .o_shift_code').toHaveText("M");
    expect('.o_shift_cell[data-employee-id="1"][data-day="2026-10-15"] .o_shift_hours').toHaveText("7.5h");
});

test("marks today, weekends and holidays", async () => {
    mockDate("2026-10-07 10:00:00");
    await openGrid();

    expect('.o_shift_day_header[data-day="2026-10-07"]').toHaveClass("o_shift_today");
    // 2026-10-03 is a Saturday
    expect('.o_shift_day_header[data-day="2026-10-03"]').toHaveClass("o_shift_weekend");
    expect('.o_shift_day_header[data-day="2026-10-20"]').toHaveClass("o_shift_holiday");
    expect('.o_shift_day_header[data-day="2026-10-20"]').toHaveAttribute("title", "Test holiday");
});

test("clicking a cell highlights the cell, its employee and its day", async () => {
    mockDate("2026-10-07 10:00:00");
    await openGrid();

    await click('.o_shift_cell[data-employee-id="2"][data-day="2026-10-12"]');
    await animationFrame();

    expect('.o_shift_cell[data-employee-id="2"][data-day="2026-10-12"]').toHaveClass(
        "o_shift_selected_cell"
    );
    expect(".o_shift_selected_cell").toHaveCount(1);
    expect(".o_shift_selected_employee").toHaveCount(1);
    expect(".o_shift_selected_employee .o_shift_employee_name").toHaveText("Bob");
    expect("thead .o_shift_selected_day").toHaveCount(1);
    expect("thead .o_shift_selected_day").toHaveAttribute("data-day", "2026-10-12");
});

test("month navigation loads the previous, next and current month", async () => {
    mockDate("2026-10-07 10:00:00");
    const requests = [];
    await openGrid(requests);

    await click(".o_shift_next");
    await animationFrame();
    expect(".o_shift_month").toHaveText(/November 2026/i);
    expect(queryAll(".o_shift_day_header")).toHaveLength(30);

    await click(".o_shift_prev");
    await animationFrame();
    await click(".o_shift_prev");
    await animationFrame();
    expect(".o_shift_month").toHaveText(/September 2026/i);

    await click(".o_shift_today_btn");
    await animationFrame();
    expect(".o_shift_month").toHaveText(/October 2026/i);

    expect(requests).toEqual([
        ["2026-10-01", "2026-10-31"],
        ["2026-11-01", "2026-11-30"],
        ["2026-10-01", "2026-10-31"],
        ["2026-09-01", "2026-09-30"],
        ["2026-10-01", "2026-10-31"],
    ]);
});
