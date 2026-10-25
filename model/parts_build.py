# -*- coding: utf-8 -*-
"""
地球守护者（编程组）零件级参数化建模 —— Blender 4.x headless
依据：结构组装PDF p8-32 + 电路组装PDF p2-14 + 用户实测（A-1三叶板螺丝孔距85mm→R=49.1mm）
每个零件独立函数，平铺成网格渲染"零件全家福"两张（结构件/电子件）。
单位：米。坐标系：Z向上。
运行：blender.exe -b --factory-startup -noaudio --python parts_build.py
输出：renders/parts_struct.png, renders/parts_elec.png
"""
import bpy, bmesh, math, os
from math import radians as rad, cos, sin, acos, pi
from mathutils import Vector

BASE = os.path.dirname(os.path.abspath(__file__))
REN = os.path.join(BASE, "renders")
os.makedirs(REN, exist_ok=True)

# ================= 全局参数（米） =================
WT = 0.0025          # 木板厚 2.5mm
HOLE_M3 = 0.0016     # M3 螺丝孔半径
HOLE_M4 = 0.0022
R_BOLT_A1 = 0.0491   # ★用户实测：A-1 三叶板螺丝孔距85mm（等边三角形）→ 螺栓圆半径 85/√3≈49.07mm
CUP_R_POS = 0.045    # A-1 杯弧切口中心半径（杯轴心，120°分布）
A1_CUP_CUT = 0.034   # A-1 杯弧切口半径 φ68：切口穿透板缘成月牙口，杯锥面楔入自锁，口朝前

# ================= 颜色 =================
C = {
    "wood_light":  (0.898, 0.631, 0.361, 1),   # 浅橙木（F-1/B-1/A板/E-1/F-6）
    "wood_maroon": (0.480, 0.270, 0.250, 1),   # 红棕木（F-3/F-4/F-5）
    "wood_dark":   (0.330, 0.190, 0.175, 1),   # 深棕木（D-1/B-2）
    "white":       (0.910, 0.910, 0.910, 1),
    "grey":        (0.680, 0.706, 0.737, 1),   # 发射支架浅灰蓝
    "grey_dark":   (0.360, 0.373, 0.390, 1),
    "silver":      (0.780, 0.790, 0.805, 1),
    "yellow":      (0.906, 0.773, 0.271, 1),
    "lilac":       (0.780, 0.700, 0.820, 1),   # 电机减速箱前端淡紫
    "black":       (0.055, 0.055, 0.060, 1),
    "pcb_black":   (0.045, 0.045, 0.052, 1),
    "pcb_blue":    (0.070, 0.180, 0.400, 1),
    "copper":      (0.722, 0.451, 0.200, 1),
    "red":         (0.800, 0.100, 0.100, 1),
    "cup_red":     (0.640, 0.075, 0.110, 1),   # 纸杯深红
    "green":       (0.150, 0.550, 0.250, 1),
    "blue":        (0.150, 0.300, 0.750, 1),
    "cup_in":      (0.870, 0.865, 0.850, 1),   # 杯内银白
    "gold":        (0.850, 0.700, 0.350, 1),
}
ROUGH = {"wood_light": .7, "wood_maroon": .7, "wood_dark": .7, "white": .45, "grey": .5,
         "grey_dark": .55, "silver": .3, "yellow": .38, "lilac": .5, "black": .55,
         "pcb_black": .6, "pcb_blue": .6, "copper": .35, "red": .4, "cup_red": .45,
         "green": .5, "blue": .5, "cup_in": .5, "gold": .35}
METAL = {"silver": .9, "copper": .85, "gold": .8}

mats = {}
def mat(key):
    if key not in mats:
        m = bpy.data.materials.new("M_" + key)
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = C[key]
        b.inputs["Roughness"].default_value = ROUGH[key]
        if key in METAL:
            b.inputs["Metallic"].default_value = METAL[key]
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

def cyl(name, r, depth, loc, mkey, axis='Z', verts=48, smooth=True):
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

def prism_z(name, poly_xy, depth, mkey, z0=0.0):
    """多边形棱柱：poly_xy=[(x,y),...]，沿Z挤出 depth"""
    n = len(poly_xy)
    verts = [(x, y, z0) for x, y in poly_xy] + [(x, y, z0 + depth) for x, y in poly_xy]
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

def boolean_cut(base, cutter, delete=True):
    bpy.ops.object.select_all(action='DESELECT')
    base.select_set(True)
    bpy.context.view_layer.objects.active = base
    mod = base.modifiers.new("cut", 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    mod.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if delete:
        bpy.data.objects.remove(cutter, do_unlink=True)
    return base

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

def screw(name, r, length, loc, axis='Z'):
    """M3螺丝头+杆 简化件"""
    head = cyl(name + "_head", 0.0028, 0.0018, loc, "silver", axis)
    off = {'Z': (0, 0, -length / 2 - 0.0009), 'X': (-length / 2 - 0.0009, 0, 0),
           'Y': (0, -length / 2 - 0.0009, 0)}[axis]
    shaft = cyl(name + "_shaft", r, length,
                (loc[0] + off[0], loc[1] + off[1], loc[2] + off[2]), "silver", axis)
    parent_keep(shaft, head)
    return head

# 场景初始化
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1.0
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

# 灯光
def add_lights():
    bpy.ops.object.light_add(type='SUN', location=(2, -2, 3))
    sun = bpy.context.active_object
    sun.data.energy = 1.5
    sun.rotation_euler = (rad(50), rad(10), rad(30))
    bpy.ops.object.light_add(type='AREA', location=(-1, -1.5, 1.2))
    ar = bpy.context.active_object
    ar.data.energy = 25
    ar.data.size = 1.5
    ar.rotation_euler = (rad(55), 0, rad(-35))

add_lights()

# 地面
def add_ground(size=4):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, -0.001))
    o = bpy.context.active_object
    o.name = "Ground"
    m = bpy.data.materials.new("M_ground")
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.30, 0.32, 0.38, 1)
    o.data.materials.append(m)

