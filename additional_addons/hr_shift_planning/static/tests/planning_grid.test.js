import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { beforeEach, describe, expect, test } from "@odoo/hoot";
import { click, edit, queryAll, select } from "@odoo/hoot-dom";
import { animationFrame, mockDate, mockTimeZone } from "@odoo/hoot-mock";
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

function makePlanningData({ canEdit = true, limits = {}, extraShifts = [] } = {}) {
    return {
        employees: [
            { id: 1, name: "Ann", job_title: "Operator", department: "Production" },
            { id: 2, name: "Bob", job_title: "", department: "Warehouse" },
        ],
        templates: [
            {
                id: 10,
                name: "Morning",
                code: "M",
                color: "#FFE08A",
                hour_from: 6,
                hour_to: 14,
                break_minutes: 30,
                duration: 7.5,
                active: true,
            },
            {
                id: 11,
                name: "Night",
                code: "N",
                color: "#1F3A93",
                hour_from: 22,
                hour_to: 6,
                break_minutes: 30,
                duration: 7.5,
                active: true,
            },
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
            ...extraShifts,
        ],
        holidays: [{ date: "2026-10-20", name: "Test holiday" }],
        can_edit: canEdit,
        limits: { min_rest_hours: 11, max_shift_hours: 12, max_week_hours: 48, ...limits },
    };
}

const ANN_15 = '.o_shift_cell[data-employee-id="1"][data-day="2026-10-15"]';
const BOB_12 = '.o_shift_cell[data-employee-id="2"][data-day="2026-10-12"]';
const BOB_13 = '.o_shift_cell[data-employee-id="2"][data-day="2026-10-13"]';

let loadRequests;
let saveRequests;

async function openGrid(options) {
    onRpc("hr.shift", "get_planning_data", ({ args }) => {
        loadRequests.push(args);
        return makePlanningData(options);
    });
    onRpc("hr.shift", "save_planning_changes", ({ args }) => {
        saveRequests.push(args[0]);
        return true;
    });
    await mountWithCleanup(WebClient);
    await getService("action").doAction(1);
}

beforeEach(() => {
    mockDate("2026-10-07 10:00:00");
    mockTimeZone(+3); // Vilnius summer time: the 03:00 UTC shift starts at 06:00
    loadRequests = [];
    saveRequests = [];
});

describe("display", () => {
    test("renders employees, days of the month and shifts", async () => {
        await openGrid();

        expect(loadRequests).toEqual([["2026-10-01", "2026-10-31"]]);
        expect(".o_shift_month").toHaveText(/October 2026/i);
        expect(".o_shift_employee_name").toHaveCount(2);
        expect(".o_shift_day_header").toHaveCount(31);
        expect(".o_shift_cell").toHaveCount(62);
        expect(".o_shift_chip").toHaveCount(1);
        expect(`${ANN_15} .o_shift_code`).toHaveText("M");
        expect(`${ANN_15} .o_shift_hours`).toHaveText("7.5h");
    });

    test("marks today, weekends and holidays", async () => {
        await openGrid();

        expect('.o_shift_day_header[data-day="2026-10-07"]').toHaveClass("o_shift_today");
        // 2026-10-03 is a Saturday
        expect('.o_shift_day_header[data-day="2026-10-03"]').toHaveClass("o_shift_weekend");
        expect('.o_shift_day_header[data-day="2026-10-20"]').toHaveClass("o_shift_holiday");
        expect('.o_shift_day_header[data-day="2026-10-20"]').toHaveAttribute("title", "Test holiday");
    });

    test("clicking a cell highlights the cell, its employee and its day", async () => {
        await openGrid({ canEdit: false });

        await click(BOB_12);
        await animationFrame();

        expect(BOB_12).toHaveClass("o_shift_selected_cell");
        expect(".o_shift_selected_cell").toHaveCount(1);
        expect(".o_shift_selected_employee .o_shift_employee_name").toHaveText("Bob");
        expect("thead .o_shift_selected_day").toHaveCount(1);
        expect("thead .o_shift_selected_day").toHaveAttribute("data-day", "2026-10-12");
    });

    test("month navigation loads the previous, next and current month", async () => {
        await openGrid();

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

        expect(loadRequests).toEqual([
            ["2026-10-01", "2026-10-31"],
            ["2026-11-01", "2026-11-30"],
            ["2026-10-01", "2026-10-31"],
            ["2026-09-01", "2026-09-30"],
            ["2026-10-01", "2026-10-31"],
        ]);
    });
});

