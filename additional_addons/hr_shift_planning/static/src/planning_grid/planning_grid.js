import { Component, markRaw, onWillStart, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, serializeDate } from "@web/core/l10n/dates";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { floatToTimeInput, getSpanHours, ShiftTemplatePicker } from "./template_picker";

const { DateTime } = luxon;

const DEFAULT_COLOR = "#D9D9D9";

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

function getColorStyle(color) {
    return `background-color: ${color}; color: ${getTextColor(color)};`;
}

function formatHours(hours) {
    return `${Math.round(hours * 100) / 100}h`;
}

function toMinutes(hours) {
    return Math.round(hours * 60);
}

function cellKey(employeeId, date) {
    return `${employeeId}|${date}`;
}

/**
 * Lowercase, without accents: "Čiurlionis" matches "ciur".
 */
function normalizeText(text) {
    return (text || "")
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .toLowerCase();
}

/**
 * Monthly shift planning grid: employees on the left, the days of the month
 * on top, one cell per employee and day.
 *
 * Editing (planners only): pick a template in the toolbar (the "brush") and
 * click cells, or click a cell without a brush to choose a template or a
 * custom time in a popover. Changes are kept in the browser until "Save".
 *
 * A cell value is ``null`` (no shift) or
 * ``{ templateId, hourFrom, hourTo, breakMinutes }`` (local hours).
 */
export class ShiftPlanningGrid extends Component {
    static template = "hr_shift_planning.ShiftPlanningGrid";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.picker = usePopover(ShiftTemplatePicker, { position: "bottom" });
        this.today = DateTime.local().startOf("day");
        this.state = useState({
            month: this.today.startOf("month"),
            // Grid data is replaced as a whole on each load and never mutated
            // in place, so it is kept out of the reactivity system.
            grid: markRaw({
                employees: [],
                departments: [],
                days: [],
                originals: {},
                templates: {},
                palette: [],
                canEdit: false,
            }),
            selected: null, // { employeeId, date }
            brush: null, // null: no brush, false: clear, number: template id
            pending: {}, // cell key -> cell value (null removes the shift)
            saving: false,
            search: "",
            department: "",
        });
        this.loadId = 0;
        this.hoveredDay = null;

