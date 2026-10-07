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
import { ShiftTemplatePicker } from "./template_picker";

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

function formatFloatHour(hours) {
    const minutes = Math.round(hours * 60);
    const hh = String(Math.floor(minutes / 60) % 24).padStart(2, "0");
    const mm = String(minutes % 60).padStart(2, "0");
    return `${hh}:${mm}`;
}

function cellKey(employeeId, date) {
    return `${employeeId}|${date}`;
}

/**
 * Monthly shift planning grid: employees on the left, the days of the month
 * on top, one cell per employee and day.
 *
 * Editing (planners only): pick a template in the toolbar (the "brush") and
 * click cells, or click a cell without a brush to choose a template in a
 * popover. Changes are kept in the browser until "Save".
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
                days: [],
                shifts: {},
                originals: {},
                templates: {},
                palette: [],
                canEdit: false,
            }),
            selected: null, // { employeeId, date }
            brush: null, // null: no brush, false: clear, number: template id
            pending: {}, // cell key -> template id, or false to remove the shift
            saving: false,
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
        }));
        const templates = {};
        for (const template of data.templates) {
            const color = template.color || DEFAULT_COLOR;
            const timeLabel = `${formatFloatHour(template.hour_from)} - ${formatFloatHour(template.hour_to)}`;
            templates[template.id] = {
                ...template,
                timeLabel,
                style: getColorStyle(color),
                // Cell shown for a template applied in the grid but not saved yet
                cell: {
                    label: template.code,
                    hours: formatHours(template.duration),
                    style: getColorStyle(color),
                    title: [
                        template.name,
                        timeLabel,
                        template.break_minutes ? _t("Break: %s min", template.break_minutes) : "",
                    ]
                        .filter(Boolean)
                        .join("\n"),
                },
            };
        }
        const shifts = {};
        const originals = {};
        for (const shift of data.shifts) {
            const key = cellKey(shift.employee_id, shift.date);
            shifts[key] = this.buildShiftCell(shift, templates[shift.template_id]);
            originals[key] = { templateId: shift.template_id, isCustom: shift.is_custom };
        }
        return {
            employees,
            days,
            shifts,
            originals,
            templates,
            palette: data.templates.filter((t) => t.active).map((t) => templates[t.id]),
            canEdit: data.can_edit,
        };
    }

    buildShiftCell(shift, template) {
        const start = deserializeDateTime(shift.start).toFormat("HH:mm");
        const end = deserializeDateTime(shift.end).toFormat("HH:mm");
        const title = [
            template ? template.name : _t("Custom time"),
            `${start} - ${end}`,
            shift.break_minutes ? _t("Break: %s min", shift.break_minutes) : "",
        ];
        return {
            label: template
                ? template.code + (shift.is_custom ? "*" : "")
                : `${start.slice(0, 2)}-${end.slice(0, 2)}`,
            hours: formatHours(shift.duration),
            style: getColorStyle(template ? template.color || DEFAULT_COLOR : DEFAULT_COLOR),
            title: title.filter(Boolean).join("\n"),
        };
    }

    /**
     * What a cell shows: the saved shift, or the pending change if any.
     */
    getCell(employee, day) {
        const key = cellKey(employee.id, day.key);
        if (key in this.state.pending) {
            const templateId = this.state.pending[key];
            return {
                shift: templateId ? this.state.grid.templates[templateId].cell : null,
                dirty: true,
            };
        }
        return { shift: this.state.grid.shifts[key] || null, dirty: false };
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
     */
    applyChange(employeeId, date, templateId) {
        const key = cellKey(employeeId, date);
        const original = this.state.grid.originals[key];
        const unchanged = templateId
            ? Boolean(original && original.templateId === templateId && !original.isCustom)
            : !original;
        if (unchanged) {
            delete this.state.pending[key];
        } else {
            this.state.pending[key] = templateId;
        }
    }

    selectBrush(value) {
        this.picker.close();
        this.state.brush = this.isBrushActive(value) ? null : value;
    }

    async save() {
        const changes = Object.entries(this.state.pending).map(([key, templateId]) => {
            const [employeeId, date] = key.split("|");
            return { employee_id: parseInt(employeeId), date, template_id: templateId };
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
            this.applyChange(employeeId, date, this.state.brush);
        } else {
            this.picker.open(cell, {
                templates: this.state.grid.palette,
                onSelect: (templateId) => this.applyChange(employeeId, date, templateId),
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
