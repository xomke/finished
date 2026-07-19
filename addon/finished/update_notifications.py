"""Show a newly discovered update in the active 3D Viewport when possible."""


def notify_update_available(version):
    try:
        import bpy

        if bpy.app.background:
            return False
        window = bpy.context.window
        if window is None:
            return False
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((item for item in area.regions if item.type == "WINDOW"), None)
            if region is None:
                continue
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = bpy.ops.finished.show_update_available("INVOKE_DEFAULT", version=str(version))
            return "RUNNING_MODAL" in result or "FINISHED" in result
        return False
    except Exception:
        return False