# ================================================================
# 零件建模函数（每个返回 root empty，零件建在原点附近）
# ================================================================

def build_F1():
    """F-1 大圆盘 φ195×2.5 浅橙木（主体底板，着地；中心背面装白色法兰盘）"""
    root = empty("P_F1")
    d = cyl("F1_disc", 0.0975, WT, (0, 0, WT / 2), "wood_light", verts=96, smooth=False)
    h = cyl("F1_hole", 0.004, 0.02, (0, 0, WT / 2), "black")
    boolean_cut(d, h)
    for k in range(6):   # 法兰盘6孔
        a = 2 * pi * k / 6
        boolean_cut(d, cyl(f"F1_hole{k}", HOLE_M3, 0.02,
                           (0.0215 * cos(a), 0.0215 * sin(a), WT / 2), "black"))
    parent_keep(d, root)
    return root

def build_B1():
    """B-1 三瓣圆角三角板（浅橙木 R≈88，旋转组件底板：中央φ30孔穿电机减速箱
    +2长条槽 +3×钢珠轮安装孔R75 + 若干M3孔）"""
    root = empty("P_B1")
    poly = []
    for i in range(144):
        th = 2 * pi * i / 144
        r = 0.070 + 0.018 * cos(3 * th)      # 三瓣：瓣尖R88 / 凹处R52
        poly.append((r * cos(th), r * sin(th)))
    plate = prism_z("B1_plate", poly, WT, "wood_light")
    boolean_cut(plate, cyl("c_gb", 0.0155, 0.02, (0, 0, WT / 2), "black"))       # 中心φ31
    boolean_cut(plate, cyl("c_key", 0.005, 0.02, (0.019, 0, WT / 2), "black"))   # 键槽缺口
    for sx in (-1, 1):   # 两条长条槽（放射向）
        a = sx * rad(35)
        sl = box(f"c_slot{sx}", (0.004, 0.022, 0.02),
                 (0.030 * cos(a), 0.030 * sin(a), WT / 2), "black", rot=(0, 0, a + pi / 2))
        boolean_cut(plate, sl)
    for k in range(3):   # 钢珠轮安装孔 R75
        a = 2 * pi * k / 3 + pi / 6
        boolean_cut(plate, cyl(f"c_w{k}", HOLE_M3, 0.02,
                               (0.075 * cos(a), 0.075 * sin(a), WT / 2), "black"))
    for k in range(3):   # 锁F-3尼龙柱孔
        a = 2 * pi * k / 3 - pi / 6
        boolean_cut(plate, cyl(f"c_p{k}", HOLE_M3, 0.02,
                               (0.052 * cos(a), 0.052 * sin(a), WT / 2), "black"))
    parent_keep(plate, root)
    return root

def build_F3():
    """F-3 主体甲板（红棕木，前宽后窄蝠形≈195×150：前方两翼装按键/红外，
    中央φ26孔过电机，后部收窄）—— 平躺展示"""
    root = empty("P_F3")
    # 蝠形轮廓：单圈闭合，前方(+Y)两翼圆凸、翼间微凹、后部(-Y)收窄
    poly = []
    N = 96
    for i in range(N):
        th = 2 * pi * i / N
        r = (0.082 + 0.030 * max(0.0, sin(th)) * abs(cos(th))
             - 0.014 * max(0.0, -sin(th)))
        poly.append((r * cos(th), r * sin(th)))
    plate = prism_z("F3_plate", poly, WT, "wood_maroon")
    boolean_cut(plate, cyl("c_gb", 0.013, 0.02, (0, 0, WT / 2), "black"))        # 中央φ26
    for k in range(3):   # 锁B-1尼龙柱的过孔
        a = 2 * pi * k / 3 - pi / 6
        boolean_cut(plate, cyl(f"c_p{k}", 0.0022, 0.02,
                               (0.052 * cos(a), 0.052 * sin(a), WT / 2), "black"))
    for sx in (-1, 1):   # 前翼按键孔×2
        boolean_cut(plate, cyl(f"c_btn{sx}", 0.008, 0.02,
                               (0.065 * sx, 0.065, WT / 2), "black"))
    boolean_cut(plate, cyl("c_ir", 0.004, 0.02, (-0.075, 0.040, WT / 2), "black"))
    for sx in (-1, 1):   # 立板卡槽口
        sl = box(f"c_fs{sx}", (0.004, 0.014, 0.02), (0.055 * sx, 0.03, WT / 2), "black")
        boolean_cut(plate, sl)
    parent_keep(plate, root)
    return root

def build_F4():
    """F-4 立板×2（红棕木 ≈110×150 异形：右侧大缺口+顶部凸耳2孔+边缘互锁卡槽
    +若干孔）—— 平躺展示"""
    root = empty("P_F4")
    poly = [(-0.055, 0.075), (0.010, 0.075), (0.010, 0.058), (0.026, 0.058),   # 顶部凸耳
            (0.026, 0.075), (0.055, 0.075), (0.055, 0.030), (0.040, 0.030),    # 右侧大缺口
            (0.040, -0.010), (0.055, -0.010), (0.055, -0.030), (0.030, -0.030),
            (0.030, -0.075), (0.014, -0.075), (0.014, -0.062), (-0.014, -0.062),  # 底部卡槽
            (-0.014, -0.075), (-0.055, -0.075), (-0.055, -0.030), (-0.048, -0.030),
            (-0.048, 0.030), (-0.055, 0.030)]
    p = prism_z("F4_plate", poly, WT, "wood_maroon")
    for (hx, hy) in ((-0.040, 0.055), (-0.040, 0.020), (0.0, 0.040), (0.0, -0.040),
                     (-0.030, -0.055), (0.020, -0.055)):
        boolean_cut(p, cyl(f"c{hx}{hy}", HOLE_M3, 0.02, (hx, hy, WT / 2), "black"))
    parent_keep(p, root)
    return root

