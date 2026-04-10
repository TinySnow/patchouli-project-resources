"""
学海计划 XMind 批量渲染脚本 v3
- 根节点居中，一级主题分列左右两侧（前半右，后半左）
- 只展开目标节点所在路径，其他分支折叠
"""

import json, os, zipfile, re, math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.path as mpath

# ─── 配置 ────────────────────────────────────────────────────────────────────
INPUT_FILE  = "学海计划.xmind"
OUTPUT_DIR  = "you_are_here"
DPI         = 120
FONT_NAME   = "Microsoft YaHei"

BRANCH_COLORS = [
    "#E07020", "#2B7FC4", "#3AAB5A",
    "#C43B6E", "#8B5CF6", "#0891B2", "#D97706",
    "#E53E3E", "#38A169", "#B45309", "#0E7490",
]
ROOT_BG = "#F0F4F8"; ROOT_FG = "#1E293B"
NODE_BG = "#FFFFFF"; NODE_FG = "#334155"
HERE_BG = "#FEF3C7"; HERE_FG = "#92400E"; HERE_BD = "#F59E0B"
TGT_FG  = "#1E293B"

X_GAP    = 2.2    # 水平间距
Y_MIN_SH = 0.55   # 每个节点最小占用高度
MAX_WRAP = 12     # 自动折行字数
Y_UNIT   = 0.42   # 图片高度换算系数
# ─────────────────────────────────────────────────────────────────────────────


def load_xmind(path):
    with zipfile.ZipFile(path) as z:
        with z.open("content.json") as f:
            data = json.load(f)
    sheets = []
    for sheet in data:
        title = sheet.get("title", "画布")
        root  = sheet.get("rootTopic", {})
        sheets.append((title, root))
    return sheets


def get_children(node):
    return node.get("children", {}).get("attached", [])


def wrap_text(text, max_c=MAX_WRAP):
    if len(text) <= max_c:
        return text
    lines = []
    while len(text) > max_c:
        lines.append(text[:max_c])
        text = text[max_c:]
    if text:
        lines.append(text)
    return "\n".join(lines)


# ─── 预先计算每个一级子节点分配到哪一侧 ────────────────────────────────────────
def compute_side_map(root_json):
    """返回 {branch_title: 'right'|'left'}"""
    children = get_children(root_json)
    n = len(children)
    # 一级主题 ≤4 个时全部放右侧
    if n <= 4:
        return {c.get("title", ""): "right" for c in children}
    right_count = math.ceil(n / 2)
    side_map = {}
    for i, c in enumerate(children):
        title = c.get("title", "")
        side_map[title] = "right" if i < right_count else "left"
    return side_map


# ─── 收集所有节点路径 ─────────────────────────────────────────────────────────
def collect_all_paths(node, path=None, out=None):
    if out is None:
        out = []
    title = node.get("title", "")
    if "You are here" in title:
        return out
    path = (path or []) + [title]
    out.append(list(path))
    for c in get_children(node):
        collect_all_paths(c, path, out)
    return out


# ─── 构建折叠树（带 side 信息） ──────────────────────────────────────────────
def build_collapsed_tree(node, depth, bc, side, path_set, target_title, here_label, side_map):
    title    = node.get("title", "")
    is_here  = "You are here" in title
    is_target = (title == target_title)
    on_path  = title in path_set

    x = float(depth) * X_GAP if side == "right" else -float(depth) * X_GAP

    t = {
        "title": title, "depth": depth, "bc": bc, "side": side,
        "is_here": is_here, "is_target": is_target, "on_path": on_path,
        "x": x, "y": 0.0, "kids": [], "sh": Y_MIN_SH,
    }

    # 根据折行后的实际行数估算最小占用高度，防止高盒子与邻居重叠
    label_lines = len(wrap_text(title).split("\n"))
    t["sh"] = max(Y_MIN_SH, label_lines * 0.38)

    children = get_children(node)

    if depth == 0:
        for i, c in enumerate(children):
            c_title = c.get("title", "")
            if "You are here" in c_title:   # 过滤原有标记
                continue
            c_bc   = BRANCH_COLORS[i % len(BRANCH_COLORS)]
            c_side = side_map.get(c_title, "right")
            child_t = build_collapsed_tree(c, 1, c_bc, c_side, path_set, target_title, here_label, side_map)
            t["kids"].append(child_t)

    elif on_path:
        for c in children:
            if "You are here" in c.get("title", ""):  # 过滤原有标记
                continue
            child_t = build_collapsed_tree(c, depth + 1, bc, side, path_set, target_title, here_label, side_map)
            t["kids"].append(child_t)

        # 目标节点追加新的 "You are here"
        if is_target:
            t["kids"].append({
                "title": here_label, "depth": depth + 1, "bc": bc, "side": side,
                "is_here": True, "is_target": False, "on_path": False,
                "x": (float(depth + 1) * X_GAP if side == "right" else -float(depth + 1) * X_GAP),
                "y": 0.0, "kids": [], "sh": Y_MIN_SH,
            })

    # 子树高度取所有子节点之和
    if t["kids"]:
        t["sh"] = sum(max(k["sh"], Y_MIN_SH) for k in t["kids"])

    return t