describe("totals and filters", () => {
    test("monthly total per employee follows unsaved changes", async () => {
        await openGrid();

        const annTotal = '.o_shift_cell[data-employee-id="1"] ~ .o_shift_total';
        const bobTotal = '.o_shift_cell[data-employee-id="2"] ~ .o_shift_total';
        expect(annTotal).toHaveText("7.5h");
        expect(bobTotal).toHaveText("0h");

        await click('.o_shift_brush[data-template-id="11"]');
        await animationFrame();
        await click(BOB_12);
        await animationFrame();
        await click(BOB_13);
        await animationFrame();
        expect(bobTotal).toHaveText("15h");
    });

    test("search by name ignores case and accents", async () => {
        await openGrid();

        await click(".o_shift_search");
        await edit("ÁN");
        await animationFrame();
        expect(".o_shift_employee_name").toHaveCount(1);
        expect(".o_shift_employee_name").toHaveText("Ann");

        await edit("");
        await animationFrame();
        expect(".o_shift_employee_name").toHaveCount(2);
    });

    test("department filter", async () => {
        await openGrid();

        expect(".o_shift_department option").toHaveCount(3); // All + 2 departments
        await select("Warehouse", { target: ".o_shift_department" });
        await animationFrame();
        expect(".o_shift_employee_name").toHaveCount(1);
        expect(".o_shift_employee_name").toHaveText("Bob");
    });
});

describe("labour code warnings", () => {
    const cell = (employeeId, date) =>
        `.o_shift_cell[data-employee-id="${employeeId}"][data-day="${date}"]`;

    async function paint(templateId, cells) {
        await click(`.o_shift_brush[data-template-id="${templateId}"]`);
        await animationFrame();
        for (const selector of cells) {
            await click(selector);
            await animationFrame();
        }
    }

    test("no warnings: no warning button", async () => {
        await openGrid();
        expect(".o_shift_warning").toHaveCount(0);
        expect(".o_shift_warnings_btn").toHaveCount(0);
    });

    test("too little rest between a night and a morning shift", async () => {
        await openGrid();

        // Ann: night on the 14th (22:00-06:00), morning on the 15th (06:00)
        await paint(11, [cell(1, "2026-10-14")]);

        expect(cell(1, "2026-10-14")).toHaveClass("o_shift_warning");
        expect(cell(1, "2026-10-15")).toHaveClass("o_shift_warning");
        expect(cell(1, "2026-10-15")).toHaveAttribute("title", /Only 0 h of rest between shifts \(minimum 11 h\)/);
        expect(".o_shift_warnings_btn").toHaveText("1");
        // warnings do not block saving
        expect(".o_shift_save").toBeEnabled();
    });

    test("shift longer than the maximum", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        await click(".o_shift_custom_from");
        await edit("06:00");
        await click(".o_shift_custom_to");
        await edit("20:00");
        await click(".o_shift_custom_apply");
        await animationFrame();

        expect(BOB_12).toHaveClass("o_shift_warning");
        expect(BOB_12).toHaveAttribute("title", /14 h shift \(maximum 12 h\)/);
    });

    test("more than the maximum hours in 7 days", async () => {
        await openGrid();

        // Bob: 7 mornings of 7.5 h in a row = 52.5 h
        const days = ["12", "13", "14", "15", "16", "17", "18"].map((d) => cell(2, `2026-10-${d}`));
        await paint(10, days);

        expect(".o_shift_warning").toHaveCount(1);
        expect(cell(2, "2026-10-18")).toHaveClass("o_shift_warning");
        expect(cell(2, "2026-10-18")).toHaveAttribute("title", /52.5 h in the 7 days up to this day \(maximum 48 h\)/);
    });

    test("rest is checked against the first shift of the next month", async () => {
        await openGrid({
            extraShifts: [
                {
                    id: 101,
                    employee_id: 2,
                    date: "2026-11-01",
                    template_id: 10,
                    start: "2026-11-01 04:00:00", // 07:00 local (+3)
                    end: "2026-11-01 12:00:00",
                    break_minutes: 30,
                    duration: 7.5,
                    is_custom: false,
                },
            ],
        });

        // night on the 31st ends 06:00 on Nov 1st, next shift at 07:00
        await paint(11, [cell(2, "2026-10-31")]);
        expect(cell(2, "2026-10-31")).toHaveClass("o_shift_warning");
    });

    test("limits come from the settings", async () => {
        await openGrid({ limits: { max_shift_hours: 7 } });

        expect(ANN_15).toHaveClass("o_shift_warning");
        expect(ANN_15).toHaveAttribute("title", /7.5 h shift \(maximum 7 h\)/);
    });

    test("warning list selects the related cell", async () => {
        await openGrid();

        await paint(11, [cell(1, "2026-10-14")]);
        await click(".o_shift_warnings_btn");
        await animationFrame();

        expect(".o_shift_warning_item").toHaveCount(1);
        expect(".o_shift_warning_item").toHaveText(/Ann/);
        await click(".o_shift_warning_item");
        await animationFrame();

        expect(".o_shift_warning_list").toHaveCount(0);
        expect(cell(1, "2026-10-15")).toHaveClass("o_shift_selected_cell");
    });
});