def build_F5():
    """F-5 摄像头托板（红棕木 ≈55×45 中央φ28孔）"""
    root = empty("P_F5")
    poly = [(-0.0275, 0.0225), (0.0275, 0.0225), (0.0275, -0.0225), (-0.0275, -0.0225)]
    p = prism_z("F5_plate", poly, WT, "wood_maroon")
    boolean_cut(p, cyl("c_cam", 0.014, 0.02, (0, 0, WT / 2), "black"))
    for sx in (-1, 1):
        boolean_cut(p, cyl(f"c{sx}", HOLE_M3, 0.02, (0.021 * sx, 0.016, WT / 2), "black"))
    parent_keep(p, root)
    return root

def build_F6():
    """F-6 前挡板（浅橙木 ≈95×65，底部两方缺口，顶缘小台阶）"""
    root = empty("P_F6")
    poly = [(-0.0475, 0.0325), (0.0475, 0.0325), (0.0475, -0.025),
            (0.022, -0.025), (0.022, -0.0325), (0.006, -0.0325),
            (0.006, -0.025), (-0.006, -0.025), (-0.006, -0.0325),
            (-0.022, -0.0325), (-0.022, -0.025), (-0.0475, -0.025)]
    p = prism_z("F6_plate", poly, WT, "wood_light")
    for sx in (-1, 1):
        boolean_cut(p, cyl(f"c{sx}", HOLE_M3, 0.02, (0.038 * sx, 0.020, WT / 2), "black"))
    parent_keep(p, root)
    return root

def build_bracket():
    """发射支架（浅灰工程塑料，手枪形≈130×45：后端双叉耳承拉勾凸轮
    + 中段平顶托激光/TT电机 + 前段下斜25°炮鼻）—— +X朝前"""
    root = empty("P_BRACKET")
    main = box("BR_main", (0.095, 0.030, 0.012), (-0.0175, 0, 0.006), "grey")
    parent_keep(main, root)
    nose = box("BR_nose", (0.062, 0.030, 0.012), (0.060, 0, -0.0095), "grey", rot=(0, rad(-25), 0))
    parent_keep(nose, root)
    tip = box("BR_tip", (0.010, 0.030, 0.008), (0.089, 0, -0.024), "grey", rot=(0, rad(-25), 0))
    parent_keep(tip, root)
    for s in (-1, 1):   # 后端双叉耳
        ear = box(f"BR_ear{s}", (0.005, 0.005, 0.042), (-0.050, s * 0.0165, 0.019), "grey")
        parent_keep(ear, root)
        eh = cyl(f"BR_earhole{s}", 0.0012, 0.012, (-0.050, s * 0.0165, 0.034), "black", axis='Y', verts=12)
        parent_keep(eh, root)
    for s in (-1, 1):   # 叉耳底座连接块
        base = box(f"BR_ebase{s}", (0.014, 0.005, 0.014), (-0.046, s * 0.0165, 0.007), "grey")
        parent_keep(base, root)
    # 中段顶面凸缘（挡TT电机前端）
    lip = box("BR_lip", (0.016, 0.030, 0.005), (0.024, 0, 0.0145), "grey")
    parent_keep(lip, root)
    return root

def build_F2():
    """F-2 拉勾垫片（浅橙木圆片 φ20×2.5）"""
    root = empty("P_F2")
    d = cyl("F2_disc", 0.010, WT, (0, 0, WT / 2), "wood_light", verts=48)
    boolean_cut(d, cyl("c", 0.0015, 0.02, (0, 0, WT / 2), "black"))
    bpy.ops.object.shade_auto_smooth(angle=rad(40))
    parent_keep(d, root)
    return root

def build_hook():
    """发射拉勾（黑色三臂凸轮 φ40：中心毂+3臂+臂端圆头）"""
    root = empty("P_HOOK")
    hub = cyl("HOOK_hub", 0.006, 0.008, (0, 0, 0), "black", verts=24)
    parent_keep(hub, root)
    for k in range(3):
        a = 2 * pi * k / 3
        arm = box(f"HOOK_arm{k}", (0.015, 0.009, 0.004),
                  (0.0125 * cos(a), 0.0125 * sin(a), 0), "black", rot=(0, 0, a))
        parent_keep(arm, root)
        knob = cyl(f"HOOK_knob{k}", 0.0045, 0.005,
                   (0.0195 * cos(a), 0.0195 * sin(a), 0), "black", verts=20)
        parent_keep(knob, root)
    return root

