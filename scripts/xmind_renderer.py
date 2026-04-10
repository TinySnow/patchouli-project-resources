"""
学海计划 XMind 批量渲染脚本
为每个节点生成带 "You are here" 标记的思维导图 PNG
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
INPUT_FILE   = "学海计划.xmind"
OUTPUT_DIR   = "output"
DPI          = 150
FONT_NAME    = "Microsoft YaHei"

BRANCH_COLORS = [
    "#E07020", "#2B7FC4", "#3AAB5A",
    "#C43B6E", "#8B5CF6", "#0891B2", "#D97706",
]
ROOT_BG  = "#F0F4F8"; ROOT_FG  = "#1E293B"
NODE_BG  = "#FFFFFF"; NODE_FG  = "#1E293B"
DIM_BG   = "#F8F9FA"; DIM_FG   = "#AAAAAA"
HERE_BG  = "#FEF3C7"; HERE_FG  = "#92400E"; HERE_BD  = "#F59E0B"

X_GAP       = 1
Y_UNIT      = 0.10
MAX_LABEL   = 20
# ─────────────────────────────────────────────────────────────────────────────


def load_xmind(path):
    with zipfile.ZipFile(path) as z:
        with z.open("content.json") as f:
            data = json.load(f)
    return data[0]["rootTopic"]


def get_children(node):
    return node.get("children", {}).get("attached", [])


def wrap(text, max_c=MAX_LABEL):
    if len(text) <= max_c:
        return text
    out = []
    while len(text) > max_c:
        out.append(text[:max_c])
        text = text[max_c:]
    if text:
        out.append(text)
    return "\n".join(out)


def collect_nodes(node, path=None, depth=0, bidx=0, out=None):
    if out is None:
        out = []
    title = node.get("title", "")
    if "You are here" in title:
        return out
    path = (path or []) + [title]
    out.append({"path": list(path), "depth": depth, "bidx": bidx})
    for i, c in enumerate(get_children(node)):
        b = i if depth == 0 else bidx
        collect_nodes(c, path, depth+1, b, out)
    return out


def slugify(path):
    name = "-".join(path)
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name.encode('utf-8')) > 220:
        name = name[:55] + "…" + name[-15:]
    return name


# ─── 布局树 ──────────────────────────────────────────────────────────────────
def build_tree(node, depth=0, bc="#888"):
    title = node.get("title","")
    children = get_children(node)
    t = {"title": title, "depth": depth, "bc": bc,
         "is_here": "You are here" in title,
         "x": float(depth)*X_GAP, "y": 0.0, "kids": []}
    for i, c in enumerate(children):
        c_bc = BRANCH_COLORS[i % len(BRANCH_COLORS)] if depth == 0 else bc
        t["kids"].append(build_tree(c, depth+1, c_bc))
    # subtree height
    if not t["kids"]:
        t["sh"] = 1.0
    else:
        t["sh"] = sum(max(k["sh"], 1.0) for k in t["kids"])
    return t


def assign_y(t, top=0.0):
    kids = t["kids"]
    if not kids:
        t["y"] = top; return
    cursor = top
    for k in kids:
        assign_y(k, cursor)
        cursor += max(k["sh"], 1.0)
    t["y"] = (kids[0]["y"] + kids[-1]["y"]) / 2.0


def flatten(t, out=None):
    if out is None:
        out = []
    out.append(t)
    for k in t["kids"]:
        flatten(k, out)
    return out


def insert_here(tree, target_path, here_label):
    node = tree
    for seg in target_path[1:]:
        for c in get_children(node):
            if c.get("title") == seg:
                node = c; break
        else:
            return
    att = node.setdefault("children", {}).setdefault("attached", [])
    att[:] = [c for c in att if "You are here" not in c.get("title","")]
    att.append({"id":"__here__","title": here_label})


# ─── 渲染 ────────────────────────────────────────────────────────────────────
def render(root_json, target_path, here_label, out_path):
    tree_json = copy.deepcopy(root_json)
    insert_here(tree_json, target_path, here_label)

    path_set = set(target_path)
    target_title = target_path[-1]

    layout = build_tree(tree_json)
    assign_y(layout)
    nodes = flatten(layout)

    xs = [n["x"] for n in nodes]
    ys = [n["y"] for n in nodes]
    x_min = min(xs) - 0.4
    x_max = max(xs) + 3.8
    y_min = min(ys) - 0.7
    y_max = max(ys) + 0.7

    fig_w = max((x_max - x_min) * Y_UNIT * 5.0, 12)
    fig_h = max((y_max - y_min) * Y_UNIT * 2.6, 5)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis("off")
    fig.patch.set_facecolor("#F8FAFC")

    draw_edges(ax, layout, path_set)
    for n in nodes:
        draw_node(ax, n, path_set, target_title)

    plt.tight_layout(pad=0.2)
    plt.savefig(out_path, dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)


def draw_edges(ax, t, path_set):
    px, py = t["x"], t["y"]
    for k in t["kids"]:
        cx, cy = k["x"], k["y"]
        on_path = k["title"] in path_set or k["is_here"]
        color = k["bc"]
        lw    = 2.0 if on_path else 0.9
        alpha = 1.0 if on_path else 0.25

        mid_x = (px + cx) / 2.0
        verts  = [(px+0.12, py), (mid_x, py), (mid_x, cy), (cx-0.12, cy)]
        codes  = [mpath.Path.MOVETO, mpath.Path.CURVE4,
                  mpath.Path.CURVE4, mpath.Path.CURVE4]
        patch = mpatches.PathPatch(
            mpath.Path(verts, codes),
            facecolor="none", edgecolor=color,
            lw=lw, alpha=alpha, zorder=1
        )
        ax.add_patch(patch)
        draw_edges(ax, k, path_set)


def draw_node(ax, n, path_set, target_title):
    x, y   = n["x"], n["y"]
    title  = n["title"]
    depth  = n["depth"]
    is_here= n["is_here"]
    on_path= title in path_set
    is_tgt = (title == target_title) and not is_here

    # font size
    fs = 13 if depth==0 else (10 if depth==1 else 8.5)
    fw = "bold" if (depth<=1 or is_tgt or on_path) else "normal"
    fa = 1.0 if (on_path or depth==0) else 0.5

    label = wrap(title)
    lines = label.split("\n")
    n_lines = len(lines)
    max_len = max(len(l) for l in lines)

    # approximate box size
    cw = fs * 0.012
    ch = fs * 0.022
    bw = max(max_len * cw + 0.22, 0.55)
    bh = max(n_lines * ch + 0.16, 0.30)
    if depth == 0:
        bw = max(bw, 0.95); bh = max(bh, 0.44)

    # colors
    if is_here:
        bg, fg, bd, lw = HERE_BG, HERE_FG, HERE_BD, 1.5
    elif depth == 0:
        bg, fg, bd, lw = ROOT_BG, ROOT_FG, "#94A3B8", 1.5
    elif is_tgt:
        bg = n["bc"] + "1A"
        fg, bd, lw = n["bc"], n["bc"], 2.2
    elif on_path:
        bg, fg, bd, lw = NODE_BG, n["bc"], n["bc"]+"99", 1.2
    else:
        bg, fg, bd, lw = DIM_BG, DIM_FG, "#E2E8F0", 0.7

    from matplotlib.patches import FancyBboxPatch
    rect = FancyBboxPatch(
        (x - bw/2, y - bh/2), bw, bh,
        boxstyle="round,pad=0.04",
        facecolor=bg, edgecolor=bd, linewidth=lw, zorder=2
    )
    ax.add_patch(rect)

    if is_tgt:
        glow = FancyBboxPatch(
            (x - bw/2 - 0.06, y - bh/2 - 0.06), bw+0.12, bh+0.12,
            boxstyle="round,pad=0.04",
            facecolor="none", edgecolor=n["bc"], linewidth=2.8,
            alpha=0.35, zorder=1
        )
        ax.add_patch(glow)

    ax.text(x, y, label, ha="center", va="center",
            fontsize=fs, color=fg, fontfamily=FONT_NAME,
            fontweight=fw, alpha=fa, zorder=3)


# ─── 主程序 ──────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("📖 解析 XMind 文件...")
    root = load_xmind(INPUT_FILE)

    print("🌲 收集所有节点...")
    all_nodes = collect_nodes(root)
    print(f"   共 {len(all_nodes)} 个节点")

    for i, ni in enumerate(all_nodes):
        path = ni["path"]
        # 本图全为右侧展开，上级主题在右侧 → '<- You are here'
        here_label = "<- You are here"
        fname = slugify(path) + ".png"
        out   = os.path.join(OUTPUT_DIR, fname)
        print(f"  [{i+1:3d}/{len(all_nodes)}] {'→'.join(path[-3:])}")
        try:
            render(root, path, here_label, out)
        except Exception as e:
            print(f"       ⚠️ 失败: {e}")

    print(f"\n✅ 完成！{OUTPUT_DIR}，共 {len(all_nodes)} 张")

if __name__ == "__main__":
    main()