describe("editing", () => {
    test("viewer: no brushes, no save button, no popover", async () => {
        await openGrid({ canEdit: false });

        expect(".o_shift_brush").toHaveCount(0);
        expect(".o_shift_save").toHaveCount(0);
        await click(BOB_12);
        await animationFrame();
        expect(".o_shift_template_picker").toHaveCount(0);
        expect(`${BOB_12} .o_shift_chip`).toHaveCount(0);
    });

    test("brush: pick a template, then each click fills a cell", async () => {
        await openGrid();

        expect(".o_shift_brush").toHaveCount(3); // M, N, Clear
        expect(".o_shift_save").not.toBeEnabled();

        await click('.o_shift_brush[data-template-id="11"]');
        await animationFrame();
        expect('.o_shift_brush[data-template-id="11"]').toHaveClass("o_shift_brush_active");

        await click(BOB_12);
        await animationFrame();
        await click(BOB_13);
        await animationFrame();

        expect(`${BOB_12} .o_shift_code`).toHaveText("N");
        expect(`${BOB_13} .o_shift_code`).toHaveText("N");
        expect(BOB_12).toHaveClass("o_shift_dirty");
        expect(".o_shift_template_picker").toHaveCount(0);
        expect(".o_shift_save").toBeEnabled();
        expect(".o_shift_save").toHaveText("Save (2)");
    });

    test("clicking the active brush again deselects it", async () => {
        await openGrid();

        await click('.o_shift_brush[data-template-id="10"]');
        await animationFrame();
        await click('.o_shift_brush[data-template-id="10"]');
        await animationFrame();
        expect(".o_shift_brush_active").toHaveCount(0);
    });

    test("without brush: clicking a cell opens the template picker", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        expect(".o_shift_template_picker").toHaveCount(1);
        expect(".o_shift_picker_template").toHaveCount(2);

        await click('.o_shift_picker_template[data-template-id="10"]');
        await animationFrame();
        expect(".o_shift_template_picker").toHaveCount(0);
        expect(`${BOB_12} .o_shift_code`).toHaveText("M");
        expect(BOB_12).toHaveClass("o_shift_dirty");
    });

    test("clear brush removes a shift; re-applying the original undoes the change", async () => {
        await openGrid();

        await click(".o_shift_brush_clear");
        await animationFrame();
        await click(ANN_15);
        await animationFrame();
        expect(`${ANN_15} .o_shift_chip`).toHaveCount(0);
        expect(ANN_15).toHaveClass("o_shift_dirty");

        await click('.o_shift_brush[data-template-id="10"]');
        await animationFrame();
        await click(ANN_15);
        await animationFrame();
        expect(`${ANN_15} .o_shift_code`).toHaveText("M");
        expect(ANN_15).not.toHaveClass("o_shift_dirty");
        expect(".o_shift_save").not.toBeEnabled();
    });

    test("save sends all changes in one call and reloads", async () => {
        await openGrid();

        await click('.o_shift_brush[data-template-id="11"]');
        await animationFrame();
        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_brush_clear");
        await animationFrame();
        await click(ANN_15);
        await animationFrame();

        await click(".o_shift_save");
        await animationFrame();

        expect(saveRequests).toEqual([
            [
                { employee_id: 2, date: "2026-10-12", template_id: 11 },
                { employee_id: 1, date: "2026-10-15", template_id: false },
            ],
        ]);
        expect(loadRequests).toHaveLength(2);
        expect(".o_shift_dirty").toHaveCount(0);
        expect(".o_shift_save").not.toBeEnabled();
    });

    test("discard drops the pending changes", async () => {
        await openGrid();

        await click('.o_shift_brush[data-template-id="11"]');
        await animationFrame();
        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_discard");
        await animationFrame();

        expect(`${BOB_12} .o_shift_chip`).toHaveCount(0);
        expect(".o_shift_dirty").toHaveCount(0);
        expect(saveRequests).toEqual([]);
    });

    test("custom time without template", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        expect(".o_shift_custom_form").toHaveCount(1);

        // short 24-hour forms are accepted: "6" = 06:00, "1200" = 12:00
        await click(".o_shift_custom_from");
        await edit("6");
        await click(".o_shift_custom_to");
        await edit("1200");
        await animationFrame();
        expect(".o_shift_custom_from").toHaveValue("06:00"); // normalized on blur
        await click(".o_shift_custom_apply");
        await animationFrame();

        expect(".o_shift_template_picker").toHaveCount(0);
        expect(`${BOB_12} .o_shift_code`).toHaveText("06-12");
        expect(`${BOB_12} .o_shift_hours`).toHaveText("6h");
        expect(BOB_12).toHaveClass("o_shift_dirty");

        await click(".o_shift_save");
        await animationFrame();
        expect(saveRequests).toEqual([
            [
                {
                    employee_id: 2,
                    date: "2026-10-12",
                    template_id: false,
                    custom: { hour_from: 6, hour_to: 12, break_minutes: 0 },
                },
            ],
        ]);
    });

    test("custom time is pre-filled from the cell and keeps the template", async () => {
        await openGrid();

        await click(ANN_15);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        expect(".o_shift_custom_template").toHaveValue("10");
        expect(".o_shift_custom_from").toHaveValue("06:00");
        expect(".o_shift_custom_to").toHaveValue("14:00");
        expect(".o_shift_custom_break").toHaveValue(30);

        await click(".o_shift_custom_to");
        await edit("12:00");
        await click(".o_shift_custom_apply");
        await animationFrame();

        expect(`${ANN_15} .o_shift_code`).toHaveText("M*");
        expect(`${ANN_15} .o_shift_hours`).toHaveText("5.5h");

        await click(".o_shift_save");
        await animationFrame();
        expect(saveRequests).toEqual([
            [
                {
                    employee_id: 1,
                    date: "2026-10-15",
                    template_id: 10,
                    custom: { hour_from: 6, hour_to: 12, break_minutes: 30 },
                },
            ],
        ]);
    });

    test("custom time: picking a template fills its time", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        await select("11", { target: ".o_shift_custom_template" });
        await animationFrame();
        expect(".o_shift_custom_from").toHaveValue("22:00");
        expect(".o_shift_custom_to").toHaveValue("06:00");
    });

    test("custom time: an invalid time is refused", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        await click(".o_shift_custom_from");
        await edit("25:00");
        await click(".o_shift_custom_apply");
        await animationFrame();

        expect(".o_shift_custom_error").toHaveCount(1);
        expect(`${BOB_12} .o_shift_chip`).toHaveCount(0);
    });

    test("custom time: a break longer than the shift is refused", async () => {
        await openGrid();

        await click(BOB_12);
        await animationFrame();
        await click(".o_shift_picker_custom");
        await animationFrame();
        await click(".o_shift_custom_from");
        await edit("06:00");
        await click(".o_shift_custom_to");
        await edit("07:00");
        await click(".o_shift_custom_break");
        await edit("90");
        await click(".o_shift_custom_apply");
        await animationFrame();

        expect(".o_shift_custom_error").toHaveCount(1);
        expect(`${BOB_12} .o_shift_chip`).toHaveCount(0);
    });

    test("changing month with unsaved changes asks for confirmation", async () => {
        await openGrid();

        await click('.o_shift_brush[data-template-id="11"]');
        await animationFrame();
        await click(BOB_12);
        await animationFrame();

        // Stay: month and change are kept
        await click(".o_shift_next");
        await animationFrame();
        expect(".modal").toHaveCount(1);
        await click(".modal-footer .btn-secondary");
        await animationFrame();
        expect(".o_shift_month").toHaveText(/October 2026/i);
        expect(BOB_12).toHaveClass("o_shift_dirty");

        // Discard: the next month is loaded without the change
        await click(".o_shift_next");
        await animationFrame();
        await click(".modal-footer .btn-primary");
        await animationFrame();
        expect(".o_shift_month").toHaveText(/November 2026/i);
        expect(".o_shift_dirty").toHaveCount(0);
        expect(saveRequests).toEqual([]);
    });
});
