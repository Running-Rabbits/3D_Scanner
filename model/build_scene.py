# -*- coding: utf-8 -*-
"""
地球守护者（编程组）参数化建模脚本 —— Blender 4.x headless
依据：结构组装PDF p14-19（发射装置）、p25-32（旋转台）、材料清单
单位：米（真实SI）。所有尺寸为脚本顶部常量，可一键调整。
运行：blender.exe -b --factory-startup -noaudio --python build_scene.py
输出：out/turret.fbx, out/turntable.fbx, out/turntable.glb, out/params.json, renders/*.png
坐标系：Z向上，+X = 炮台瞄准方向（指向转盘）。
"""
import bpy, bmesh, math, os, json
from math import radians as rad

# ================= 参数（单位：米） =================
P = {
    # ---- 转盘（旋转台）----
    "tt_axis_h": 0.100,          # 转轴离地高 H
    "tt_orbit_r": 0.058,         # 纸杯中心轨道半径 R
    "tt_d1_r": 0.0875,           # 背板圆盘 D-1 半径
    "tt_d2_r": 0.085,            # 转盘 A-2 半径
    "wood_t": 0.0025,            # 木板厚 2.5mm
    "cup_top_d": 0.075,          # 纸杯口直径 75mm（材料清单）
    "cup_bot_d": 0.055,          # 纸杯底直径（常规270ml比例）
    "cup_h": 0.090,              # 纸杯长
    "cup_rear_x": 0.016,         # 杯底位置（在A-2圆盘x=0.01175之前方，避免遮挡Tag）
    "tag_size": 0.032,           # AprilTag 边长（受杯底内径限制）
    "omega_deg_s": 20.0,         # 实测转速
    "anim_seconds": 18.0,        # 360° 所需时间
    "flange_d": 0.060,           # 法兰盘 60mm（材料清单）
    # ---- 炮台（发射台）----
    "tur_base_r": 0.110,
    "tur_caster_r_pos": 0.072, "tur_caster_h": 0.017,   # 钢珠轮17mm（材料清单）
    "tur_mid_r": 0.090,
    "tur_flange_d": 0.060,
    "tur_upright_y": 0.024,
    "tur_cam_h": 0.178,          # 摄像头光轴离地高（估计）
    # ---- 场景（待A3精确测量）----
    "tt_scene_pos": [-0.45, 0.0, 0.0],   # 转盘在场地的位置（地图估算占位）
}

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "out")
REN = os.path.join(BASE, "renders")
os.makedirs(OUT, exist_ok=True)
os.makedirs(REN, exist_ok=True)

C = {  # 材料颜色 (linear-ish sRGB)
    "wood_light": (0.898, 0.631, 0.361, 1),
    "wood_dark":  (0.494, 0.290, 0.227, 1),
    "white":      (0.910, 0.910, 0.910, 1),
    "grey":       (0.680, 0.706, 0.737, 1),
    "grey_dark":  (0.360, 0.373, 0.390, 1),
    "yellow":     (0.906, 0.773, 0.271, 1),
    "black":      (0.055, 0.055, 0.060, 1),
    "copper":     (0.722, 0.451, 0.200, 1),
    "cup_red":    (0.651, 0.149, 0.224, 1),
    "tag_white":  (0.949, 0.949, 0.949, 1),
}
ROUGH = {"wood_light": .7, "wood_dark": .7, "white": .45, "grey": .5, "grey_dark": .55,
         "yellow": .38, "black": .55, "copper": .35, "cup_red": .45, "tag_white": .8}
METAL = {"copper": .8, "yellow": .4}
EMISS = {"tag_white": 0.4}   # 杯底 AprilTag 轻微自发光，保证空腔阴影下可检测

mats = {}
def mat(key):
    if key not in mats:
        m = bpy.data.materials.new("M_" + key)
        m.use_nodes = True
        bsdf = m.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = C[key]
        bsdf.inputs["Roughness"].default_value = ROUGH[key]
        if key in METAL:
            bsdf.inputs["Metallic"].default_value = METAL[key]
        if key in EMISS:
            bsdf.inputs["Emission Color"].default_value = C[key]
            bsdf.inputs["Emission Strength"].default_value = EMISS[key]
        mats[key] = m
    return mats[key]