def build_flange():
    """法兰盘 60mm（白色星形：中心毂φ28 + 6辐射臂 + 6×M3.5孔R21.5 + 中心轴孔）"""
    root = empty("P_FLANGE")
    web = cyl("FL_web", 0.023, 0.003, (0, 0, 0.0015), "white", verts=64)
    hub = cyl("FL_hub", 0.014, 0.012, (0, 0, 0.006), "white", verts=48)
    arms = []
    for k in range(6):
        a = 2 * pi * k / 6
        arms.append(box(f"FL_arm{k}", (0.019, 0.011, 0.003),
                        (0.0225 * cos(a), 0.0225 * sin(a), 0.0015), "white", rot=(0, 0, a)))
    # 合并 web+hub+arms
    bpy.ops.object.select_all(action='DESELECT')
    web.select_set(True)
    hub.select_set(True)
    for a_ in arms:
        a_.select_set(True)
    bpy.context.view_layer.objects.active = web
    bpy.ops.object.join()
    fl = bpy.context.active_object
    fl.name = "FLANGE"
    # 切孔：中心 + 6螺丝孔
    cuts = [cyl("FL_ch", 0.0035, 0.03, (0, 0, 0.006), "black", verts=16)]
    for k in range(6):
        a = 2 * pi * k / 6
        cuts.append(cyl(f"FL_sh{k}", 0.0018, 0.02,
                        (0.0215 * cos(a), 0.0215 * sin(a), 0.003), "black", verts=12))
    for c in cuts:
        boolean_cut(fl, c)
    bpy.ops.object.shade_auto_smooth(angle=rad(40))
    parent_keep(fl, root)
    return root

def build_ballcaster():
    """钢珠轮（总高17mm：φ15黑色钢珠 + 银色U形支架 + 顶部安装孔耳）"""
    root = empty("P_BALLCASTER")
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.0075, segments=32, ring_count=16,
                                         location=(0, 0, 0.0075))
    ball = bpy.context.active_object
    ball.name = "BC_ball"
    _apply(ball)
    ball.data.materials.append(mat("black"))
    parent_keep(ball, root)
    for s in (-1, 1):
        side = box(f"BC_side{s}", (0.016, 0.002, 0.014), (0, s * 0.0085, 0.009), "silver")
        parent_keep(side, root)
    tab = box("BC_tab", (0.011, 0.019, 0.002), (0, 0, 0.016), "silver")
    parent_keep(tab, root)
    th = cyl("BC_hole", 0.0016, 0.01, (0, 0, 0.016), "black", verts=12)
    boolean_cut(tab, th)
    return root

def build_B2():
    """B-2 梯形斜撑板（深棕木，底宽120 顶宽70 高150，底边4×M3.6孔）—— 平躺展示"""
    root = empty("P_B2")
    poly = [(0, -0.060), (0.150, -0.035), (0.150, 0.035), (0, 0.060)]
    p = prism_z("B2_plate", poly, WT, "wood_dark")
    for hy in (-0.046, -0.026, 0.026, 0.046):
        boolean_cut(p, cyl(f"c{hy}", 0.0018, 0.02, (0.012, hy, WT / 2), "black"))
    parent_keep(p, root)
    return root

def build_D1():
    """D-1 圆板 φ180×2.5（旋转台背板，深棕木：中央键形孔装电机减速箱
    +4×电机法兰孔 +2×黑尼龙柱孔Y=±31 +底部2长条槽）"""
    root = empty("P_D1")
    d = cyl("D1_disc", 0.090, WT, (0, 0, WT / 2), "wood_dark", verts=96, smooth=False)
    boolean_cut(d, cyl("c_kh", 0.011, 0.02, (0, 0, WT / 2), "black"))            # 键形孔圆端
    boolean_cut(d, box("c_ks", (0.014, 0.006, 0.02), (0.017, 0, WT / 2), "black"))  # 键槽
    for sx in (-1, 1):
        for sy in (-1, 1):   # 电机法兰4孔
            boolean_cut(d, cyl(f"c_m{sx}{sy}", HOLE_M3, 0.02,
                               (0.0124 * sx, 0.0124 * sy, WT / 2), "black"))
    for sy in (-1, 1):       # 黑尼龙柱孔
        boolean_cut(d, cyl(f"c_p{sy}", HOLE_M3, 0.02, (0, 0.031 * sy, WT / 2), "black"))
    for sx in (-1, 1):       # 底部长条槽
        boolean_cut(d, box(f"c_s{sx}", (0.004, 0.020, 0.02),
                           (0.020 * sx, -0.055, WT / 2), "black"))
    parent_keep(d, root)
    return root

def build_A2():
    """A-2 圆板 φ180×2.5（旋转台前板，浅橙木：中央φ23孔过减速箱前端
    +2×黑尼龙柱孔Y=±31 +底部2小孔）"""
    root = empty("P_A2")
    d = cyl("A2_disc", 0.090, WT, (0, 0, WT / 2), "wood_light", verts=96, smooth=False)
    boolean_cut(d, cyl("c_c", 0.0115, 0.02, (0, 0, WT / 2), "black"))
    for sy in (-1, 1):
        boolean_cut(d, cyl(f"c_p{sy}", HOLE_M3, 0.02, (0, 0.031 * sy, WT / 2), "black"))
    for sx in (-1, 1):
        boolean_cut(d, cyl(f"c_s{sx}", HOLE_M3, 0.02, (0.020 * sx, -0.055, WT / 2), "black"))
    parent_keep(d, root)
    return root

def build_E1():
    """E-1 圆板 φ110×2.5（浅橙木，背面装白法兰盘：中心孔φ7 + 6×法兰螺丝孔R21.5
    + 3×黑尼龙柱孔R=49.1★孔距85mm）"""
    root = empty("P_E1")
    d = cyl("E1_disc", 0.055, WT, (0, 0, WT / 2), "wood_light", verts=72, smooth=False)
    boolean_cut(d, cyl("c_c", 0.0035, 0.02, (0, 0, WT / 2), "black"))
    for k in range(6):       # 法兰盘螺丝过孔
        a = 2 * pi * k / 6
        boolean_cut(d, cyl(f"c_f{k}", 0.0018, 0.02,
                           (0.0215 * cos(a), 0.0215 * sin(a), WT / 2), "black"))
    for k in range(3):       # 尼龙柱孔（★R=49.1）
        a = 2 * pi * k / 3 + rad(30)
        boolean_cut(d, cyl(f"c_p{k}", HOLE_M3, 0.02,
                           (R_BOLT_A1 * cos(a), R_BOLT_A1 * sin(a), WT / 2), "black"))
    parent_keep(d, root)
    return root

