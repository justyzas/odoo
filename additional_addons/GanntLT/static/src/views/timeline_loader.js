/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";

const actionRegistry = registry.category("actions");

function registerTimelineActionLazyLoader(actionTag) {
    const actionLazyLoader = async (env, action) => {
        const bundleName =
            cookie.get("color_scheme") === "dark"
                ? "web.assets_backend_lazy_dark"
                : "web.assets_backend_lazy";
        await loadBundle(bundleName);

        if (actionRegistry.get(actionTag) === actionLazyLoader) {
            actionRegistry.add(
                actionTag,
                () => {
                    env.services.notification.add(
                        _t("Nepavyko įkelti GanntLT laiko juostos modulio."),
                        { type: "danger" }
                    );
                },
                { force: true }
            );
        }
        return action;
    };
    actionRegistry.add(actionTag, actionLazyLoader, { force: true });
}

registerTimelineActionLazyLoader("gannt_lt.action");
registerTimelineActionLazyLoader("all_in_one_timeline.action");