# ================= 基础工具 =================
def _apply(obj, rot=True, scale=True):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=rot, scale=scale)

def box(name, dims, loc, mkey, rot=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot or (0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    o.scale = dims
    _apply(o)
    o.data.materials.append(mat(mkey))
    return o

def cyl(name, r, depth, loc, mkey, axis='X', verts=48, smooth=True):
    rot = {'X': (0, rad(90), 0), 'Y': (rad(90), 0, 0), 'Z': (0, 0, 0)}[axis]
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth,
                                        location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    _apply(o)
    if smooth:
        bpy.ops.object.shade_smooth()
    o.data.materials.append(mat(mkey))
    return o

def cone(name, r1, r2, depth, loc, mkey, rot=None, verts=48):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r1, radius2=r2,
                                    depth=depth, location=loc, rotation=rot or (0, 0, 0))
    o = bpy.context.active_object
    o.name = name
    _apply(o)
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat(mkey))
    return o

def prism(name, poly_xz, thick_y, mkey):
    """多边形棱柱：poly_xz=[(x,z),...]，沿Y拉伸 thick_y"""
    n = len(poly_xz)
    verts = [(x, -thick_y / 2, z) for x, z in poly_xz] + [(x, thick_y / 2, z) for x, z in poly_xz]
    faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    bm = bmesh.new(); bm.from_mesh(me)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    o.data.materials.append(mat(mkey))
    return o

def c_ring(name, major, minor, loc, mkey, gap_dir='+X'):
    """C形抱箍环（围绕X轴，开口朝gap_dir）"""
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.rotation_euler = (0, rad(90), 0)
    _apply(o)
    bm = bmesh.new(); bm.from_mesh(o.data)
    kill = [v for v in bm.verts if v.co.x > 0.0005]
    if kill:
        bmesh.ops.delete(bm, geom=kill, context='VERTS')
    bm.to_mesh(o.data); bm.free()
    bpy.ops.object.shade_smooth()
    o.data.materials.append(mat(mkey))
    return o

def empty(name, loc=(0, 0, 0)):
    bpy.ops.object.empty_add(type='PLAIN_AXES', location=loc)
    o = bpy.context.active_object
    o.name = name
    return o

def parent_keep(obj, parent):
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted()

def select_tree(root, state=True):
    root.select_set(state)
    for ch in root.children:
        select_tree(ch, state)

# ================= 场景初始化 =================
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1.0
scene.render.resolution_x = 960
scene.render.resolution_y = 720
scene.render.fps = 30
scene.view_settings.view_transform = 'Standard'
for eng in ("CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
    try:
        scene.render.engine = eng
        break
    except TypeError:
        continue
scene.cycles.samples = 48
scene.cycles.use_denoising = True
world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.86, 0.88, 0.92, 1)
bg.inputs[1].default_value = 0.35

def ground_plane():
    bpy.ops.mesh.primitive_plane_add(size=3, location=(0, 0, -0.001))
    o = bpy.context.active_object
    o.name = "Ground"
    m = bpy.data.materials.new("M_ground")
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.30, 0.32, 0.38, 1)
    o.data.materials.append(m)
    return o

