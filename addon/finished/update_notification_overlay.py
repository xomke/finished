"""A brief, theme-aware update toast in the lower-right 3D Viewport corner."""

import time


TOAST_WIDTH = 480
TOAST_HEIGHT = 126
TOAST_RIGHT_MARGIN = 28
TOAST_BOTTOM_MARGIN = 28
TOAST_VISIBLE_SECONDS = 4.0
TOAST_FADE_SECONDS = 0.4
TOAST_BACKGROUND_OPACITY = 0.5
TOAST_CLOSE_SIZE = 24
TOAST_CLOSE_MARGIN = 12


_draw_handle = None
_target_area_pointer = None
_version = ""
_shown_at = 0.0
_expiry_timer_registered = False
_close_hovered = False


def toast_bounds(region_width, region_height):
    """Return a lower-right toast rectangle inside a viewport WINDOW region."""

    available_width = max(0, region_width - (2 * TOAST_RIGHT_MARGIN))
    width = min(TOAST_WIDTH, available_width)
    right = max(width, region_width - TOAST_RIGHT_MARGIN)
    left = right - width
    bottom = TOAST_BOTTOM_MARGIN
    return (left, bottom, right, bottom + TOAST_HEIGHT)


def opacity_at(elapsed):
    """Return a simple fade-out alpha without relying on Blender state."""

    if elapsed < 0 or elapsed >= TOAST_VISIBLE_SECONDS:
        return 0.0
    fade_start = TOAST_VISIBLE_SECONDS - TOAST_FADE_SECONDS
    if elapsed <= fade_start:
        return 1.0
    return max(0.0, (TOAST_VISIBLE_SECONDS - elapsed) / TOAST_FADE_SECONDS)


def close_bounds(bounds):
    """Return the square click target for the toast close control."""

    _left, _bottom, right, top = bounds
    close_right = right - TOAST_CLOSE_MARGIN
    close_top = top - TOAST_CLOSE_MARGIN
    return (
        close_right - TOAST_CLOSE_SIZE,
        close_top - TOAST_CLOSE_SIZE,
        close_right,
        close_top,
    )


def is_close_click(bounds, x, y):
    left, bottom, right, top = close_bounds(bounds)
    return left <= x <= right and bottom <= y <= top


def close_from_event(context, event):
    """Close when the user clicks the visible close control."""

    if event.type != "LEFTMOUSE" or event.value != "PRESS":
        return False
    if not _is_target_context(context):
        return False
    region = getattr(context, "region", None)
    if region is None or getattr(region, "type", "") != "WINDOW":
        return False
    if not is_close_click(toast_bounds(region.width, region.height), event.mouse_region_x, event.mouse_region_y):
        return False
    close()
    return True


def update_hover(context, event):
    """Refresh the close-control hover state without intercepting viewport input."""

    global _close_hovered
    if event.type not in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"} or not _is_target_context(context):
        return False
    region = getattr(context, "region", None)
    if region is None or getattr(region, "type", "") != "WINDOW":
        return False
    hovered = is_close_click(toast_bounds(region.width, region.height), event.mouse_region_x, event.mouse_region_y)
    if hovered == _close_hovered:
        return False
    _close_hovered = hovered
    context.area.tag_redraw()
    return True


def show(context, version):
    """Show the toast briefly in the active 3D Viewport."""

    global _draw_handle
    global _target_area_pointer
    global _version
    global _shown_at
    global _close_hovered

    if getattr(context.area, "type", "") != "VIEW_3D":
        return False
    close()
    try:
        import bpy

        _target_area_pointer = context.area.as_pointer()
        _version = str(version)
        _shown_at = time.monotonic()
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")
        _ensure_expiry_timer(bpy)
        return True
    except Exception:
        close()
        return False


def close():
    """Remove the toast safely; it is fine to call this more than once."""

    global _draw_handle
    global _target_area_pointer
    global _version
    global _shown_at
    global _close_hovered

    handle = _draw_handle
    _draw_handle = None
    _target_area_pointer = None
    _version = ""
    _shown_at = 0.0
    _close_hovered = False
    if handle is None:
        return
    try:
        import bpy

        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    except Exception:
        pass


