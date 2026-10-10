# The owner's 3D CR-Z for share/js/car3d.js, from the bought model's OBJ export.
#
#   blender --background --python tools/car_model_convert.py -- IN.obj share/models/crz.glb
#
# IN.obj is "Honda CR-Z EX 2015.obj" from the owner's purchase (the .rar of
# 2026-09-18). Its materials did not survive the export from 3ds Max, but its
# part names did (carpaint, chrome, glass_red, tire...), so each part gets a
# material by name; an unnamed part with exactly a named part's face count (the
# fourth wheel, say) gets that part's. Dark grey paint, the owner's colour.
# Reduced to ~160k triangles, visible parts least. The model and its output are
# never committed: share/models/ is git-ignored (doc/car3d.md, trademarks).
import bpy, sys

src, out = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.obj_import(filepath=src, use_split_groups=True, use_split_objects=True)

def mat(name, rgb, metal=0.0, rough=0.5, alpha=1.0, emit=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*rgb, 1.0)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    p.inputs["Alpha"].default_value = alpha
    if emit:
        p.inputs["Emission Color"].default_value = (*emit, 1.0)
        p.inputs["Emission Strength"].default_value = 1.0
    if alpha < 1.0:
        m.surface_render_method = "BLENDED"
    return m

M = {
    # Dark grey metallic, the owner's colour (Honda's "Polished Metal").
    "paint": mat("paint", (0.11, 0.115, 0.125), metal=0.6, rough=0.3),
    "chrome": mat("chrome", (0.80, 0.80, 0.82), metal=1.0, rough=0.12),
    "rim": mat("rim", (0.55, 0.56, 0.58), metal=1.0, rough=0.25),
    "tire": mat("tire", (0.018, 0.018, 0.02), rough=0.85),
    "disc": mat("disc", (0.33, 0.33, 0.34), metal=0.85, rough=0.45),
    "glass": mat("glass", (0.03, 0.035, 0.04), rough=0.04, alpha=0.38),
    "lens": mat("lens", (0.85, 0.88, 0.92), rough=0.03, alpha=0.22),
    "red": mat("red", (0.55, 0.01, 0.01), rough=0.15, alpha=0.75, emit=(0.08, 0.0, 0.0)),
    "orange": mat("orange", (0.75, 0.28, 0.0), rough=0.15, alpha=0.75),
    "black": mat("black", (0.022, 0.022, 0.024), rough=0.6),
    "shiny": mat("shiny", (0.02, 0.02, 0.022), rough=0.22),
    "white": mat("white", (0.62, 0.62, 0.62), rough=0.5),
    "trim": mat("trim", (0.03, 0.03, 0.032), rough=0.55),
}

def kind(name):
    n = name.lower()
    for key, k in (("carpaint", "paint"), ("chrome", "chrome"), ("rim", "rim"),
                   ("tire", "tire"), ("brake_disc", "disc"), ("glass_red", "red"),
                   ("glass_orange", "orange"), ("glass", "glass"), ("xenon", "lens"),
                   ("transparent", "lens"), ("black_shiny", "shiny"), ("white", "white"),
                   ("black", "black"), ("interior", "black"), ("seats", "black")):
        if key in n:
            return k
    return None

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
faces = {o.name: len(o.data.polygons) for o in meshes}
# Unnamed parts that are copies of a named one (the fourth wheel, say) have
# exactly its face count: give them its material.
by_count = {}
for o in meshes:
    k = kind(o.name)
    if k:
        by_count.setdefault(faces[o.name], k)
for o in meshes:
    k = kind(o.name) or by_count.get(faces[o.name]) or "trim"
    o.data.materials.clear()
    o.data.materials.append(M[k])
    n = faces[o.name]
    # What shows from outside keeps its detail; what hides inside gives it up.
    ratio = {"paint": 0.33, "glass": 0.5, "lens": 0.35, "red": 0.35, "orange": 0.35,
             "chrome": 0.09, "rim": 0.08, "disc": 0.08, "tire": 0.07,
             "black": 0.06, "shiny": 0.15, "white": 0.2, "trim": 0.1}[k]
    if n > 400:
        d = o.modifiers.new("reduce", "DECIMATE")
        d.ratio = ratio
        d.use_collapse_triangulate = True

bpy.ops.export_scene.gltf(filepath=out, export_format="GLB", export_yup=True,
                          export_apply=True)
tris = 0
for o in meshes:
    ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
    tris += sum(len(p.vertices) - 2 for p in ev.data.polygons)
print(f"CONVERTED parts={len(meshes)} triangles~{tris}")
