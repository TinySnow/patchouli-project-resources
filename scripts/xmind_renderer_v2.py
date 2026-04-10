"""
学海计划 XMind 批量渲染脚本 v2
- 只展开目标节点所在路径，其他分支折叠
- 大幅缩小输出文件体积
"""

import json, os, zipfile, copy, re, math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.path as mpath
import numpy as np

# ─── 配置 ────────────────────────────────────────────────────────────────────
INPUT_FILE  = "学海计划.xmind"   # 与脚本放同一目录
OUTPUT_DIR  = "you_are_here"
DPI         = 120
FONT_NAME   = "Microsoft YaHei"

BRANCH_COLORS = [
    "#E07020", "#2B7FC4", "#3AAB5A",
    "#C43B6E", "#8B5CF6", "#0891B2", "#D97706",
    "#E53E3E", "#38A169",
]
ROOT_BG = "#F0F4F8"; ROOT_FG = "#1E293B"
NODE_BG = "#FFFFFF"; NODE_FG = "#334155"
DIM_BG  = "#F1F5F9"; DIM_FG  = "#94A3B8"
HERE_BG = "#FEF3C7"; HERE_FG = "#92400E"; HERE_BD = "#F59E0B"
TGT_FG  = "#1E293B"

X_GAP    = 2.0   # 水平间距（压缩）
Y_UNIT   = 0.42  # 垂直单位
MAX_WRAP = 12    # 自动折行字数
Y_MIN_SH = 0.55  # 每个节点最小占用高度（原来是1.0，太大）
# ─────────────────────────────────────────────────────────────────────────────


def load_xmind(path):
    with zipfile.ZipFile(path) as z:
        with z.open("content.json") as f:
            data = json.load(f)
    # 返回所有画布：[(画布标题, rootTopic), ...]
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


# ─── 构建折叠后的布局树 ───────────────────────────────────────────────────────
def build_collapsed_tree(node, depth, bc, path_set, target_title, here_label):
    """
    path_set: set of titles on the path to target (including root & target)
    只展开 path_set 中节点的子节点；其他节点作为叶子显示。
    """
    title = node.get("title", "")
    is_here = "You are here" in title
    is_target = (title == target_title)
    on_path = title in path_set

    t = {
        "title": title,
        "depth": depth,
        "bc": bc,
        "is_here": is_here,
        "is_target": is_target,
        "on_path": on_path,
        "x": float(depth) * X_GAP,
        "y": 0.0,
        "kids": [],
        "sh": 1.0,
    }

    children = get_children(node)

    # 决定是否展开子节点：
    # - depth==0 (根节点) 总是展开
    # - 在路径上的节点展开
    if depth == 0 or on_path:
        for i, c in enumerate(children):
            c_bc = BRANCH_COLORS[i % len(BRANCH_COLORS)] if depth == 0 else bc
            child_t = build_collapsed_tree(c, depth + 1, c_bc, path_set, target_title, here_label)
            t["kids"].append(child_t)

        # 如果当前是 target，追加 "You are here" 节点
        if is_target:
            here_node = {
                "title": here_label,
                "depth": depth + 1,
                "bc": bc,
                "is_here": True,
                "is_target": False,
                "on_path": False,
                "x": float(depth + 1) * X_GAP,
                "y": 0.0,
                "kids": [],
                "sh": 1.0,
            }
            t["kids"].append(here_node)

    # 计算子树高度
    if not t["kids"]:
        t["sh"] = Y_MIN_SH
    else:
        t["sh"] = sum(max(k["sh"], Y_MIN_SH) for k in t["kids"])

    return t


def assign_y(t, top=0.0):
    kids = t["kids"]
    if not kids:
        t["y"] = top
        return
    cursor = top
    for k in kids:
        assign_y(k, cursor)
        cursor += max(k["sh"], Y_MIN_SH)
    t["y"] = (kids[0]["y"] + kids[-1]["y"]) / 2.0


def flatten_tree(t, out=None):
    if out is None:
        out = []
    out.append(t)
    for k in t["kids"]:
        flatten_tree(k, out)
    return out


# ─── 计算节点盒子尺寸（与绘制逻辑共享） ─────────────────────────────────────
def node_box_size(n):
    """返回 (bw, bh, label)，基于文字长度估算。"""
    depth = n["depth"]
    title = n["title"]
    fs = 14 if depth == 0 else (10 if depth == 1 else 9)
    label = wrap_text(title)
    lines = label.split("\n")
    n_lines = len(lines)
    max_len = max(len(l) for l in lines)
    cw = fs * 0.013
    ch = fs * 0.024
    bw = max(max_len * cw + 0.28, 0.6)
    bh = max(n_lines * ch + 0.18, 0.34)
    if depth == 0:
        bw = max(bw, 1.1); bh = max(bh, 0.50)
    return bw, bh, label


# ─── 预计算所有节点的盒子尺寸，存入节点字典 ──────────────────────────────────
def precompute_boxes(nodes):
    for n in nodes:
        bw, bh, label = node_box_size(n)
        n["bw"] = bw
        n["bh"] = bh
        n["label"] = label