# ================= 转盘 =================
def build_turntable():
    H = P["tt_axis_h"]; R = P["tt_orbit_r"]; wt = P["wood_t"]
    root = empty("Turntable")
    rotor = empty("Turntable_Rotor", loc=(0, 0, H))
    rotor.parent = root

    # 静止部分
    static = []
    static.append(cyl("TT_BaseD1", P["tt_d1_r"], wt, (0, 0, H), "wood_dark"))          # 背板 D-1
    static.append(cyl("TT_Flange", P["flange_d"] / 2, 0.008, (0.00525, 0, H), "white"))  # 法兰盘60
    static.append(cyl("TT_FlangeHub", 0.007, 0.006, (0.01225, 0, H), "white"))
    static.append(cyl("TT_MotorGear", 0.0175, 0.030, (-0.0165, 0, H), "grey"))          # 减速电机
    static.append(cyl("TT_MotorBody", 0.013, 0.034, (-0.0495, 0, H), "grey_dark"))
    for s in (1, -1):
        static.append(cyl(f"TT_Standoff{s}", 0.0015, 0.023, (-0.0125, s * 0.050, H), "grey_dark"))  # 尼龙柱M3*22
    static.append(prism("TT_BaseB2", [(-0.00125, 0), (-0.101, 0), (-0.101, 0.090), (-0.00125, 0.150)],
                        wt, "wood_dark"))                                               # 梯形底座 B-2
    static.append(box("TT_BatteryBox", (0.062, 0.032, 0.018), (-0.135, 0, 0.009), "black"))  # 电池盒（直供恒速）
    static.append(box("TT_BatterySwitch", (0.010, 0.012, 0.006), (-0.106, 0.020, 0.021), "grey_dark"))

    # 旋转部分（Rotor 子级）
    d2 = cyl("TT_ROTOR_DiscA2", P["tt_d2_r"], wt, (0.0105, 0, H), "wood_light")
    hub = cyl("TT_ROTOR_HubA1", 0.022, wt, (0.01325, 0, H), "wood_light")
    arms = []
    for i, th in enumerate((0, 120, 240)):   # 杯位角（0=正上）
        t = rad(th)
        arm = box(f"TT_ROTOR_Arm{i+1}", (wt, 0.026, 0.068),
                  (0.01325, 0.052 * math.sin(t), H + 0.052 * math.cos(t)),
                  "wood_light", rot=(-t, 0, 0))
        arms.append(arm)
    parts = [d2, hub] + arms

    # 3个纸杯（布尔挖空壳体）+ AprilTag + 抱箍
    cup_rear = P["cup_rear_x"]; cup_len = P["cup_h"]
    tag_x = cup_rear + 0.004
    for i, th in enumerate((0, 120, 240)):
        t = rad(th)
        cy, cz = R * math.sin(t), H + R * math.cos(t)
        cx = cup_rear + cup_len / 2
        bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=P["cup_bot_d"] / 2,
                                        radius2=P["cup_top_d"] / 2, depth=cup_len,
                                        location=(cx, cy, cz), rotation=(0, rad(90), 0))
        cup = bpy.context.active_object
        cup.name = f"TT_ROTOR_Cup{i+1}"
        _apply(cup)
        bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=0.023, radius2=0.034, depth=0.092,
                                        location=(cx + 0.004, cy, cz), rotation=(0, rad(90), 0))
        cutter = bpy.context.active_object
        _apply(cutter)
        mod = cup.modifiers.new("cavity", 'BOOLEAN')
        mod.operation = 'DIFFERENCE'
        mod.object = cutter
        bpy.context.view_layer.objects.active = cup
        bpy.ops.object.select_all(action='DESELECT')
        cup.select_set(True)
        bpy.ops.object.modifier_apply(modifier="cavity")
        bpy.data.objects.remove(cutter, do_unlink=True)
        bpy.ops.object.shade_smooth()
        cup.data.materials.append(mat("cup_red"))
        ring = c_ring(f"TT_ROTOR_Cradle{i+1}", 0.0295, 0.003, (cup_rear + 0.006, cy, cz), "white")
        tag = box(f"TT_ROTOR_Tag{i+1}_base", (0.0006, P["tag_size"], P["tag_size"]),
                  (tag_x, cy, cz), "tag_white")
        ts = P["tag_size"]
        fr = []
        fr.append(box(f"TT_ROTOR_Tag{i+1}_fT", (0.0006, ts, 0.006), (tag_x + 0.0006, cy, cz + ts / 2 - 0.003), "black"))
        fr.append(box(f"TT_ROTOR_Tag{i+1}_fB", (0.0006, ts, 0.006), (tag_x + 0.0006, cy, cz - ts / 2 + 0.003), "black"))
        fr.append(box(f"TT_ROTOR_Tag{i+1}_fL", (0.0006, 0.006, ts - 0.012), (tag_x + 0.0006, cy - ts / 2 + 0.003, cz), "black"))
        fr.append(box(f"TT_ROTOR_Tag{i+1}_fR", (0.0006, 0.006, ts - 0.012), (tag_x + 0.0006, cy + ts / 2 - 0.003, cz), "black"))
        fr.append(box(f"TT_ROTOR_Tag{i+1}_core", (0.0006, 0.014, 0.014), (tag_x + 0.0006, cy, cz), "black"))
        parts += [cup, ring, tag] + fr

    for p in static:
        parent_keep(p, root)
    for p in parts:
        parent_keep(p, rotor)
    return root, rotor