def build_A1():
    """A-1 纸杯卡板（浅橙木 镖形φ156：盘缘3个月牙弧口R34@R45@120°，杯锥面楔入自锁口朝前
    + 3×黑尼龙柱孔R=49.1★孔距85mm 位于臂中 + 中心φ5孔锁电机轴端）"""
    root = empty("P_A1")
    # 镖形轮廓：R78 圆盘减去3个穿透板缘的弧切口（月牙口）
    R_DISC = 0.078
    poly = []
    N = 168
    for i in range(N):
        th = 2 * pi * i / N
        ux, uy = cos(th), sin(th)
        r_pt = R_DISC                     # 默认盘缘
        for k in range(3):                # 盘缘点落入切口圆内 → 收缩到切口圆近交点
            a = rad(90) + 2 * pi * k / 3
            cxk, cyk = CUP_R_POS * cos(a), CUP_R_POS * sin(a)
            dx, dy = R_DISC * ux - cxk, R_DISC * uy - cyk
            if dx * dx + dy * dy < A1_CUP_CUT ** 2:
                b = cxk * ux + cyk * uy
                disc = A1_CUP_CUT ** 2 - (cxk * cxk + cyk * cyk - b * b)
                if disc > 0:
                    t = b - disc ** 0.5   # 射线进入切口的近交点
                    if 0 < t < r_pt:
                        r_pt = t
        poly.append((r_pt * ux, r_pt * uy))
    d = prism_z("A1_plate", poly, WT, "wood_light")
    boolean_cut(d, cyl("c_hub", 0.0025, 0.02, (0, 0, WT / 2), "black"))   # 中心φ5锁轴端
    for k in range(3):   # 黑尼龙柱孔（★R=49.1，位于臂中，与切口错开60°）
        a = rad(30) + 2 * pi * k / 3
        boolean_cut(d, cyl(f"c_m{k}", HOLE_M3, 0.02,
                           (R_BOLT_A1 * cos(a), R_BOLT_A1 * sin(a), WT / 2), "black"))
    parent_keep(d, root)
    return root

def build_standoff(color="white"):
    """尼龙柱 M3*22 双通圆柱（白/黑两版，两端小凸缘）"""
    root = empty("P_STANDOFF_" + color)
    body = cyl("SO_body", 0.0024, 0.022, (0, 0, 0.011), color, verts=24)
    parent_keep(body, root)
    for z in (0.001, 0.021):
        f = cyl(f"SO_f{z}", 0.0029, 0.002, (0, 0, z), color, verts=24)
        parent_keep(f, root)
    return root

def build_cup():
    """纸杯（口φ75 底φ55 长85，口朝+X：红色外壁+银白内壁 + 内底二维码白片）"""
    root = empty("P_CUP")
    L = 0.085
    cx = L / 2
    shell = cone("CUP_out", 0.0275, 0.0375, L, (cx, 0, 0), "cup_red", rot=(0, rad(90), 0))
    boolean_cut(shell, cone("c1", 0.0262, 0.0362, L, (cx + 0.0015, 0, 0),
                            "black", rot=(0, rad(90), 0)))
    parent_keep(shell, root)
    liner = cone("CUP_in", 0.0262, 0.0362, L, (cx + 0.0015, 0, 0), "cup_in", rot=(0, rad(90), 0))
    boolean_cut(liner, cone("c2", 0.0250, 0.0350, L, (cx + 0.0015, 0, 0),
                            "black", rot=(0, rad(90), 0)))
    parent_keep(liner, root)
    for o in (shell, liner):   # 布尔后按角度平滑，消除杯口伪影
        bpy.ops.object.select_all(action='DESELECT')
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.shade_auto_smooth(angle=rad(40))
    # 二维码白片贴杯内底 + 黑色方块图案
    tag = cyl("CUP_tag", 0.024, 0.0012, (0.0022, 0, 0), "white", axis='X', verts=48)
    parent_keep(tag, root)
    for i in range(3):
        for j in range(3):
            if (i + j) % 2 == 0 or (i == 1 and j == 1):
                bx = box(f"CUP_px{i}{j}", (0.0008, 0.009, 0.009),
                         (0.0031, -0.010 + j * 0.010, -0.010 + i * 0.010), "black")
                parent_keep(bx, root)
    return root

# ---------------- 电子件 ----------------

