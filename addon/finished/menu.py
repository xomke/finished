import bpy

from .operators import FINISHED_OT_render_animation


def draw_render_menu(self, _context):
    layout = self.layout
    layout.separator()
    layout.operator(
        FINISHED_OT_render_animation.bl_idname,
        text="Render Animation with Finished?",
        icon="RENDER_ANIMATION",
    )


def register():
    bpy.types.TOPBAR_MT_render.append(draw_render_menu)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(draw_render_menu)
