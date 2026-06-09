from __future__ import annotations

from .nodes.colour_transform import OpenClipColourTransform
from .nodes.reader import OpenClipReader
from .nodes.version_selector import OpenClipVersionSelector
from .nodes.writer import OpenClipWriter

NODE_CLASS_MAPPINGS = {
    "OpenClipColourTransform": OpenClipColourTransform,
    "OpenClipReader": OpenClipReader,
    "OpenClipWriter": OpenClipWriter,
    "OpenClipVersionSelector": OpenClipVersionSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OpenClipColourTransform": "OpenClip Colour Transform",
    "OpenClipReader": "OpenClip Reader",
    "OpenClipWriter": "OpenClip Writer",
    "OpenClipVersionSelector": "OpenClip Version Selector",
}

WEB_DIRECTORY = "./web"

try:
    from aiohttp import web as aiohttp_web
    from server import PromptServer

    from .lib import openclip_xml as _xml

    @PromptServer.instance.routes.get("/openclip/versions")
    async def _get_clip_versions(request: aiohttp_web.Request) -> aiohttp_web.Response:
        clip_path = request.rel_url.query.get("path", "").strip()
        if not clip_path:
            return aiohttp_web.json_response({"versions": [], "current_version": ""})
        try:
            parsed = _xml.parse(clip_path)
            return aiohttp_web.json_response({
                "versions": list(parsed.versions.keys()),
                "current_version": parsed.current_version,
            })
        except Exception as exc:
            return aiohttp_web.json_response({"error": str(exc)}, status=400)

except ImportError:
    pass  # ComfyUI server not available (e.g. test environment)
