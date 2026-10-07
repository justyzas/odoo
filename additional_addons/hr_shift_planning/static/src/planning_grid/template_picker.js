import { Component } from "@odoo/owl";

/**
 * Popover opened on a grid cell when no brush is selected: lists the shift
 * templates and a "Clear" entry.
 */
export class ShiftTemplatePicker extends Component {
    static template = "hr_shift_planning.ShiftTemplatePicker";
    static props = {
        templates: Array,
        onSelect: Function,
        close: Function,
    };

    select(templateId) {
        this.props.onSelect(templateId);
        this.props.close();
    }
}
