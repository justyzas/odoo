import {
    Component,
    markRaw,
    onPatched,
    onWillStart,
    useExternalListener,
    useState,
} from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import {
    deserializeDate,
    deserializeDateTime,
    formatDate,
    serializeDate,
} from "@web/core/l10n/dates";
import { usePopover } from "@web/core/popover/popover_hook";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { floatToTimeInput, getSpanHours, ShiftTemplatePicker } from "./template_picker";
import { ShiftWarningList } from "./warning_list";

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

function roundHours(hours) {
    return Math.round(hours * 100) / 100;
}

function formatHours(hours) {
    return `${roundHours(hours)}h`;
}

function toMinutes(hours) {
    return Math.round(hours * 60);
}

function cellKey(employeeId, date) {
    return `${employeeId}|${date}`;
}

function buildTemplate(template) {
    return {
        ...template,
        timeLabel: `${floatToTimeInput(template.hour_from)} - ${floatToTimeInput(template.hour_to)}`,
        style: getColorStyle(template.color || DEFAULT_COLOR),
    };
}

/**
 * Shift as returned by the server -> cell value (local hours).
 */
function shiftToValue(shift) {
    const start = deserializeDateTime(shift.start);
    const end = deserializeDateTime(shift.end);
    return {
        templateId: shift.template_id,
        hourFrom: start.hour + start.minute / 60,
        hourTo: end.hour + end.minute / 60,
        breakMinutes: shift.break_minutes,
    };
}