def build_tt_motor():
    """TT减速电机（发射电机14.9rpm，轴朝+X）：黄色齿轮箱+银电机筒+黑尾盖+白色摩擦轮"""
    root = empty("P_TT_MOTOR")
    gear = box("TT_gear", (0.036, 0.028, 0.022), (0, 0, 0), "yellow")            # 黄齿轮箱
    parent_keep(gear, root)
    body = cyl("TT_body", 0.0125, 0.030, (-0.033, 0, 0), "silver", axis='X', verts=32)
    parent_keep(body, root)
    cap = cyl("TT_cap", 0.0105, 0.005, (-0.0505, 0, 0), "grey_dark", axis='X', verts=32)
    parent_keep(cap, root)
    shaft = cyl("TT_shaft", 0.0015, 0.012, (0.024, 0, 0), "silver", axis='X', verts=16)
    parent_keep(shaft, root)
    roller = cyl("TT_roller", 0.0070, 0.026, (0.031, 0, 0), "cup_in", axis='X', verts=32)
    parent_keep(roller, root)                                                    # 白色摩擦轮
    for s in (-1, 1):    # 齿轮箱安装耳
        ear = box(f"TT_ear{s}", (0.014, 0.004, 0.006), (0.008, s * 0.016, -0.014), "yellow")
        parent_keep(ear, root)
    for i, (dx, c) in enumerate(((-0.004, "red"), (0.004, "black"))):
        w = cyl(f"TT_wire{i}", 0.0009, 0.05, (-0.045, 0.014 + dx * 2, -0.005), c, axis='X', verts=8)
        w.location = (-0.045, 0.016 + dx, -0.005)
        parent_keep(w, root)
    return root

def build_gear_motor():
    """金属轴减速电机（旋转电机4.9rpm，轴朝+X）：银色金属筒+淡紫减速箱端盖+双法兰金属轴"""
    root = empty("P_GM")
    body = cyl("GM_body", 0.0125, 0.036, (-0.020, 0, 0), "silver", axis='X', verts=48)
    parent_keep(body, root)
    cap = cyl("GM_cap", 0.0125, 0.004, (-0.040, 0, 0), "grey_dark", axis='X', verts=48)
    parent_keep(cap, root)
    flange = cyl("GM_flange", 0.0113, 0.008, (0.002, 0, 0), "lilac", axis='X', verts=48)
    parent_keep(flange, root)                                                    # 减速箱端盖（贴D-1）
    nose = cyl("GM_nose", 0.0055, 0.004, (0.008, 0, 0), "lilac", axis='X', verts=24)
    parent_keep(nose, root)
    shaft = cyl("GM_shaft", 0.002, 0.016, (0.018, 0, 0), "silver", axis='X', verts=16)
    parent_keep(shaft, root)
    collar = cyl("GM_collar", 0.005, 0.004, (0.022, 0, 0), "silver", axis='X', verts=24)
    parent_keep(collar, root)                                                    # 双法兰轴环
    for i, (dy, c) in enumerate(((0.004, "red"), (-0.004, "black"))):
        w = cyl(f"GM_wire{i}", 0.0009, 0.04, (-0.030, dy, -0.016), c, axis='Y', verts=8)
        parent_keep(w, root)
    return root

def build_servo():
    """数字离合舵机（黑壳带耳 ≈41×20×40，轴向输出：+X 前端白色舵盘轴）"""
    root = empty("P_SERVO")
    body = box("SV_body", (0.041, 0.020, 0.040), (0, 0, 0.020), "black")
    parent_keep(body, root)
    for s in (-1, 1):    # 安装耳
        ear = box(f"SV_ear{s}", (0.051, 0.0025, 0.004), (0, s * 0.0110, 0.033), "black")
        parent_keep(ear, root)
    boss = cyl("SV_boss", 0.0075, 0.005, (0.023, 0, 0.018), "black", axis='X', verts=24)
    parent_keep(boss, root)                                                      # 轴座凸台
    axle = cyl("SV_axle", 0.0022, 0.008, (0.0295, 0, 0.018), "cup_in", axis='X', verts=16)
    parent_keep(axle, root)                                                      # 白色输出轴
    for i, (dx, c) in enumerate(((0.005, "red"), (0.0, "black"), (-0.005, "yellow"))):
        w = cyl(f"SV_wire{i}", 0.0009, 0.06, (-0.018, -0.028, 0.006), c, axis='Y', verts=8)
        w.location = (-0.018 + dx, -0.028, 0.006)
        parent_keep(w, root)
    return root

def build_laser():
    """激光发射模块（黑PCB 22×11 + 铜色激光头φ6×11 + 3针）"""
    root = empty("P_LASER")
    pcb = box("LS_pcb", (0.022, 0.011, 0.0016), (0, 0, 0), "pcb_black")
    parent_keep(pcb, root)
    head = cyl("LS_head", 0.003, 0.011, (-0.006, 0, 0.0065), "copper", axis='X', verts=24)
    parent_keep(head, root)
    mount = box("LS_mount", (0.006, 0.008, 0.005), (-0.006, 0, 0.003), "black")
    parent_keep(mount, root)
    for i, dx in enumerate((0.008, 0.005, 0.002)):
        p = cyl(f"LS_pin{i}", 0.0005, 0.008, (dx, 0.006, -0.005), "gold", verts=8)
        parent_keep(p, root)
    return root

def build_ir():
    """红外模块（蓝PCB 32×15 + 蓝电位器 + 黑/白红外管对 + 3针）"""
    root = empty("P_IR")
    pcb = box("IR_pcb", (0.032, 0.015, 0.0016), (0, 0, 0), "pcb_blue")
    parent_keep(pcb, root)
    pot = cyl("IR_pot", 0.0035, 0.005, (0.004, 0.003, 0.0033), "blue", verts=20)
    parent_keep(pot, root)
    e1 = cyl("IR_emit", 0.0025, 0.004, (-0.012, -0.004, 0.0035), "black", verts=16)
    e2 = cyl("IR_recv", 0.0025, 0.004, (-0.012, 0.004, 0.0035), "white", verts=16)
    parent_keep(e1, root); parent_keep(e2, root)
    ic = box("IR_ic", (0.006, 0.005, 0.002), (0.008, -0.003, 0.0018), "black")
    parent_keep(ic, root)
    for i, dx in enumerate((0.013, 0.010, 0.007)):
        p = cyl(f"IR_pin{i}", 0.0005, 0.008, (dx, -0.009, -0.005), "gold", verts=8)
        parent_keep(p, root)
    return root

