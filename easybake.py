bl_info = {
    "name": "Procedural Texture Baker",
    "author": "Friedrich Eibl",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Bake",
    "description": "Bake procedural materials to diffuse, roughness, and normal textures",
    "category": "Material",
}

import bpy
import os


def ensure_dir(path):
    path = bpy.path.abspath(path)
    os.makedirs(path, exist_ok=True)
    return path


def create_image(name, width, height, alpha=True):
    img = bpy.data.images.new(name, width=width, height=height, alpha=alpha)
    img.generated_color = (0, 0, 0, 1)
    return img


def get_or_create_image_node(material, image):
    material.use_nodes = True
    nodes = material.node_tree.nodes

    node = nodes.new("ShaderNodeTexImage")
    node.name = "__BAKE_TARGET__"
    node.label = "Bake Target"
    node.image = image

    nodes.active = node
    node.select = True
    return node


def remove_node(material, node):
    if material and material.use_nodes and node:
        material.node_tree.nodes.remove(node)


class PTB_Settings(bpy.types.PropertyGroup):
    target_object: bpy.props.PointerProperty(
        name="Object",
        type=bpy.types.Object,
        description="Mesh object to bake"
    )

    resolution: bpy.props.EnumProperty(
        name="Resolution",
        items=[
            ("512", "512", ""),
            ("1024", "1024", ""),
            ("2048", "2048", ""),
            ("4096", "4096", ""),
            ("8192", "8192", ""),
        ],
        default="2048"
    )

    samples: bpy.props.IntProperty(
        name="Samples",
        default=64,
        min=1,
        max=4096
    )

    margin: bpy.props.IntProperty(
        name="Margin",
        default=16,
        min=0,
        max=128
    )

    output_dir: bpy.props.StringProperty(
        name="Output Folder",
        subtype="DIR_PATH",
        default="//baked_textures/"
    )

    file_format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ("PNG", "PNG", ""),
            ("JPEG", "JPEG", ""),
            ("TIFF", "TIFF", ""),
            ("OPEN_EXR", "OpenEXR", ""),
        ],
        default="PNG"
    )

    bake_diffuse: bpy.props.BoolProperty(
        name="Diffuse / Base Color",
        default=True
    )

    bake_roughness: bpy.props.BoolProperty(
        name="Roughness",
        default=True
    )

    bake_normal: bpy.props.BoolProperty(
        name="Normal",
        default=True
    )

    create_baked_material: bpy.props.BoolProperty(
        name="Create Baked Material",
        default=True
    )


class PTB_OT_bake(bpy.types.Operator):
    bl_idname = "ptb.bake"
    bl_label = "Bake Textures"
    bl_options = {"REGISTER", "UNDO"}

    def bake_pass(self, obj, image_name, bake_type, settings):
        output_dir = ensure_dir(settings.output_dir)
        resolution = int(settings.resolution)

        image = create_image(image_name, resolution, resolution)
        image.file_format = settings.file_format

        ext = {
            "PNG": "png",
            "JPEG": "jpg",
            "TIFF": "tif",
            "OPEN_EXR": "exr",
        }[settings.file_format]

        image.filepath_raw = os.path.join(output_dir, f"{image_name}.{ext}")

        bake_nodes = []

        for slot in obj.material_slots:
            mat = slot.material
            if not mat:
                continue

            node = get_or_create_image_node(mat, image)
            bake_nodes.append((mat, node))

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.samples = settings.samples

        bpy.context.scene.render.bake.margin = settings.margin
        bpy.context.scene.render.bake.target = "IMAGE_TEXTURES"

        if bake_type == "DIFFUSE":
            bpy.context.scene.render.bake.use_pass_direct = False
            bpy.context.scene.render.bake.use_pass_indirect = False
            bpy.context.scene.render.bake.use_pass_color = True

        bpy.ops.object.bake(type=bake_type)

        image.save()

        for mat, node in bake_nodes:
            remove_node(mat, node)

        return image

    def create_baked_material(self, obj, images):
        mat = bpy.data.materials.new(f"{obj.name}_Baked_Material")
        mat.use_nodes = True

        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        bsdf = nodes.get("Principled BSDF")

        if "diffuse" in images:
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = images["diffuse"]
            tex.label = "Baked Diffuse"
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

        if "roughness" in images:
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = images["roughness"]
            tex.label = "Baked Roughness"
            tex.image.colorspace_settings.name = "Non-Color"
            links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])

        if "normal" in images:
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = images["normal"]
            tex.label = "Baked Normal"
            tex.image.colorspace_settings.name = "Non-Color"

            normal_map = nodes.new("ShaderNodeNormalMap")
            links.new(tex.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

        obj.data.materials.clear()
        obj.data.materials.append(mat)

    def execute(self, context):
        settings = context.scene.ptb_settings
        obj = settings.target_object or context.object

        if not obj:
            self.report({"ERROR"}, "No object selected")
            return {"CANCELLED"}

        if obj.type != "MESH":
            self.report({"ERROR"}, "Object must be a mesh")
            return {"CANCELLED"}

        if not obj.data.uv_layers:
            self.report({"ERROR"}, "Object has no UV map")
            return {"CANCELLED"}

        if not obj.material_slots:
            self.report({"ERROR"}, "Object has no materials")
            return {"CANCELLED"}

        baked_images = {}

        try:
            if settings.bake_diffuse:
                baked_images["diffuse"] = self.bake_pass(
                    obj,
                    f"{obj.name}_Diffuse",
                    "DIFFUSE",
                    settings
                )

            if settings.bake_roughness:
                baked_images["roughness"] = self.bake_pass(
                    obj,
                    f"{obj.name}_Roughness",
                    "ROUGHNESS",
                    settings
                )

            if settings.bake_normal:
                baked_images["normal"] = self.bake_pass(
                    obj,
                    f"{obj.name}_Normal",
                    "NORMAL",
                    settings
                )

            if settings.create_baked_material:
                self.create_baked_material(obj, baked_images)

        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        self.report({"INFO"}, "Bake complete")
        return {"FINISHED"}


class PTB_PT_panel(bpy.types.Panel):
    bl_label = "Procedural Texture Baker"
    bl_idname = "PTB_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bake"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ptb_settings

        layout.prop(settings, "target_object")
        layout.prop(settings, "resolution")
        layout.prop(settings, "samples")
        layout.prop(settings, "margin")
        layout.prop(settings, "output_dir")
        layout.prop(settings, "file_format")

        layout.separator()
        layout.label(text="Maps")
        layout.prop(settings, "bake_diffuse")
        layout.prop(settings, "bake_roughness")
        layout.prop(settings, "bake_normal")

        layout.separator()
        layout.prop(settings, "create_baked_material")

        layout.separator()
        layout.operator("ptb.bake", icon="RENDER_STILL")


classes = (
    PTB_Settings,
    PTB_OT_bake,
    PTB_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ptb_settings = bpy.props.PointerProperty(type=PTB_Settings)


def unregister():
    del bpy.types.Scene.ptb_settings

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