# ================= 炮台 =================
def build_turret():
    root = empty("Turret")
    o = []
    o.append(cyl("TRT_BaseDisc", P["tur_base_r"], 0.003, (0, 0, 0.0015), "wood_light", axis='Z'))
    for i, th in enumerate((90, 210, 330)):
        t = rad(th)
        o.append(cyl(f"TRT_Caster{i+1}", 0.009, P["tur_caster_h"],
                     (P["tur_caster_r_pos"] * math.cos(t), P["tur_caster_r_pos"] * math.sin(t), 0.0115), "grey", axis='Z'))
    o.append(cyl("TRT_MidPlate", P["tur_mid_r"], 0.0025, (0, 0, 0.02125), "wood_light", axis='Z'))
    o.append(cyl("TRT_Flange", P["tur_flange_d"] / 2, 0.008, (0, 0, 0.0265), "white", axis='Z'))
    o.append(cyl("TRT_FlangeHub", 0.007, 0.006, (0, 0, 0.0335), "white", axis='Z'))
    y = P["tur_upright_y"]
    o.append(box("TRT_UprightRear", (0.062, 0.0025, 0.108), (-0.001, y, 0.084), "wood_dark"))
    o.append(box("TRT_UprightFront", (0.062, 0.0025, 0.102), (-0.001, -y, 0.081), "wood_dark"))
    o.append(box("TRT_TopRail", (0.062, 0.053, 0.003), (-0.001, 0, 0.1395), "wood_light"))
    # 发射装置（发射支架+电机+拉勾+激光）
    o.append(box("TRT_Bracket", (0.088, 0.058, 0.020), (0.014, 0, 0.151), "grey"))
    o.append(box("TRT_BandLip", (0.016, 0.058, 0.008), (0.066, 0, 0.137), "grey"))
    o.append(cyl("TRT_Motor", 0.013, 0.040, (0.045, 0, 0.151), "yellow"))
    o.append(cyl("TRT_PullHook", 0.015, 0.007, (0.0775, 0, 0.145), "black"))
    o.append(box("TRT_LaserPCB", (0.022, 0.014, 0.003), (0.041, 0, 0.1625), "grey_dark"))
    o.append(cyl("TRT_Laser", 0.005, 0.022, (0.043, 0, 0.163), "copper"))
    # 舵机（俯仰）
    o.append(box("TRT_Servo", (0.036, 0.020, 0.038), (-0.034, 0, 0.160), "black"))
    o.append(cyl("TRT_ServoHorn", 0.009, 0.003, (-0.034, 0, 0.1805), "white", axis='Z'))
    o.append(box("TRT_ServoArm", (0.026, 0.006, 0.004), (-0.020, 0.004, 0.1805), "white"))
    # 摄像头
    o.append(box("TRT_Camera", (0.026, 0.026, 0.026), (0.066, 0, 0.178), "black"))
    o.append(cyl("TRT_LensRing", 0.007, 0.010, (0.084, 0, 0.178), "grey"))
    o.append(cyl("TRT_LensEye", 0.005, 0.002, (0.0895, 0, 0.178), "grey_dark"))
    for p in o:
        parent_keep(p, root)
    return root

# ================= 旋转动画（20°/s） =================
def bake_rotation(rotor):
    fps = scene.render.fps
    n = int(P["anim_seconds"] * fps)
    scene.frame_start = 1
    scene.frame_end = n + 1
    rotor.rotation_euler = (0, 0, 0)
    rotor.keyframe_insert(data_path="rotation_euler", frame=1)
    total = rad(360.0)
    steps = 36
    for s in range(1, steps + 1):
        f = 1 + int(n * s / steps)
        rotor.rotation_euler = (total * s / steps, 0, 0)
        rotor.keyframe_insert(data_path="rotation_euler", frame=f)
    # 线性插值保证恒速
    for fc in rotor.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'

