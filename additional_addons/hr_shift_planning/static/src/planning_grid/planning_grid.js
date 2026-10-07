import { Component, markRaw, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, serializeDate } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const { DateTime } = luxon;

/**
 * Pick black or white text depending on the background brightness.
 */
function getTextColor(hexColor) {
    const hex = (hexColor || "").replace("#", "");
    if (hex.length !== 6) {
        return "#000000";
    }
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#000000" : "#FFFFFF";
}

function formatHours(hours) {
    return `${Math.round(hours * 100) / 100}h`;
}

/**
 * Monthly shift planning grid: employees on the left, the days of the month
 * on top, one cell per employee and day.
 */
export class ShiftPlanningGrid extends Component {
    static template = "hr_shift_planning.ShiftPlanningGrid";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.today = DateTime.local().startOf("day");
        this.state = useState({
            month: this.today.startOf("month"),
            // Grid data is replaced as a whole on each load and never mutated
            // in place, so it is kept out of the reactivity system.
            grid: markRaw({ employees: [], days: [], shifts: {} }),
            selected: null, // { employeeId, date }
        });
        this.loadId = 0;
        this.hoveredDay = null;
        onWillStart(() => this.load());
    }

    get monthLabel() {
        return this.state.month.toFormat("LLLL yyyy");
    }

    async load() {
        const loadId = ++this.loadId;
        const month = this.state.month;
        const data = await this.orm.call("hr.shift", "get_planning_data", [
            serializeDate(month),
            serializeDate(month.endOf("month")),
        ]);
        if (loadId !== this.loadId) {
            return; // a newer month was requested meanwhile
        }
        this.state.grid = markRaw(this.buildGrid(month, data));
    }

    buildGrid(month, data) {
        const holidays = Object.fromEntries(data.holidays.map((h) => [h.date, h.name]));
        const days = [];
        for (let day = month; day.month === month.month; day = day.plus({ days: 1 })) {
            const key = serializeDate(day);
            days.push({
                key,
                day: day.day,
                weekday: day.toFormat("ccc"),
                isWeekend: day.weekday >= 6,
                isToday: day.hasSame(this.today, "day"),
                holiday: holidays[key] || "",
            });
        }
        const employees = data.employees.map((employee) => ({
            ...employee,
            info: employee.job_title || employee.department,
        }));
        const templates = Object.fromEntries(data.templates.map((t) => [t.id, t]));
        const shifts = {};
        for (const shift of data.shifts) {
            shifts[`${shift.employee_id}|${shift.date}`] = this.buildCell(shift, templates);
        }
        return { employees, days, shifts };
    }

    buildCell(shift, templates) {
        const template = templates[shift.template_id];
        const start = deserializeDateTime(shift.start).toFormat("HH:mm");
        const end = deserializeDateTime(shift.end).toFormat("HH:mm");
        const color = template ? template.color || "#D9D9D9" : "#D9D9D9";
        const title = [
            template ? template.name : _t("Custom time"),
            `${start} - ${end}`,
            shift.break_minutes ? _t("Break: %s min", shift.break_minutes) : "",
        ].filter(Boolean);
        return {
            id: shift.id,
            label: template ? template.code + (shift.is_custom ? "*" : "") : `${start.slice(0, 2)}-${end.slice(0, 2)}`,
            hours: formatHours(shift.duration),
            style: `background-color: ${color}; color: ${getTextColor(color)};`,
            title: title.join("\n"),
        };
    }

    getShift(employee, day) {
        return this.state.grid.shifts[`${employee.id}|${day.key}`];
    }

    getDayClass(day) {
        const selected = this.state.selected;
        return {
            o_shift_weekend: day.isWeekend,
            o_shift_holiday: Boolean(day.holiday),
            o_shift_today: day.isToday,
            o_shift_selected_day: Boolean(selected && selected.date === day.key),
        };
    }

    getCellClass(employee, day) {
        const selected = this.state.selected;
        return {
            ...this.getDayClass(day),
            o_shift_selected_cell: Boolean(
                selected && selected.date === day.key && selected.employeeId === employee.id
            ),
        };
    }

    isEmployeeSelected(employee) {
        return this.state.selected?.employeeId === employee.id;
    }

    // ------------------------------------------------------------------
    // Handlers
    // ------------------------------------------------------------------

    async changeMonth(delta) {
        this.state.month = this.state.month.plus({ months: delta });
        this.state.selected = null;
        await this.load();
    }

    async goToday() {
        this.state.month = this.today.startOf("month");
        this.state.selected = null;
        await this.load();
    }

    onGridClick(ev) {
        const cell = ev.target.closest("td[data-day]");
        if (!cell) {
            return;
        }
        this.state.selected = {
            employeeId: parseInt(cell.dataset.employeeId),
            date: cell.dataset.day,
        };
    }

    /**
     * Column hover is done on the DOM directly: going through the component
     * state would re-render the whole grid on every mouse move.
     */
    onGridMouseOver(ev) {
        const cell = ev.target.closest("[data-day]");
        const day = cell ? cell.dataset.day : null;
        if (day !== this.hoveredDay) {
            this.setColumnHover(ev.currentTarget, day);
        }
    }

    onGridMouseLeave(ev) {
        this.setColumnHover(ev.currentTarget, null);
    }

    setColumnHover(table, day) {
        if (this.hoveredDay) {
            for (const el of table.querySelectorAll(`[data-day="${this.hoveredDay}"]`)) {
                el.classList.remove("o_shift_col_hover");
            }
        }
        this.hoveredDay = day;
        if (day) {
            for (const el of table.querySelectorAll(`[data-day="${day}"]`)) {
                el.classList.add("o_shift_col_hover");
            }
        }
    }
}

registry.category("actions").add("hr_shift_planning.planning_grid", ShiftPlanningGrid);
