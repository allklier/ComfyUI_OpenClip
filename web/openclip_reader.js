import { app } from "../../scripts/app.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

// Shows the clip_report text (sent back via the "ui" half of execute()'s return value) in a
// read-only multiline widget on both reader nodes: clip, format, timecode, versions and AOVs,
// one version/AOV per line so names can be copied. Best-effort: if this breaks on a future
// ComfyUI frontend, the same text is the clip_report STRING output, wireable to any
// text-preview node.
const _NAME = "clip_report";

app.registerExtension({
    name: "OpenClip.Reader",

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (!["OpenClipReader", "OpenClipExrSequenceReader"].includes(nodeData.name)) {
            return;
        }
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            if (message?.[_NAME]) {
                _updateTextWidget(this, message[_NAME][0] ?? "");
            }
        };
    },
});

function _updateTextWidget(node, text) {
    let widget = node.widgets?.find((w) => w.name === _NAME);
    if (!widget) {
        widget = ComfyWidgets["STRING"](
            node, _NAME, ["STRING", { multiline: true }], app
        ).widget;
        widget.inputEl.readOnly = true;
        // The report lines its columns up with spaces.
        widget.inputEl.style.fontFamily = "monospace";
        widget.options = widget.options || {};
        widget.options.serialize = false;
    }
    widget.value = text;
    node.setDirtyCanvas(true);
}