def build_button():
    """按键模块（黑PCB 25×25 + 红色大按键帽φ12 + 3针 + 对角安装孔）"""
    root = empty("P_BTN")
    pcb = box("BTN_pcb", (0.025, 0.025, 0.0016), (0, 0, 0), "pcb_black")
    parent_keep(pcb, root)
    cap = cyl("BTN_cap", 0.006, 0.007, (0, 0, 0.0043), "red", verts=32)
    parent_keep(cap, root)
    for sx in (-1, 1):
        for sy in (-1, 1):
            h = cyl(f"BTN_hole{sx}{sy}", 0.0016, 0.01, (sx * 0.010, sy * 0.010, 0), "black", verts=12)
    for i, dy in enumerate((0.006, 0.000, -0.006)):
        p = cyl(f"BTN_pin{i}", 0.0005, 0.008, (0.0135, dy, -0.005), "gold", verts=8)
        parent_keep(p, root)
    return root

def build_camera():
    """USB摄像头（方形黑PCB 38×38 + M12镜头筒φ14×20 朝上 + 排线）"""
    root = empty("P_CAM")
    pcb = box("CAM_pcb", (0.038, 0.038, 0.0016), (0, 0, 0), "pcb_black")
    parent_keep(pcb, root)
    lens = cyl("CAM_lens", 0.0075, 0.020, (0, 0, 0.011), "black", verts=32)
    parent_keep(lens, root)
    ring = cyl("CAM_ring", 0.0095, 0.003, (0, 0, 0.003), "black", verts=32)
    parent_keep(ring, root)
    cable = box("CAM_cable", (0.006, 0.030, 0.0015), (0, -0.030, 0), "black")
    parent_keep(cable, root)
    return root

def build_battery():
    """四节5号电池盒（灰盒 62×32×18，2×2电池仓银色顶可见 + 端面开关 + 红黑引线）"""
    root = empty("P_BAT4")
    body = box("BAT_body", (0.062, 0.032, 0.018), (0, 0, 0.009), "grey")
    parent_keep(body, root)
    for sx in (-1, 1):          # 2×2 电池顶（银）
        for sy in (-1, 1):
            cap = cyl(f"BAT_cell{sx}{sy}", 0.0072, 0.002,
                      (sx * 0.013, sy * 0.008, 0.0185), "silver", verts=20)
            parent_keep(cap, root)
    sw = box("BAT_sw", (0.006, 0.010, 0.006), (0.0325, 0, 0.012), "grey_dark")
    parent_keep(sw, root)
    for i, (dy, c) in enumerate(((-0.004, "red"), (-0.011, "black"))):
        w = cyl(f"BAT_wire{i}", 0.001, 0.04, (-0.033, dy, 0.009), c, axis='X', verts=8)
        parent_keep(w, root)
    return root

def build_battery2():
    """两节5号电池盒（黑盒 57×31×17，顶部淡紫拨动开关，红黑杜邦线）——旋转台电机电源"""
    root = empty("P_BAT2")
    body = box("BAT2_body", (0.057, 0.031, 0.017), (0, 0, 0.0085), "black")
    parent_keep(body, root)
    swb = box("BAT2_swbase", (0.014, 0.012, 0.003), (-0.020, 0, 0.0185), "black")
    parent_keep(swb, root)
    sw = box("BAT2_slider", (0.010, 0.007, 0.006), (-0.020, 0, 0.022), "lilac")
    parent_keep(sw, root)
    for i, (dy, c) in enumerate(((0.004, "red"), (-0.004, "black"))):
        w = cyl(f"BAT2_wire{i}", 0.001, 0.04, (0.0305, dy, 0.008), c, axis='X', verts=8)
        parent_keep(w, root)
    return root

def build_breadboard():
    """背胶面包板 400孔（83×55×9 白壳：两侧红蓝电源线 + 中央凹槽）"""
    root = empty("P_BB")
    body = box("BB_body", (0.083, 0.055, 0.009), (0, 0, 0.0045), "white")
    parent_keep(body, root)
    top = box("BB_top", (0.081, 0.053, 0.0015), (0, 0, 0.0095), "cup_in")
    parent_keep(top, root)
    canyon = box("BB_canyon", (0.081, 0.003, 0.001), (0, 0, 0.0102), "grey_dark")
    parent_keep(canyon, root)
    for sx in (-1, 1):          # 两侧电源线：红/蓝
        for k, c in enumerate(("red", "blue")):
            ln = box(f"BB_line{sx}{c}", (0.078, 0.0022, 0.0006),
                     (0, sx * (0.019 + k * 0.005), 0.0103), c)
            parent_keep(ln, root)
    return root