# ─── Y 坐标分配（左右两侧独立，各自以 y=0 为中心） ──────────────────────────
def _assign_y_side(kids):
    """对一组同侧子节点分配 y 坐标，返回中心 y。"""
    if not kids:
        return 0.0
    cursor = 0.0
    for k in kids:
        _assign_y_rec(k, cursor)
        cursor += max(k["sh"], Y_MIN_SH)
    return (kids[0]["y"] + kids[-1]["y"]) / 2.0


def _assign_y_rec(t, top):
    kids = t["kids"]
    if not kids:
        t["y"] = top
        return
    cursor = top
    for k in kids:
        _assign_y_rec(k, cursor)
        cursor += max(k["sh"], Y_MIN_SH)
    t["y"] = (kids[0]["y"] + kids[-1]["y"]) / 2.0


def _shift_y(t, dy):
    t["y"] += dy
    for k in t["kids"]:
        _shift_y(k, dy)


def assign_y_two_sides(root_t):
    right_kids = [k for k in root_t["kids"] if k["side"] == "right"]
    left_kids  = [k for k in root_t["kids"] if k["side"] == "left"]

    r_center = _assign_y_side(right_kids)
    l_center = _assign_y_side(left_kids)

    # 各自以 y=0 为中心
    for k in right_kids:
        _shift_y(k, -r_center)
    for k in left_kids:
        _shift_y(k, -l_center)

    root_t["y"] = 0.0


# ─── 拍平树 ──────────────────────────────────────────────────────────────────
def flatten_tree(t, out=None):
    if out is None:
        out = []
    out.append(t)
    for k in t["kids"]:
        flatten_tree(k, out)
    return out


# ─── 盒子尺寸 ────────────────────────────────────────────────────────────────
def node_box_size(n):
    depth = n["depth"]
    title = n["title"]
    fs    = 14 if depth == 0 else (10 if depth == 1 else 9)
    label = wrap_text(title)
    lines = label.split("\n")
    cw = fs * 0.013; ch = fs * 0.024
    bw = max(max(len(l) for l in lines) * cw + 0.28, 0.6)
    bh = max(len(lines) * ch + 0.18, 0.34)
    if depth == 0:
        bw = max(bw, 1.1); bh = max(bh, 0.50)
    return bw, bh, label


def precompute_boxes(nodes):
    for n in nodes:
        bw, bh, label = node_box_size(n)
        n["bw"] = bw; n["bh"] = bh; n["label"] = label


# ─── 绘制边 ──────────────────────────────────────────────────────────────────
def draw_edges(ax, t):
    px, py = t["x"], t["y"]
    p_bw   = t.get("bw", 0.6)

    for k in t["kids"]:
        cx, cy = k["x"], k["y"]
        k_bw   = k.get("bw", 0.6)
        side   = k["side"]

        on_path = k["on_path"] or k["is_here"] or k["is_target"]
        color = k["bc"]
        lw    = 2.2 if on_path else 1.0
        alpha = 1.0 if on_path else 0.35

        if side == "right":
            # 父节点右边框 → 子节点左边框
            x_start = px + p_bw / 2
            x_end   = cx - k_bw / 2 - 0.06
        else:
            # 父节点左边框 → 子节点右边框
            x_start = px - p_bw / 2
            x_end   = cx + k_bw / 2 + 0.06

        ctrl_ratio = 0.3
        cp1_x = x_start + (x_end - x_start) * ctrl_ratio
        cp2_x = x_end   - (x_end - x_start) * ctrl_ratio

        verts = [(x_start, py), (cp1_x, py), (cp2_x, cy), (x_end, cy)]
        codes = [mpath.Path.MOVETO, mpath.Path.CURVE4,
                 mpath.Path.CURVE4, mpath.Path.CURVE4]
        ax.add_patch(mpatches.PathPatch(
            mpath.Path(verts, codes),
            facecolor="none", edgecolor=color,
            lw=lw, alpha=alpha, zorder=1
        ))
        draw_edges(ax, k)


