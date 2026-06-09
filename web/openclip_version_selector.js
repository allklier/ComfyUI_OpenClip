import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "OpenClip.VersionSelector",

    async beforeRegisterNodeDef(nodeType, nodeData, _app) {
        if (nodeData.name !== "OpenClipVersionSelector") {
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
    const clipPathWidget = node.widgets?.find((w) => w.name === "clip_path");
    const versionWidget  = node.widgets?.find((w) => w.name === "selected_version");
    const latestWidget   = node.widgets?.find((w) => w.name === "latest_version");

    if (!clipPathWidget || !versionWidget || !latestWidget) {
        return;
    }

    _makeReadOnly(latestWidget);

    node.addWidget("button", "↻  Refresh & Check", null, async () => {
        const path = (clipPathWidget.value ?? "").trim();
        if (!path) {
            return;
        }
        await _refreshAndCheck(path, versionWidget, latestWidget, node);
    });
}

async function _refreshAndCheck(clipPath, versionWidget, latestWidget, node) {
    let data;
    try {
        const resp = await fetch(`/openclip/versions?path=${encodeURIComponent(clipPath)}`);
        if (!resp.ok) {
            console.error(`[OpenClip] Server returned ${resp.status} for versions request`);
            return;
        }
        data = await resp.json();
    } catch (err) {
        console.error("[OpenClip] Failed to fetch versions:", err);
        return;
    }

    if (!Array.isArray(data.versions) || data.versions.length === 0) {
        console.warn("[OpenClip] No versions found in clip:", clipPath);
        return;
    }

    const latest = data.current_version ?? data.versions[data.versions.length - 1];
    latestWidget.value = latest;

    const typed = (versionWidget.value ?? "").trim();
    if (!typed) {
        versionWidget.value = latest;
    } else if (!data.versions.includes(typed)) {
        alert(
            `[OpenClip] Version '${typed}' not found in clip.\n` +
            `Available: ${data.versions.join(", ")}`
        );
    }

    node.setDirtyCanvas(true);
}

// Render the latest_version widget as a non-interactive display label.
function _makeReadOnly(widget) {
    widget.draw = function (ctx, node, widgetWidth, y, H) {
        const margin = 15;
        const w = widgetWidth - margin * 2;

        ctx.fillStyle = "#1a1a1a";
        ctx.fillRect(margin, y, w, H);
        ctx.strokeStyle = "#2e2e2e";
        ctx.lineWidth = 1;
        ctx.strokeRect(margin, y, w, H);

        ctx.textAlign = "left";

        ctx.fillStyle = "#555";
        ctx.font = "10px sans-serif";
        ctx.fillText("Latest:", margin + 6, y + H * 0.42);

        ctx.fillStyle = "#999";
        ctx.font = "12px sans-serif";
        ctx.fillText(this.value ?? "", margin + 55, y + H * 0.72);

        ctx.textAlign = "center";
    };

    // Swallow mouse events so the field is not editable.
    widget.mouse = () => false;
}