def is_visible():
    return _draw_handle is not None


def _ensure_expiry_timer(bpy):
    global _expiry_timer_registered

    if _expiry_timer_registered:
        return
    bpy.app.timers.register(_expiry_timer, first_interval=TOAST_VISIBLE_SECONDS)
    _expiry_timer_registered = True


def _expiry_timer():
    global _expiry_timer_registered

    elapsed = time.monotonic() - _shown_at
    if _draw_handle is not None and elapsed < TOAST_VISIBLE_SECONDS:
        return TOAST_VISIBLE_SECONDS - elapsed
    close()
    _expiry_timer_registered = False
    return None


def _draw():
    blending_enabled = False
    try:
        import blf
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader

        context = bpy.context
        if not _is_target_context(context):
            return
        region = getattr(context, "region", None)
        if region is None or getattr(region, "type", "") != "WINDOW":
            return
        opacity = opacity_at(time.monotonic() - _shown_at)
        if opacity <= 0:
            return
        bounds = toast_bounds(region.width, region.height)
        close_button = close_bounds(bounds)
        colors = _theme_colors(context, opacity)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        blending_enabled = True
        _rectangle(shader, batch_for_shader, bounds, colors["background"])
        _rectangle(shader, batch_for_shader, bounds, colors["outline"], outline=True)
        if _close_hovered:
            _rectangle(shader, batch_for_shader, close_button, colors["close_hover"])
        _text(blf, close_button[0] + 4, close_button[1] + 2, "×", 22, colors["close_text"])
        _text(blf, bounds[0] + 20, bounds[3] - 36, "Finished? update available", 21, colors["text"])
        _text(blf, bounds[0] + 20, bounds[3] - 68, f"Version {_version}", 17, colors["text"])
        _text(blf, bounds[0] + 20, bounds[3] - 98, "Open the add-on preferences to update.", 16, colors["text"])
    except Exception:
        # A notification must never interfere with Blender drawing.
        return
    finally:
        if blending_enabled:
            try:
                gpu.state.blend_set("NONE")
            except Exception:
                pass


def _is_target_context(context):
    area = getattr(context, "area", None)
    return area is not None and area.as_pointer() == _target_area_pointer


def _theme_colors(context, opacity):
    fallback = {
        "background": (0.09, 0.09, 0.09, TOAST_BACKGROUND_OPACITY * opacity),
        "outline": (0.34, 0.34, 0.34, 0.82 * opacity),
        "text": (0.92, 0.92, 0.92, opacity),
        "close_text": (0.72, 0.72, 0.72, opacity),
        "close_hover": (0.34, 0.34, 0.34, 0.8 * opacity),
    }
    try:
        theme = context.preferences.themes[0].user_interface.wcol_tool
        return {
            "background": _with_alpha(theme.inner, TOAST_BACKGROUND_OPACITY * opacity),
            "outline": _with_alpha(theme.outline, 0.82 * opacity),
            "text": _with_alpha(theme.text, opacity),
            "close_text": _with_alpha(theme.text, 0.72 * opacity),
            "close_hover": _with_alpha(theme.inner_sel, 0.8 * opacity),
        }
    except Exception:
        return fallback


def _with_alpha(color, alpha):
    return (float(color[0]), float(color[1]), float(color[2]), alpha)


def _rectangle(shader, batch_for_shader, bounds, color, *, outline=False):
    left, bottom, right, top = bounds
    if outline:
        coordinates = ((left, bottom), (right, bottom), (right, top), (left, top), (left, bottom))
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": coordinates})
    else:
        coordinates = ((left, bottom), (right, bottom), (right, top), (left, top))
        batch = batch_for_shader(shader, "TRI_FAN", {"pos": coordinates})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def _text(blf, x, y, value, size, color):
    font_id = 0
    blf.size(font_id, size)
    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, value)