function isEditableTarget(target) {
    return (
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable
    );
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
    static components = { Dropdown, DropdownItem, Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.picker = usePopover(ShiftTemplatePicker, { position: "bottom" });
        this.warningPopover = usePopover(ShiftWarningList, { position: "bottom" });
        this.today = DateTime.local().startOf("day");
        this.state = useState({
            month: this.today.startOf("month"),
            // Grid data is replaced as a whole on each load and never mutated
            // in place, so it is kept out of the reactivity system.
            grid: markRaw({
                employees: [],
                departments: [],
                days: [],
                checkDays: [],
                originals: {},
                templates: {},
                palette: [],
                canEdit: false,
                limits: {},
            }),
            selected: null, // { employeeId, date }
            brush: null, // null: no brush, false: clear, number: template id
            pending: {}, // cell key -> cell value (null removes the shift)
            saving: false,
            search: "",
            department: "",
            drag: null, // { start, end }: { employeeId, date } corners of the dragged rectangle
        });
        this.loadId = 0;
        this.hoveredDay = null;
        this.scrollToSelected = null; // scrollIntoView options, set to scroll after the next patch
        onPatched(() => {
            if (this.scrollToSelected) {
                const options = this.scrollToSelected;
                this.scrollToSelected = null;
                document
                    .querySelector(".o_shift_planning .o_shift_selected_cell")
                    ?.scrollIntoView(options);
            }
        });
        useExternalListener(window, "mouseup", () => this.onWindowMouseUp());
        useExternalListener(window, "keydown", (ev) => this.onWindowKeydown(ev));

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
        // Days looked at by the labour code checks: 6 days before the month
        // (7-day window) and the day after (rest before the next shift).
        // The server also sends the 7th day before, for "copy previous week".
        const checkDays = [];
        const lastCheckDay = month.endOf("month").plus({ days: 1 }).startOf("day");
        for (let day = month.minus({ days: 6 }); day <= lastCheckDay; day = day.plus({ days: 1 })) {
            checkDays.push({
                key: serializeDate(day),
                // minutes since epoch at local midnight, ignoring DST shifts
                minutes: Date.UTC(day.year, day.month - 1, day.day) / 60000,
                inMonth: day.month === month.month,
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
            templates[template.id] = buildTemplate(template);
        }
        const originals = {};
        for (const shift of data.shifts) {
            originals[cellKey(shift.employee_id, shift.date)] = shiftToValue(shift);
        }
        return {
            employees,
            departments,
            days,
            checkDays,
            originals,
            templates,
            palette: data.templates.filter((t) => t.active).map((t) => templates[t.id]),
            canEdit: data.can_edit,
            limits: data.limits || {},
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
     * What a cell shows: the saved shift, or the pending change if any, and
     * its labour code warnings.
     *
     * @param {Object} employee
     * @param {Object} day
     * @param {Object} warnings result of ``computeWarnings``
     */
    getCell(employee, day, warnings) {
        const key = cellKey(employee.id, day.key);
        const dirty = key in this.state.pending;
        const value = this.getValue(employee.id, day.key);
        const cellWarnings = warnings.cells[key] || [];
        if (!value) {
            return { shift: null, dirty, warnings: cellWarnings, title: cellWarnings.join("\n") };
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
        for (const warning of cellWarnings) {
            title.push(`⚠ ${warning}`);
        }
        return {
            shift: {
                label: template
                    ? template.code + (custom ? "*" : "")
                    : `${from.slice(0, 2)}-${to.slice(0, 2)}`,
                hours: formatHours(this.getDuration(value)),
                style: template ? template.style : getColorStyle(DEFAULT_COLOR),
            },
            dirty,
            warnings: cellWarnings,
            title: title.filter(Boolean).join("\n"),
        };
    }

    // ------------------------------------------------------------------
    // Labour code warnings
    // ------------------------------------------------------------------

    /**
     * Check the plan (saved shifts and pending changes) against the labour
     * code limits. Warnings never block saving.
     *
     * @returns {{ cells: Object<string, string[]>, issues: Object[] }}
     *  ``cells``: warning messages per cell key; ``issues``: one entry per
     *  problem, for the warning list.
     */
    computeWarnings() {
        const { employees, checkDays, limits } = this.state.grid;
        const cells = {};
        const issues = [];
        const add = (employee, date, message, keys) => {
            issues.push({
                employeeId: employee.id,
                employeeName: employee.name,
                date,
                dateLabel: formatDate(deserializeDate(date)),
                message,
            });
            for (const key of keys) {
                (cells[key] ||= []).push(message);
            }
        };
        const maxShiftMinutes = toMinutes(limits.max_shift_hours ?? 12);
        const minRestMinutes = toMinutes(limits.min_rest_hours ?? 11);
        const maxWeekMinutes = toMinutes(limits.max_week_hours ?? 48);

        for (const employee of employees) {
            const shifts = [];
            const workedByDay = [];
            for (const day of checkDays) {
                const value = this.getValue(employee.id, day.key);
                const worked = value ? toMinutes(this.getDuration(value)) : 0;
                workedByDay.push(worked);
                if (value) {
                    const start = day.minutes + toMinutes(value.hourFrom);
                    shifts.push({
                        date: day.key,
                        key: cellKey(employee.id, day.key),
                        inMonth: day.inMonth,
                        start,
                        end: start + toMinutes(getSpanHours(value.hourFrom, value.hourTo)),
                        worked,
                    });
                }
            }

            for (const shift of shifts) {
                if (shift.inMonth && shift.worked > maxShiftMinutes) {
                    add(
                        employee,
                        shift.date,
                        _t("%(hours)s h shift (maximum %(max)s h)", {
                            hours: roundHours(shift.worked / 60),
                            max: roundHours(maxShiftMinutes / 60),
                        }),
                        [shift.key]
                    );
                }
            }

            for (let i = 1; i < shifts.length; i++) {
                const previous = shifts[i - 1];
                const next = shifts[i];
                const rest = next.start - previous.end;
                if (rest < minRestMinutes && (previous.inMonth || next.inMonth)) {
                    add(
                        employee,
                        next.inMonth ? next.date : previous.date,
                        _t("Only %(rest)s h of rest between shifts (minimum %(min)s h)", {
                            rest: roundHours(Math.max(rest, 0) / 60),
                            min: roundHours(minRestMinutes / 60),
                        }),
                        [previous.key, next.key]
                    );
                }
            }

            // Rolling 7-day windows ending on each day of the month
            checkDays.forEach((day, index) => {
                if (!day.inMonth || !workedByDay[index] || index < 6) {
                    return;
                }
                const total = workedByDay.slice(index - 6, index + 1).reduce((a, b) => a + b, 0);
                if (total > maxWeekMinutes) {
                    add(
                        employee,
                        day.key,
                        _t("%(hours)s h in the 7 days up to this day (maximum %(max)s h)", {
                            hours: roundHours(total / 60),
                            max: roundHours(maxWeekMinutes / 60),
                        }),
                        [cellKey(employee.id, day.key)]
                    );
                }
            });
        }
        issues.sort(
            (a, b) => a.date.localeCompare(b.date) || a.employeeName.localeCompare(b.employeeName)
        );
        return { cells, issues };
    }

    openWarnings(ev) {
        this.picker.close();
        this.warningPopover.open(ev.currentTarget, {
            issues: this.computeWarnings().issues,
            onSelect: (issue) => this.focusCell(issue.employeeId, issue.date),
        });
    }

    /**
     * Select a cell and scroll to it, showing its employee if filtered out.
     */
    focusCell(employeeId, date) {
        if (!this.visibleEmployees.some((employee) => employee.id === employeeId)) {
            this.state.search = "";
            this.state.department = "";
        }
        this.state.selected = { employeeId, date };
        this.scrollToSelected = { block: "center", inline: "center" };
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

    /**
     * @param {Object} employee
     * @param {Object} day
     * @param {Object} cell result of ``getCell``
     * @param {Object|null} dragRange result of ``getDragRange``
     * @param {number} rowIndex index of the employee among the visible ones
     * @param {number} colIndex index of the day in the month
     */
    getCellClass(employee, day, cell, dragRange, rowIndex, colIndex) {
        const selected = this.state.selected;
        return {
            ...this.getDayClass(day),
            o_shift_selected_cell: Boolean(
                selected && selected.date === day.key && selected.employeeId === employee.id
            ),
            o_shift_dirty: cell.dirty,
            o_shift_warning: cell.warnings.length > 0,
            o_shift_editable: this.state.grid.canEdit,
            o_shift_drag_selected: Boolean(
                dragRange &&
                    rowIndex >= dragRange.rowFrom &&
                    rowIndex <= dragRange.rowTo &&
                    colIndex >= dragRange.colFrom &&
                    colIndex <= dragRange.colTo
            ),
        };
    }

    /**
     * Shift count per template and day, for the summary rows under the grid.
     *
     * @param {Object[]} employees the visible employees
     * @returns {Object[]} rows ``{ key, label, title, style, counts }`` where
     *  ``counts`` maps a day key to a number of employees
     */
    computeDayCounts(employees) {
        const { days, palette, templates } = this.state.grid;
        const rows = new Map(
            palette.map((t) => [
                t.id,
                { key: t.id, label: t.code, title: t.name, style: t.style, counts: {} },
            ])
        );
        const other = {
            key: "other",
            label: _t("Other"),
            title: _t("Custom time without template"),
            style: "",
            counts: {},
        };
        for (const employee of employees) {
            for (const day of days) {
                const value = this.getValue(employee.id, day.key);
                if (!value) {
                    continue;
                }
                let row = rows.get(value.templateId);
                if (!row && templates[value.templateId]) {
                    // archived template still used in this month
                    const t = templates[value.templateId];
                    row = { key: t.id, label: t.code, title: t.name, style: t.style, counts: {} };
                    rows.set(t.id, row);
                }
                row ||= other;
                row.counts[day.key] = (row.counts[day.key] || 0) + 1;
            }
        }
        const result = [...rows.values()];
        if (Object.keys(other.counts).length) {
            result.push(other);
        }
        return result;
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
     * @returns {Promise<boolean>} whether the user confirmed
     */
    askConfirmation({ title, body, confirmLabel, cancelLabel }) {
        return new Promise((resolve) => {
            this.dialog.add(
                ConfirmationDialog,
                {
                    title,
                    body,
                    confirmLabel,
                    confirm: () => resolve(true),
                    cancelLabel,
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
    }

    /**
     * @returns {Promise<boolean>} whether it is fine to drop the pending changes
     */
    confirmDiscard() {
        if (!this.hasChanges) {
            return Promise.resolve(true);
        }
        return this.askConfirmation({
            title: _t("Unsaved changes"),
            body: _t("You have unsaved changes in the planning. Discard them?"),
            confirmLabel: _t("Discard"),
            cancelLabel: _t("Stay"),
        });
    }

    // ------------------------------------------------------------------
    // Copy
    // ------------------------------------------------------------------

    /**
     * Copy the week before into the week of the selected cell (the part of
     * it in the current month), for the visible employees. Empty days are
     * copied too. The result is a pending change, saved with "Save".
     */
    async copyPreviousWeek() {
        const selected = this.state.selected;
        if (!selected) {
            this.notification.add(_t("Select a day of the week to fill first."), {
                type: "warning",
            });
            return;
        }
        const monthDays = new Set(this.state.grid.days.map((d) => d.key));
        const monday = deserializeDate(selected.date).startOf("week");
        const targets = [...Array(7).keys()]
            .map((i) => monday.plus({ days: i }))
            .filter((day) => monthDays.has(serializeDate(day)));
        const employees = this.visibleEmployees;
        const confirmed = await this.askConfirmation({
            title: _t("Copy previous week"),
            body: _t(
                "Copy the shifts of the week before to %(from)s - %(to)s for %(count)s employees? Shifts already planned on these days are replaced. Nothing is saved until you click Save.",
                {
                    from: formatDate(targets[0]),
                    to: formatDate(targets[targets.length - 1]),
                    count: employees.length,
                }
            ),
            confirmLabel: _t("Copy"),
            cancelLabel: _t("Cancel"),
        });
        if (!confirmed) {
            return;
        }
        for (const employee of employees) {
            for (const target of targets) {
                const source = this.getValue(
                    employee.id,
                    serializeDate(target.minus({ days: 7 }))
                );
                this.applyChange(employee.id, serializeDate(target), source && { ...source });
            }
        }
    }

    /**
     * Copy the previous month day by day (1st to 1st, ...) for the visible
     * employees. Days missing in the previous month (e.g. the 31st) are not
     * changed. The result is a pending change, saved with "Save".
     */
    async copyPreviousMonth() {
        const month = this.state.month;
        const previous = month.minus({ months: 1 });
        const employees = this.visibleEmployees;
        const confirmed = await this.askConfirmation({
            title: _t("Copy previous month"),
            body: _t(
                "Copy the shifts of %(source)s to %(target)s for %(count)s employees? Shifts already planned this month are replaced. Nothing is saved until you click Save.",
                {
                    source: previous.toFormat("LLLL yyyy"),
                    target: this.monthLabel,
                    count: employees.length,
                }
            ),
            confirmLabel: _t("Copy"),
            cancelLabel: _t("Cancel"),
        });
        if (!confirmed) {
            return;
        }
        const data = await this.orm.call("hr.shift", "get_planning_data", [
            serializeDate(previous),
            serializeDate(previous.endOf("month")),
        ]);
        if (!this.state.month.equals(month)) {
            return; // the month was changed meanwhile
        }
        const templates = this.state.grid.templates;
        for (const template of data.templates) {
            templates[template.id] ||= buildTemplate(template);
        }
        const source = {};
        for (const shift of data.shifts) {
            const date = deserializeDate(shift.date);
            if (date.hasSame(previous, "month")) {
                source[cellKey(shift.employee_id, date.day)] = shiftToValue(shift);
            }
        }
        for (const employee of employees) {
            for (const day of this.state.grid.days) {
                if (day.day > previous.daysInMonth) {
                    continue;
                }
                const value = source[cellKey(employee.id, day.day)];
                this.applyChange(employee.id, day.key, value || null);
            }
        }
    }

    // ------------------------------------------------------------------
    // Drag selection and keyboard
    // ------------------------------------------------------------------

    /**
     * Rectangle being dragged, as index ranges over the visible employees
     * and the days of the month, or null.
     */
    getDragRange(employees) {
        const drag = this.state.drag;
        if (!drag) {
            return null;
        }
        const rows = employees.map((e) => e.id);
        const cols = this.state.grid.days.map((d) => d.key);
        const r1 = rows.indexOf(drag.start.employeeId);
        const r2 = rows.indexOf(drag.end.employeeId);
        const c1 = cols.indexOf(drag.start.date);
        const c2 = cols.indexOf(drag.end.date);
        if ([r1, r2, c1, c2].includes(-1)) {
            return null;
        }
        return {
            rowFrom: Math.min(r1, r2),
            rowTo: Math.max(r1, r2),
            colFrom: Math.min(c1, c2),
            colTo: Math.max(c1, c2),
        };
    }

    onGridMouseDown(ev) {
        if (ev.button !== 0 || !this.state.grid.canEdit || this.state.brush === null) {
            return;
        }
        const cell = ev.target.closest("td.o_shift_cell");
        if (!cell) {
            return;
        }
        ev.preventDefault(); // no text selection while dragging
        const point = { employeeId: parseInt(cell.dataset.employeeId), date: cell.dataset.day };
        this.state.drag = { start: point, end: point };
    }

    /**
     * End of a brush click or drag: apply the brush to the dragged rectangle
     * (a single cell for a simple click).
     */
    onWindowMouseUp() {
        const drag = this.state.drag;
        if (!drag) {
            return;
        }
        const employees = this.visibleEmployees;
        const range = this.getDragRange(employees);
        this.state.drag = null;
        if (!range) {
            return;
        }
        const days = this.state.grid.days;
        for (let row = range.rowFrom; row <= range.rowTo; row++) {
            for (let col = range.colFrom; col <= range.colTo; col++) {
                this.applySelection(employees[row].id, days[col].key, this.state.brush);
            }
        }
    }

    moveSelection(rowDelta, colDelta) {
        const employees = this.visibleEmployees;
        const days = this.state.grid.days;
        const selected = this.state.selected;
        if (!employees.length || !selected) {
            return;
        }
        const clamp = (value, max) => Math.max(0, Math.min(value, max));
        const row = Math.max(employees.findIndex((e) => e.id === selected.employeeId), 0);
        const col = Math.max(days.findIndex((d) => d.key === selected.date), 0);
        this.state.selected = {
            employeeId: employees[clamp(row + rowDelta, employees.length - 1)].id,
            date: days[clamp(col + colDelta, days.length - 1)].key,
        };
        this.scrollToSelected = { block: "nearest", inline: "nearest" };
    }

    /**
     * Template whose code starts with the typed letter. Typing the same
     * letter again on a cell cycles through the templates starting with it.
     */
    getTemplateForLetter(letter, currentValue) {
        const matches = this.state.grid.palette.filter((t) =>
            t.code.toUpperCase().startsWith(letter.toUpperCase())
        );
        if (!matches.length) {
            return null;
        }
        const index = matches.findIndex(
            (t) =>
                currentValue && t.id === currentValue.templateId && !this.isCustom(currentValue)
        );
        return matches[(index + 1) % matches.length];
    }

    /**
     * Keyboard on the selected cell: arrows move; for planners, a letter
     * applies the template whose code starts with it, Delete / Backspace
     * clears, Enter opens the picker. After a letter or Delete the selection
     * moves to the next day, so a week can be typed in one go ("RRRRR").
     */
    onWindowKeydown(ev) {
        const selected = this.state.selected;
        if (
            !selected ||
            ev.ctrlKey ||
            ev.metaKey ||
            ev.altKey ||
            isEditableTarget(ev.target) ||
            this.picker.isOpen ||
            this.warningPopover.isOpen ||
            document.querySelector(".modal")
        ) {
            return;
        }
        const moves = {
            ArrowUp: [-1, 0],
            ArrowDown: [1, 0],
            ArrowLeft: [0, -1],
            ArrowRight: [0, 1],
        };
        if (ev.key in moves) {
            ev.preventDefault();
            this.moveSelection(...moves[ev.key]);
            return;
        }
        if (!this.state.grid.canEdit) {
            return;
        }
        const { employeeId, date } = selected;
        if (ev.key === "Delete" || ev.key === "Backspace") {
            ev.preventDefault();
            this.applySelection(employeeId, date, false);
            this.moveSelection(0, 1);
        } else if (ev.key === "Enter") {
            ev.preventDefault();
            const cell = document.querySelector(
                `.o_shift_planning .o_shift_cell[data-employee-id="${employeeId}"][data-day="${date}"]`
            );
            if (cell) {
                this.openPicker(cell, employeeId, date);
            }
        } else if (ev.key.length === 1 && /\p{L}/u.test(ev.key)) {
            const template = this.getTemplateForLetter(ev.key, this.getValue(employeeId, date));
            if (template) {
                ev.preventDefault();
                this.applySelection(employeeId, date, template.id);
                this.moveSelection(0, 1);
            }
        }
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
        const cell = ev.target.closest("td.o_shift_cell");
        if (!cell) {
            return;
        }
        const employeeId = parseInt(cell.dataset.employeeId);
        const date = cell.dataset.day;
        this.state.selected = { employeeId, date };
        // With a brush, the cell was already painted on mouseup
        if (this.state.grid.canEdit && this.state.brush === null) {
            this.openPicker(cell, employeeId, date);
        }
    }

    openPicker(cell, employeeId, date) {
        this.picker.open(cell, {
            templates: this.state.grid.palette,
            current: this.getValue(employeeId, date) || undefined,
            onSelect: (selection) => this.applySelection(employeeId, date, selection),
        });
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
        const drag = this.state.drag;
        if (drag && cell && cell.dataset.employeeId) {
            const employeeId = parseInt(cell.dataset.employeeId);
            if (employeeId !== drag.end.employeeId || day !== drag.end.date) {
                drag.end = { employeeId, date: day };
            }
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
