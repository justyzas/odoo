import { Component } from "@odoo/owl";

/**
 * Popover listing the labour code warnings of the planning grid. Clicking a
 * warning selects the related cell.
 */
export class ShiftWarningList extends Component {
    static template = "hr_shift_planning.ShiftWarningList";
    static props = {
        issues: Array,
        onSelect: Function,
        close: Function,
    };

    select(issue) {
        this.props.onSelect(issue);
        this.props.close();
    }
}
