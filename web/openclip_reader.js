import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

// Populates read-only multiline widgets on the Reader node with text sent back via the
// "ui" half of execute()'s return value: available_versions, and available_aovs (the
// AOV probe -- copy a name from here into an OpenClipAOVReader's part_name). Best-effort:
// if this breaks on a future ComfyUI frontend, both texts are still available as STRING
// outputs, wireable to any text-preview node.
const _TEXT_WIDGETS = ["available_versions", "available_aovs"];

app.registerExtension({
    name: "OpenClip.Reader",

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!["OpenClipReader", "OpenClipExrSequenceReader"].includes(nodeData.name)) {
            return;
        }
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            // Only the texts this node sends: the EXR Sequence Reader has no versions.
            for (const name of _TEXT_WIDGETS) {
                if (message?.[name]) {
                    _updateTextWidget(this, name, message);
                }
            }
        };
    },
});

function _updateTextWidget(node, name, message) {
    const text = message?.[name]?.[0] ?? "";

    let widget = node.widgets?.find((w) => w.name === name);
    if (!widget) {
        widget = ComfyWidgets["STRING"](
            node, name, ["STRING", { multiline: true }], app
        ).widget;
        widget.inputEl.readOnly = true;
        widget.options = widget.options || {};
        widget.options.serialize = false;
    }
    widget.value = text;
    node.setDirtyCanvas(true);
}
