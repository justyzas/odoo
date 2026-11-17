/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { loadBundle } from "@web/core/assets";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { rpcBus } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

const actionRegistry = registry.category("actions");

// Clear Odoo's per-tab sessionStorage.current_action whenever registry_hash changes
// (e.g. GanntLT permission tier changed before reload) so action_service.js re-fetches
// /web/action/load instead of reusing stale views from sessionStorage.
const currentRegistryHash = session.registry_hash || "";
const prevRegistryHash = browser.sessionStorage.getItem("gannt_lt_registry_hash");
if (currentRegistryHash && prevRegistryHash !== currentRegistryHash) {
    browser.sessionStorage.removeItem("current_action");
    browser.sessionStorage.setItem("gannt_lt_registry_hash", currentRegistryHash);
}

// Invalidate cached window actions and views when user/group permissions change
// in the same SPA session so Timeline view switcher visibility updates immediately.
rpcBus.addEventListener("RPC:RESPONSE", (ev) => {
    const detail = ev.detail;
    if (!detail || detail.error) {
        return;
    }
    const params = detail.data && detail.data.params;
    if (
        params &&
        (params.model === "res.users" || params.model === "res.groups") &&
        ["write", "web_save", "create", "unlink"].includes(params.method)
    ) {
        browser.sessionStorage.removeItem("current_action");
        rpcBus.trigger("CLEAR-CACHES", ["/web/action/load", "get_views"]);
    }
});

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