# ─── 绘制节点 ────────────────────────────────────────────────────────────────
def draw_node(ax, n):
    from matplotlib.patches import FancyBboxPatch
    x, y      = n["x"], n["y"]
    depth     = n["depth"]
    is_here   = n["is_here"]
    is_target = n["is_target"]
    on_path   = n["on_path"]
    bc        = n["bc"]
    bw, bh, label = n["bw"], n["bh"], n["label"]

    fs = 14 if depth == 0 else (10 if depth == 1 else 9)
    fw = "bold" if (depth <= 1 or is_target or on_path) else "normal"
    fa = 1.0 if (on_path or depth == 0 or is_here or is_target) else 0.85

    if is_here:
        bg, fg, bd, lw_box = HERE_BG, HERE_FG, HERE_BD, 1.8
    elif depth == 0:
        bg, fg, bd, lw_box = ROOT_BG, ROOT_FG, "#94A3B8", 1.8
    elif is_target:
        bg = bc + "22"; fg, bd, lw_box = TGT_FG, bc, 2.5
    elif on_path:
        bg, fg, bd, lw_box = NODE_BG, NODE_FG, bc + "88", 1.4
    else:
        bg = bc + "30"   # 淡底色（原 18，提亮）
        fg = bc          # 文字用分支色
        bd = bc + "88"   # 边框更明显（原 55）
        lw_box = 1.0     # 边框略粗（原 0.8）

    if is_target:
        ax.add_patch(FancyBboxPatch(
            (x - bw/2 - 0.07, y - bh/2 - 0.07), bw + 0.14, bh + 0.14,
            boxstyle="round,pad=0.05", facecolor="none", edgecolor=bc,
            linewidth=3.0, alpha=0.3, zorder=1
        ))

    ax.add_patch(FancyBboxPatch(
        (x - bw/2, y - bh/2), bw, bh,
        boxstyle="round,pad=0.05",
        facecolor=bg, edgecolor=bd, linewidth=lw_box, zorder=2
    ))
    ax.text(x, y, label, ha="center", va="center",
            fontsize=fs, color=fg, fontfamily=FONT_NAME,
            fontweight=fw, alpha=fa, zorder=3)


# ─── 渲染单张图 ───────────────────────────────────────────────────────────────
def render(root_json, target_path, side_map, out_path):
    path_set     = set(target_path)
    target_title = target_path[-1]

    # 根据目标节点所在侧确定 here_label 方向
    branch_title = target_path[1] if len(target_path) >= 2 else ""
    target_side  = side_map.get(branch_title, "right")
    here_label   = "<- You are here" if target_side == "right" else "You are here ->"

    layout = build_collapsed_tree(
        root_json, 0, "#888", "right",
        path_set, target_title, here_label, side_map
    )
    assign_y_two_sides(layout)
    nodes = flatten_tree(layout)
    precompute_boxes(nodes)

    x_min = min(n["x"] - n["bw"] / 2 for n in nodes) - 0.4
    x_max = max(n["x"] + n["bw"] / 2 for n in nodes) + 0.4
    y_min = min(n["y"] - n["bh"] / 2 for n in nodes) - 0.5
    y_max = max(n["y"] + n["bh"] / 2 for n in nodes) + 0.5

    width  = max((x_max - x_min) * 1.05, 8)
    height = max((y_max - y_min) * Y_UNIT * 2.4, 4)

    fig, ax = plt.subplots(figsize=(width, height), dpi=DPI)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis("off")
    fig.patch.set_facecolor("#F8FAFC")

    draw_edges(ax, layout)
    for n in nodes:
        draw_node(ax, n)

    plt.tight_layout(pad=0.3)
    plt.savefig(out_path, dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)


# ─── 文件名处理 ───────────────────────────────────────────────────────────────
def slugify(path_list):
    name = "__".join(path_list)
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    while len(name.encode('utf-8')) > 220:
        name = name[:-1]
    return name


# ─── 主程序 ──────────────────────────────────────────────────────────────────
def main():
    print("📖 解析 XMind 文件...")
    sheets = load_xmind(INPUT_FILE)
    print(f"   共 {len(sheets)} 个画布")

    for sheet_title, root in sheets:
        sheet_dir = os.path.join(OUTPUT_DIR, re.sub(r'[\\/:*?"<>|]', '_', sheet_title))
        print(f"\n📂 画布：{sheet_title}")

        side_map  = compute_side_map(root)
        all_paths = collect_all_paths(root)
        print(f"   共 {len(all_paths)} 个节点")
        print(f"   右侧：{sum(1 for s in side_map.values() if s=='right')} 个一级主题  "
              f"左侧：{sum(1 for s in side_map.values() if s=='left')} 个一级主题")

        for i, path in enumerate(all_paths):
            branch_name = path[1] if len(path) >= 2 else path[0]
            sub_dir = os.path.join(sheet_dir, re.sub(r'[\\/:*?"<>|]', '_', branch_name))
            os.makedirs(sub_dir, exist_ok=True)

            fname    = slugify(path) + ".png"
            out_path = os.path.join(sub_dir, fname)

            print(f"  [{i+1:3d}/{len(all_paths)}] {'→'.join(path[-3:])}")
            try:
                render(root, path, side_map, out_path)
            except Exception as e:
                import traceback
                print(f"       ⚠️ 失败: {e}")
                traceback.print_exc()

    print(f"\n✅ 全部完成！输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
