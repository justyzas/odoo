import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

/**
 * Float hours -> "HH:MM" (24-hour format).
 */
export function floatToTimeInput(hours) {
    const minutes = Math.round(hours * 60);
    const hh = String(Math.floor(minutes / 60) % 24).padStart(2, "0");
    const mm = String(minutes % 60).padStart(2, "0");
    return `${hh}:${mm}`;
}

/**
 * 24-hour time typed by the user -> float hours, or null if invalid.
 * Accepts "6", "06", "630", "0630", "6:30", "6.30" and "24:00" (midnight).
 */
export function parseTimeInput(text) {
    const value = (text || "").trim().replace(/[.,h ]/g, ":");
    let hh;
    let mm;
    const match = value.match(/^(\d{1,2}):(\d{1,2})$/);
    if (match) {
        hh = parseInt(match[1], 10);
        mm = parseInt(match[2], 10);
    } else if (/^\d{1,4}$/.test(value)) {
        hh = parseInt(value.length <= 2 ? value : value.slice(0, -2), 10);
        mm = value.length <= 2 ? 0 : parseInt(value.slice(-2), 10);
    } else {
        return null;
    }
    if (hh === 24 && mm === 0) {
        hh = 0;
    }
    if (hh > 23 || mm > 59) {
        return null;
    }
    return hh + mm / 60;
}

/**
 * Length of a shift in hours, break included. An end earlier than or equal
 * to the start is on the next day.
 */
export function getSpanHours(hourFrom, hourTo) {
    const span = hourTo - hourFrom;
    return span > 0 ? span : span + 24;
}

/**
 * Popover opened on a grid cell when no brush is selected: lists the shift
 * templates, a "Clear" entry and a "Custom time" form.
 *
 * Calls ``onSelect`` with ``false`` (clear), a template id, or a custom time
 * ``{ templateId, hourFrom, hourTo, breakMinutes }``.
 */
export class ShiftTemplatePicker extends Component {
    static template = "hr_shift_planning.ShiftTemplatePicker";
    static props = {
        templates: Array,
        // Current value of the cell, used to pre-fill the custom time form
        current: { type: Object, optional: true },
        onSelect: Function,
        close: Function,
    };

    setup() {
        const current = this.props.current;
        this.state = useState({
            mode: "list",
            templateId: current?.templateId ? String(current.templateId) : "",
            from: floatToTimeInput(current ? current.hourFrom : 8),
            to: floatToTimeInput(current ? current.hourTo : 16),
            breakMinutes: current ? current.breakMinutes : 0,
            error: "",
        });
    }

    isTemplateSelected(template) {
        return String(template.id) === this.state.templateId;
    }

    select(templateId) {
        this.props.onSelect(templateId);
        this.props.close();
    }

    showCustom() {
        this.state.mode = "custom";
    }

    onTemplateChange(ev) {
        this.state.templateId = ev.target.value;
        const template = this.props.templates.find((t) => String(t.id) === ev.target.value);
        if (template) {
            this.state.from = floatToTimeInput(template.hour_from);
            this.state.to = floatToTimeInput(template.hour_to);
            this.state.breakMinutes = template.break_minutes;
        }
    }

    /**
     * Rewrite what was typed ("630") in the canonical form ("06:30").
     */
    onTimeBlur(fieldName) {
        const hours = parseTimeInput(this.state[fieldName]);
        if (hours !== null) {
            this.state[fieldName] = floatToTimeInput(hours);
        }
    }

    applyCustom() {
        const breakMinutes = parseInt(this.state.breakMinutes, 10) || 0;
        const hourFrom = parseTimeInput(this.state.from);
        const hourTo = parseTimeInput(this.state.to);
        if (hourFrom === null || hourTo === null) {
            this.state.error = _t("Use the 24-hour format HH:MM, e.g. 06:00 or 22:30.");
            return;
        }
        if (breakMinutes < 0 || breakMinutes / 60 >= getSpanHours(hourFrom, hourTo)) {
            this.state.error = _t("The break must be shorter than the shift.");
            return;
        }
        this.select({
            templateId: this.state.templateId ? parseInt(this.state.templateId, 10) : false,
            hourFrom,
            hourTo,
            breakMinutes,
        });
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.applyCustom();
        }
    }
}
