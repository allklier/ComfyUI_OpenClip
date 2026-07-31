import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

// Populates a read-only multiline widget on the Reader node with the
// available_versions text sent back via the "ui" half of execute()'s
// return value. Best-effort: if this breaks on a future ComfyUI frontend,
// the same text is still available as the available_versions STRING
// output, wireable to any text-preview node.
app.registerExtension({
    name: "OpenClip.Reader",

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== "OpenClipReader") {
            return;
        }
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            _updateAvailableVersionsWidget(this, message);
        };
    },
});

function _updateAvailableVersionsWidget(node, message) {
    const text = message?.available_versions?.[0] ?? "";

    let widget = node.widgets?.find((w) => w.name === "available_versions");
    if (!widget) {
        widget = ComfyWidgets["STRING"](
            node, "available_versions", ["STRING", { multiline: true }], app
        ).widget;
        widget.inputEl.readOnly = true;
        widget.options = widget.options || {};
        widget.options.serialize = false;
    }
    widget.value = text;
    node.setDirtyCanvas(true);
}