def build_mainboard():
    """主控板 UNO MOTORA（黑PCB 68.6×53.4 + Type-C + DC座 + 三色排针 + 电机端子 + 蜂鸣器 + 板载按键）"""
    root = empty("P_MB")
    pcb = box("MB_pcb", (0.0686, 0.0534, 0.0016), (0, 0, 0), "pcb_black")
    parent_keep(pcb, root)
    # 四角安装孔
    for sx in (-1, 1):
        for sy in (-1, 1):
            h = cyl(f"MB_hole{sx}{sy}", 0.0018, 0.01, (sx * 0.031, sy * 0.0235, 0), "black", verts=12)
    # Type-C（左上边缘）
    tc = box("MB_typec", (0.009, 0.008, 0.003), (-0.033, 0.018, 0.0015), "silver")
    parent_keep(tc, root)
    # DC 电源座（左下）
    dc = box("MB_dc", (0.014, 0.009, 0.011), (-0.026, -0.022, 0.0055), "black")
    parent_keep(dc, root)
    # 顶部三色排针（蓝2P+红/绿A0-5+红/黄D0-13 简化色条×小柱阵）
    strips = [("blue", -0.012, 0.010), ("red", 0.004, 0.014), ("green", 0.020, 0.012), ("red", 0.034, 0.014)]
    for c, x0, ln in strips:
        s = box(f"MB_strip{c}{x0}", (ln, 0.005, 0.011), (x0, 0.024, 0.0055), c)
        parent_keep(s, root)
    # 右侧黑色电机接线端子 2×3P
    for k in range(2):
        t = box(f"MB_terminal{k}", (0.008, 0.022, 0.012), (0.036, 0.004 - k * 0.014, 0.006), "green")
        parent_keep(t, root)
    # 芯片区
    chip = box("MB_chip", (0.014, 0.014, 0.002), (-0.004, 0.002, 0.0018), "black")
    parent_keep(chip, root)
    # 蜂鸣器（黑圆柱 φ12×7 右下 D12）
    bz = cyl("MB_buzzer", 0.006, 0.007, (0.020, -0.014, 0.0035), "black", verts=24)
    parent_keep(bz, root)
    # 板载红色按键（右下角 D13）
    btn = box("MB_btn", (0.006, 0.006, 0.005), (0.028, -0.020, 0.0025), "red")
    parent_keep(btn, root)
    return root

# ================================================================
# 网格全家福布局 + 渲染
# ================================================================

def text_label(name, body, loc):
    curve = bpy.data.curves.new(name, type='FONT')
    curve.body = body
    curve.size = 0.020
    curve.align_x = 'CENTER'
    o = bpy.data.objects.new(name, curve)
    o.location = loc
    o.rotation_euler = (rad(62), 0, 0)   # 立起朝向相机
    bpy.context.scene.collection.objects.link(o)
    m = bpy.data.materials.new("M_label_" + name)
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.95, 0.95, 0.98, 1)
    o.data.materials.append(m)
    return o

def lay_out(builders, cols, dx=0.26, dy=0.34):
    """builders: [(fn, label), ...] 网格摆放，返回网格中心与包围尺寸"""
    rows = (len(builders) + cols - 1) // cols
    for idx, (fn, label) in enumerate(builders):
        r, c = divmod(idx, cols)
        tile = empty("tile_" + label, loc=(c * dx, -r * dy, 0))
        part = fn()
        part.parent = tile   # 零件建在原点，直接挂到 tile 即可跟随位移
        text_label("lbl_" + label, label, (c * dx, -r * dy - dy * 0.42, 0.002))
    cx = (cols - 1) * dx / 2
    cy = -(rows - 1) * dy / 2
    return cx, cy, cols * dx, rows * dy

def render_shot(cx, cy, w, h, path, res=(1600, 1000)):
    scene.render.resolution_x, scene.render.resolution_y = res
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = max(w, h * res[0] / res[1]) * 1.12
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    dist = 2.0
    direction = Vector((0, 0.52, -0.30))   # 视线方向：从相机指向目标（略俯视）
    direction.normalize()
    cam.location = Vector((cx, cy, 0.03)) - direction * dist
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    scene.camera = cam
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)

def main():
    add_ground(size=4.5)
    struct_parts = [
        (build_F1, "F-1 base disc"),
        (build_B1, "B-1 rotor plate"),
        (build_F3, "F-3 top plate"),
        (build_F4, "F-4 upright"),
        (build_F5, "F-5 cam plate"),
        (build_F6, "F-6 front panel"),
        (build_bracket, "Launch bracket"),
        (build_F2, "F-2 washer"),
        (build_hook, "Release hook"),
        (build_flange, "Flange 60"),
        (build_ballcaster, "Ball caster"),
        (build_B2, "B-2 support"),
        (build_D1, "D-1 disc 180"),
        (build_A2, "A-2 disc 180"),
        (build_E1, "E-1 disc 110"),
        (build_A1, "A-1 3-leaf"),
        (lambda: build_standoff("white"), "Standoff M3x22"),
        (build_cup, "Paper cup+QR"),
    ]
    elec_parts = [
        (build_tt_motor, "TT motor"),
        (build_gear_motor, "Gear motor"),
        (build_servo, "Servo"),
        (build_laser, "Laser module"),
        (build_ir, "IR module"),
        (build_button, "Button module"),
        (build_camera, "USB camera"),
        (build_battery, "Battery 4xAA"),
        (build_battery2, "Battery 2xAA"),
        (build_mainboard, "Mainboard UNO"),
        (build_breadboard, "Breadboard"),
    ]
    # ---- 图1：结构件 5列4行 ----
    cx, cy, w, h = lay_out(struct_parts, cols=5)
    stray = [ob.name for ob in bpy.data.objects
             if ob.type == 'MESH' and ob.parent is None and ob.name != "Ground"]
    if stray:
        print("STRAY TOP-LEVEL MESH:", stray)
    render_shot(cx, cy - 0.02, w, h, os.path.join(REN, "parts_struct.png"))
    # 清场
    for ob in list(bpy.data.objects):
        if ob.name != "Ground" and ob.type != 'LIGHT':
            bpy.data.objects.remove(ob, do_unlink=True)
    # ---- 图2：电子件 4列3行 ----
    cx, cy, w, h = lay_out(elec_parts, cols=4)
    render_shot(cx, cy - 0.02, w, h, os.path.join(REN, "parts_elec.png"), res=(1400, 1100))
    print("PARTS BUILD DONE")

main()