# ================= 渲染 =================
def add_light():
    bpy.ops.object.light_add(type='SUN', location=(0.5, -0.6, 1.2))
    sun = bpy.context.active_object
    sun.data.energy = 1.5
    sun.rotation_euler = (rad(50), 0, rad(-35))
    bpy.ops.object.light_add(type='AREA', location=(0.0, -0.7, 0.7))
    ar = bpy.context.active_object
    ar.data.energy = 25
    ar.data.size = 1.5
    ar.rotation_euler = (rad(55), 0, 0)

def look_at(obj, target):
    d = bpy.app.version  # noqa
    from mathutils import Vector
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

def render_view(name, cam_loc, target, lens=50):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = lens
    cam = bpy.data.objects.new("CAM_" + name, cam_data)
    scene.collection.objects.link(cam)
    cam.location = cam_loc
    look_at(cam, target)
    scene.camera = cam
    scene.render.filepath = os.path.join(REN, name + ".png")
    bpy.ops.render.render(write_still=True)

# ================= 主流程 =================
def main():
    ground_plane()
    tt_root, tt_rotor = build_turntable()
    tur_root = build_turret()
    bake_rotation(tt_rotor)
    add_light()

    # 预览摆位：炮台居中转向转盘，转盘在 -X 侧（杯口朝炮台）
    tur_root.rotation_euler = (0, 0, rad(180))
    tt_root.location = P["tt_scene_pos"]
    bpy.context.view_layer.update()

    render_view("tt_iso", (-0.85, -0.55, 0.38), (-0.45, 0, 0.10))
    render_view("tt_front_from_turret", (0.0, 0.0, 0.16), (-0.45, 0, 0.10), lens=40)
    render_view("turret_iso", (0.45, -0.50, 0.33), (0.0, 0, 0.09))
    render_view("turret_front", (0.38, 0.18, 0.22), (0.02, 0, 0.13))
    render_view("pair_wide", (0.30, -0.95, 0.55), (-0.22, 0, 0.07), lens=40)

    # 导出
    bpy.ops.object.select_all(action='DESELECT')
    select_tree(tur_root)
    bpy.ops.export_scene.fbx(filepath=os.path.join(OUT, "turret.fbx"), use_selection=True)

    bpy.ops.object.select_all(action='DESELECT')
    select_tree(tt_root)
    bpy.ops.export_scene.fbx(filepath=os.path.join(OUT, "turntable.fbx"), use_selection=True)
    try:
        bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "turntable.glb"), use_selection=True,
                                  export_format='GLB')
    except Exception as e:
        print("GLB export failed:", e)

    # 参数表
    out_params = dict(P)
    out_params.update({
        "units": "meters",
        "axes": "Z-up, +X = turret aiming direction",
        "turntable_rotation": {
            "axis": "+X (toward turret)", "omega_deg_s": P["omega_deg_s"],
            "direction": "right-hand about +X (可翻转)",
            "animation": f"{int(P['anim_seconds']*scene.render.fps)+1} frames @ {scene.render.fps}fps, linear",
        },
        "cup_tag": {"faces": "+X (toward turret)", "size_m": P["tag_size"],
                    "note": "视觉占位图案，正式仿真请替换为 tag36h11 id1/2/3 图片"},
        "turret_camera": {"lens_axis": "+X", "height_m": P["tur_cam_h"]},
        "scene_layout_status": "turntable position = map estimate, pending A3 measurement",
        "precision_notes": "精确: 杯φ75/法兰60/钢珠轮17/转速20°/s/地图1200x600; 估算: 木板外形/R/H/炮台各件",
    })
    with open(os.path.join(OUT, "params.json"), "w", encoding="utf-8") as f:
        json.dump(out_params, f, ensure_ascii=False, indent=2)

    print("DONE. out=", OUT, " renders=", REN)

main()