        useSetupAction({
            beforeLeave: ({ forceLeave } = {}) => forceLeave || this.confirmLeave(),
            beforeUnload: (ev) => {
                if (this.hasChanges) {
                    ev.preventDefault();
                    ev.returnValue = "";
                }
            },
        });
        onWillStart(() => this.load());
    }

    get monthLabel() {
        return this.state.month.toFormat("LLLL yyyy");
    }

    get hasChanges() {
        return Object.keys(this.state.pending).length > 0;
    }

    get changeCount() {
        return Object.keys(this.state.pending).length;
    }

    get visibleEmployees() {
        const search = normalizeText(this.state.search.trim());
        const department = this.state.department;
        return this.state.grid.employees.filter(
            (employee) =>
                (!search || employee.searchText.includes(search)) &&
                (!department || employee.department === department)
        );
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------

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
            searchText: normalizeText(employee.name),
        }));
        const departments = [...new Set(employees.map((e) => e.department).filter(Boolean))].sort();
        const templates = {};
        for (const template of data.templates) {
            templates[template.id] = {
                ...template,
                timeLabel: `${floatToTimeInput(template.hour_from)} - ${floatToTimeInput(template.hour_to)}`,
                style: getColorStyle(template.color || DEFAULT_COLOR),
            };
        }
        const originals = {};
        for (const shift of data.shifts) {
            const start = deserializeDateTime(shift.start);
            const end = deserializeDateTime(shift.end);
            originals[cellKey(shift.employee_id, shift.date)] = {
                templateId: shift.template_id,
                hourFrom: start.hour + start.minute / 60,
                hourTo: end.hour + end.minute / 60,
                breakMinutes: shift.break_minutes,
            };
        }
        return {
            employees,
            departments,
            days,
            originals,
            templates,
            palette: data.templates.filter((t) => t.active).map((t) => templates[t.id]),
            canEdit: data.can_edit,
        };
    }

    templateValue(templateId) {
        const template = this.state.grid.templates[templateId];
        return {
            templateId,
            hourFrom: template.hour_from,
            hourTo: template.hour_to,
            breakMinutes: template.break_minutes,
        };
    }

    /**
     * Whether a value has another time than its template (or no template).
     */
    isCustom(value) {
        const template = this.state.grid.templates[value.templateId];
        return (
            !template ||
            toMinutes(template.hour_from) !== toMinutes(value.hourFrom) ||
            toMinutes(template.hour_to) !== toMinutes(value.hourTo) ||
            template.break_minutes !== value.breakMinutes
        );
    }

    sameValue(a, b) {
        if (!a || !b) {
            return !a && !b;
        }
        return (
            (a.templateId || false) === (b.templateId || false) &&
            toMinutes(a.hourFrom) === toMinutes(b.hourFrom) &&
            toMinutes(a.hourTo) === toMinutes(b.hourTo) &&
            a.breakMinutes === b.breakMinutes
        );
    }

    getValue(employeeId, date) {
        const key = cellKey(employeeId, date);
        if (key in this.state.pending) {
            return this.state.pending[key];
        }
        return this.state.grid.originals[key] || null;
    }

    getDuration(value) {
        return Math.max(getSpanHours(value.hourFrom, value.hourTo) - value.breakMinutes / 60, 0);
    }

    /**
     * What a cell shows: the saved shift, or the pending change if any.
     */
    getCell(employee, day) {
        const key = cellKey(employee.id, day.key);
        const dirty = key in this.state.pending;
        const value = this.getValue(employee.id, day.key);
        if (!value) {
            return { shift: null, dirty };
        }
        const template = this.state.grid.templates[value.templateId];
        const from = floatToTimeInput(value.hourFrom);
        const to = floatToTimeInput(value.hourTo);
        const custom = this.isCustom(value);
        const title = [
            template ? template.name : _t("Custom time"),
            `${from} - ${to}`,
            value.breakMinutes ? _t("Break: %s min", value.breakMinutes) : "",
        ];
        return {
            shift: {
                label: template
                    ? template.code + (custom ? "*" : "")
                    : `${from.slice(0, 2)}-${to.slice(0, 2)}`,
                hours: formatHours(this.getDuration(value)),
                style: template ? template.style : getColorStyle(DEFAULT_COLOR),
                title: title.filter(Boolean).join("\n"),
            },
            dirty,
        };
    }

    getEmployeeTotal(employee) {
        let total = 0;
        for (const day of this.state.grid.days) {
            const value = this.getValue(employee.id, day.key);
            if (value) {
                total += this.getDuration(value);
            }
        }
        return formatHours(total);
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

    getCellClass(employee, day, cell) {
        const selected = this.state.selected;
        return {
            ...this.getDayClass(day),
            o_shift_selected_cell: Boolean(
                selected && selected.date === day.key && selected.employeeId === employee.id
            ),
            o_shift_dirty: cell.dirty,
            o_shift_editable: this.state.grid.canEdit,
        };
    }

    isEmployeeSelected(employee) {
        return this.state.selected?.employeeId === employee.id;
    }

    isBrushActive(value) {
        return this.state.brush === value;
    }

    // ------------------------------------------------------------------
    // Editing
    // ------------------------------------------------------------------

    /**
     * Record a change for a cell. A change that brings the cell back to its
     * saved state is dropped instead of being kept as pending.
     *
     * @param {number} employeeId
     * @param {string} date
     * @param {Object|null} value
     */
    applyChange(employeeId, date, value) {
        const key = cellKey(employeeId, date);
        if (this.sameValue(value, this.state.grid.originals[key])) {
            delete this.state.pending[key];
        } else {
            this.state.pending[key] = value;
        }
    }

    /**
     * Apply what the brush or the picker gives: false (clear), a template id,
     * or a custom time value.
     */
    applySelection(employeeId, date, selection) {
        let value = null;
        if (typeof selection === "number") {
            value = this.templateValue(selection);
        } else if (selection) {
            value = selection;
        }
        this.applyChange(employeeId, date, value);
    }

    selectBrush(value) {
        this.picker.close();
        this.state.brush = this.isBrushActive(value) ? null : value;
    }

    async save() {
        const changes = Object.entries(this.state.pending).map(([key, value]) => {
            const [employeeId, date] = key.split("|");
            const change = {
                employee_id: parseInt(employeeId),
                date,
                template_id: value ? value.templateId || false : false,
            };
            if (value && this.isCustom(value)) {
                change.custom = {
                    hour_from: value.hourFrom,
                    hour_to: value.hourTo,
                    break_minutes: value.breakMinutes,
                };
            }
            return change;
        });
        if (!changes.length) {
            return;
        }
        this.state.saving = true;
        try {
            await this.orm.call("hr.shift", "save_planning_changes", [changes]);
        } finally {
            this.state.saving = false;
        }
        this.state.pending = {};
        await this.load();
    }

    discard() {
        this.state.pending = {};
    }

    /**
     * @returns {Promise<boolean>} whether it is fine to drop the pending changes
     */
    confirmDiscard() {
        if (!this.hasChanges) {
            return Promise.resolve(true);
        }
        return new Promise((resolve) => {
            this.dialog.add(
                ConfirmationDialog,
                {
                    title: _t("Unsaved changes"),
                    body: _t("You have unsaved changes in the planning. Discard them?"),
                    confirmLabel: _t("Discard"),
                    confirm: () => resolve(true),
                    cancelLabel: _t("Stay"),
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
    }

    async confirmLeave() {
        const ok = await this.confirmDiscard();
        if (ok) {
            this.discard();
        }
        return ok;
    }

    // ------------------------------------------------------------------
    // Handlers
    // ------------------------------------------------------------------

    async changeMonth(delta) {
        await this.goToMonth(this.state.month.plus({ months: delta }));
    }

    async goToday() {
        await this.goToMonth(this.today.startOf("month"));
    }

    async goToMonth(month) {
        if (!(await this.confirmLeave())) {
            return;
        }
        this.picker.close();
        this.state.month = month;
        this.state.selected = null;
        await this.load();
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value;
    }

    onDepartmentChange(ev) {
        this.state.department = ev.target.value;
    }

    onGridClick(ev) {
        const cell = ev.target.closest("td[data-day]");
        if (!cell) {
            return;
        }
        const employeeId = parseInt(cell.dataset.employeeId);
        const date = cell.dataset.day;
        this.state.selected = { employeeId, date };
        if (!this.state.grid.canEdit) {
            return;
        }
        if (this.state.brush !== null) {
            this.applySelection(employeeId, date, this.state.brush);
        } else {
            this.picker.open(cell, {
                templates: this.state.grid.palette,
                current: this.getValue(employeeId, date) || undefined,
                onSelect: (selection) => this.applySelection(employeeId, date, selection),
            });
        }
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