# ─── 绘制 ─────────────────────────────────────────────────────────────────────
def draw_edges(ax, t):
    px, py = t["x"], t["y"]
    p_bw = t.get("bw", 0.6)

    for k in t["kids"]:
        cx, cy = k["x"], k["y"]
        k_bw = k.get("bw", 0.6)

        on_path = k["on_path"] or k["is_here"] or k["is_target"]
        color = k["bc"]
        lw    = 2.2 if on_path else 1.0
        alpha = 1.0 if on_path else 0.35

        # 线起点：父节点右边框；终点：子节点左边框再退 0.06 留出间隙
        x_start = px + p_bw / 2
        x_end   = cx - k_bw / 2 - 0.06

        # 控制点靠近起点侧（30% 处），避免曲线横向幅度过大划入兄弟节点
        ctrl_ratio = 0.3
        cp1_x = x_start + (x_end - x_start) * ctrl_ratio
        cp2_x = x_end   - (x_end - x_start) * ctrl_ratio

        verts = [(x_start, py), (cp1_x, py), (cp2_x, cy), (x_end, cy)]
        codes = [mpath.Path.MOVETO, mpath.Path.CURVE4,
                 mpath.Path.CURVE4, mpath.Path.CURVE4]
        patch = mpatches.PathPatch(
            mpath.Path(verts, codes),
            facecolor="none", edgecolor=color,
            lw=lw, alpha=alpha, zorder=1
        )
        ax.add_patch(patch)
        draw_edges(ax, k)


def draw_node(ax, n):
    from matplotlib.patches import FancyBboxPatch

    x, y      = n["x"], n["y"]
    depth     = n["depth"]
    is_here   = n["is_here"]
    is_target = n["is_target"]
    on_path   = n["on_path"]
    bc        = n["bc"]
    bw        = n["bw"]
    bh        = n["bh"]
    label     = n["label"]

    fs = 14 if depth == 0 else (10 if depth == 1 else 9)
    fw = "bold" if (depth <= 1 or is_target or on_path) else "normal"
    fa = 1.0 if (on_path or depth == 0 or is_here or is_target) else 0.7

    # 颜色方案
    if is_here:
        bg, fg, bd, lw_box = HERE_BG, HERE_FG, HERE_BD, 1.8
    elif depth == 0:
        bg, fg, bd, lw_box = ROOT_BG, ROOT_FG, "#94A3B8", 1.8
    elif is_target:
        bg = bc + "22"
        fg, bd, lw_box = TGT_FG, bc, 2.5
    elif on_path:
        bg, fg, bd, lw_box = NODE_BG, NODE_FG, bc + "88", 1.4
    else:
        bg = bc + "18"
        fg = bc
        bd = bc + "55"
        lw_box = 0.8

    # 目标节点发光效果
    if is_target:
        glow = FancyBboxPatch(
            (x - bw / 2 - 0.07, y - bh / 2 - 0.07), bw + 0.14, bh + 0.14,
            boxstyle="round,pad=0.05",
            facecolor="none", edgecolor=bc,
            linewidth=3.0, alpha=0.3, zorder=1
        )
        ax.add_patch(glow)

    rect = FancyBboxPatch(
        (x - bw / 2, y - bh / 2), bw, bh,
        boxstyle="round,pad=0.05",
        facecolor=bg, edgecolor=bd, linewidth=lw_box, zorder=2
    )
    ax.add_patch(rect)

    ax.text(x, y, label, ha="center", va="center",
            fontsize=fs, color=fg, fontfamily=FONT_NAME,
            fontweight=fw, alpha=fa, zorder=3)


# ─── 渲染单张图 ───────────────────────────────────────────────────────────────
def render(root_json, target_path, here_label, out_path):
    path_set = set(target_path)
    target_title = target_path[-1]

    layout = build_collapsed_tree(
        root_json, 0, "#888", path_set, target_title, here_label
    )
    assign_y(layout)
    nodes = flatten_tree(layout)

    # 预计算每个节点的盒子尺寸
    precompute_boxes(nodes)

    # 用实际盒子边界计算画布范围，避免节点被裁切
    x_lefts  = [n["x"] - n["bw"] / 2 for n in nodes]
    x_rights = [n["x"] + n["bw"] / 2 for n in nodes]
    ys       = [n["y"] for n in nodes]
    bhs      = [n["bh"] for n in nodes]

    x_min = min(x_lefts)  - 0.4
    x_max = max(x_rights) + 0.4
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
    # 如果 UTF-8 字节超过 220，截断
    while len(name.encode('utf-8')) > 220:
        name = name[:-1]
    return name


# ─── 主程序 ──────────────────────────────────────────────────────────────────
def main():
    print("📖 解析 XMind 文件...")
    sheets = load_xmind(INPUT_FILE)
    print(f"   共 {len(sheets)} 个画布")

    for sheet_title, root in sheets:
        # 画布文件夹名
        sheet_dir = os.path.join(OUTPUT_DIR, re.sub(r'[\\/:*?"<>|]', '_', sheet_title))
        print(f"\n📂 画布：{sheet_title}")

        all_paths = collect_all_paths(root)
        print(f"   共 {len(all_paths)} 个节点")

        for i, path in enumerate(all_paths):
            # 一级标题子文件夹
            branch_name = path[1] if len(path) >= 2 else path[0]
            sub_dir = os.path.join(sheet_dir, re.sub(r'[\\/:*?"<>|]', '_', branch_name))
            os.makedirs(sub_dir, exist_ok=True)

            here_label = "<- You are here"
            fname = slugify(path) + ".png"
            out_path = os.path.join(sub_dir, fname)

            print(f"  [{i+1:3d}/{len(all_paths)}] {'→'.join(path[-3:])}")
            try:
                render(root, path, here_label, out_path)
            except Exception as e:
                import traceback
                print(f"       ⚠️ 失败: {e}")
                traceback.print_exc()

    print(f"\n✅ 全部完成！输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
