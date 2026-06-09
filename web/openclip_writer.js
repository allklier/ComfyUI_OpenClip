import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "OpenClip.Writer",

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== "OpenClipWriter") {
            return;
        }
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            _setupNode(this);
        };
    },
});

function _setupNode(node) {
    const formatWidget     = node.widgets?.find((w) => w.name === "file_format");
    const bitDepthWidget   = node.widgets?.find((w) => w.name === "exr_bit_depth");
    const compressionWidget = node.widgets?.find((w) => w.name === "exr_compression");

    if (!formatWidget || !bitDepthWidget || !compressionWidget) {
        return;
    }

    function _updateEXRWidgets() {
        const isEXR = formatWidget.value === "EXR";
        bitDepthWidget.hidden   = !isEXR;
        compressionWidget.hidden = !isEXR;
        node.setSize(node.computeSize());
        node.setDirtyCanvas(true);
    }

    const origCallback = formatWidget.callback;
    formatWidget.callback = function (value) {
        origCallback?.call(this, value);
        _updateEXRWidgets();
    };

    _updateEXRWidgets();
}
