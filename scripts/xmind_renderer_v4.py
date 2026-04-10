"""
================================================================================
学海计划 XMind 思维导图批量渲染脚本 v4
================================================================================

【功能】
  读取 XMind 文件，为每一个节点生成一张 PNG 图片。
  图片展示以该节点为"当前位置"的思维导图，效果如下：
    - 当前节点及其祖先路径高亮展开
    - 当前节点旁边附有黄色 "<- You are here" 标注框
    - 当前节点的兄弟节点、其他分支折叠显示（仅显示一级）
    - 根节点居中，一级主题超过 4 个时左右分布（模仿 XMind 平衡布局）

【依赖】
  pip install matplotlib

  字体：需要系统安装中文字体。脚本默认使用 "Microsoft YaHei"（微软雅黑）。
  其他可用字体示例：
    macOS  → "PingFang SC"
    Linux  → "Noto Sans CJK SC"

【用法】
  将本脚本与 .xmind 文件放在同一目录，然后运行：

    python xmind_renderer_v4.py                  # 渲染全部画布
    python xmind_renderer_v4.py -s 哲学           # 只渲染"哲学"画布
    python xmind_renderer_v4.py -p 哲学/总论      # 只渲染该路径前缀下的节点
    python xmind_renderer_v4.py -s 哲学 -p 总论    # 在指定画布内渲染该局部
    python xmind_renderer_v4.py -i 其他文件.xmind # 指定其他 XMind 文件
    python xmind_renderer_v4.py -o 自定义输出目录  # 指定输出目录
    python xmind_renderer_v4.py -h                # 查看帮助

【增量更新推荐（只改一个分支时）】
  当你仅修改思维导图中的局部内容时，优先使用 --path/-p 做前缀过滤，
  避免重跑整张图，显著缩短渲染时间。

    # 仅更新“哲学”画布下“总论”分支
    python xmind_renderer_v4.py -i ./学海计划.xmind -o ./maps -s 哲学 -p 总论

    # 仅更新更深层的小分支（完整路径）
    python xmind_renderer_v4.py -i ./学海计划.xmind -o ./maps -p 哲学/总论/哲学的分支体系

  注意：局部渲染不会自动删除历史输出文件；若节点重命名，旧 PNG 可能保留在输出目录。

【输出目录结构】
  you_are_here/
  ├── 文学/              ← 画布名称
  │   ├── 写作手法/      ← 一级主题名称
  │   │   ├── 文学__写作手法.png
  │   │   ├── 文学__写作手法__细节.png
  │   │   └── ...
  │   └── 体裁/
  │       └── ...
  └── 哲学/
      └── ...

【文件命名规则】
  文件名由节点完整路径组成，各层级之间用双下划线 __ 分隔。
  例如：文学__写作手法__细节__陌生化.png
  文件名超过 220 字节时自动截断。

【可调参数】
  见下方"配置区"，常用的有：
    INPUT_FILE  : XMind 文件路径（命令行 -i 可覆盖）
    OUTPUT_DIR  : 输出目录（命令行 -o 可覆盖）
    DPI         : 输出图片分辨率，越高越清晰但文件越大
    FONT_NAME   : 字体名称
    X_GAP       : 节点横向间距
    Y_MIN_SH    : 节点最小纵向占用空间
    MAX_WRAP    : 文字自动折行的字符数阈值
================================================================================
"""

import json, os, zipfile, re, math
import matplotlib
matplotlib.use('Agg')   # 非交互式后端，适合批量生成文件，无需显示器
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.path as mpath


# ══════════════════════════════════════════════════════════════════════════════
# 配置区 —— 常用参数在此修改
# ══════════════════════════════════════════════════════════════════════════════

INPUT_FILE  = "../学海计划.xmind"   # 默认 XMind 文件路径（可被命令行 -i 覆盖）
OUTPUT_DIR  = "../maps"     # 默认输出目录（可被命令行 -o 覆盖）
DPI         = 120                # 图片分辨率（每英寸点数），建议 100~150
FONT_NAME   = "Microsoft YaHei" # 渲染中文使用的字体英文名

# 各一级分支的颜色，按顺序循环分配
BRANCH_COLORS = [
    "#E07020", "#2B7FC4", "#3AAB5A",
    "#C43B6E", "#8B5CF6", "#0891B2", "#D97706",
    "#E53E3E", "#38A169", "#B45309", "#0E7490",
]

# 节点颜色配置
ROOT_BG = "#F0F4F8"   # 根节点背景色
ROOT_FG = "#1E293B"   # 根节点文字色
NODE_BG = "#FFFFFF"   # 路径节点背景色
NODE_FG = "#334155"   # 路径节点文字色
HERE_BG = "#FEF3C7"   # "You are here" 节点背景色（黄色）
HERE_FG = "#92400E"   # "You are here" 节点文字色
HERE_BD = "#F59E0B"   # "You are here" 节点边框色
TGT_FG  = "#1E293B"   # 当前目标节点文字色

# 布局参数
X_GAP    = 2.2    # 相邻层级节点之间的水平间距（单位：matplotlib 坐标）
Y_MIN_SH = 0.55   # 每个节点最小纵向占用空间，防止节点过于紧密
MAX_WRAP = 15     # 超过此字符数的标题自动折行
Y_UNIT   = 0.42   # y 坐标范围转换为英寸的系数，影响图片高度

# ══════════════════════════════════════════════════════════════════════════════


# ── 文件解析 ──────────────────────────────────────────────────────────────────

def load_xmind(path):
    """
    读取 XMind 文件（本质是 ZIP 压缩包），解析其中的 content.json。
    返回所有画布列表：[(画布标题, rootTopic字典), ...]
    """
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
    """获取 XMind 节点的子节点列表（attached 类型，不包括 detached）。"""
    return node.get("children", {}).get("attached", [])


# ── 文字处理 ──────────────────────────────────────────────────────────────────

def wrap_text(text, max_c=MAX_WRAP):
    """
    超过 max_c 个字符时强制折行，返回带换行符的字符串。
    例如 "这是一段很长的标题文字" → "这是一段很长的\n标题文字"
    """
    if len(text) <= max_c:
        return text
    lines = []
    while len(text) > max_c:
        lines.append(text[:max_c])
        text = text[max_c:]
    if text:
        lines.append(text)
    return "\n".join(lines)


# ── 布局辅助 ──────────────────────────────────────────────────────────────────

def compute_side_map(root_json):
    """
    决定每个一级主题放在根节点的左侧还是右侧。
    规则：
      - 一级主题 ≤ 4 个时全部放右侧（类似 XMind 非平衡布局）
      - 超过 4 个时前半部分放右侧、后半部分放左侧（类似 XMind 平衡布局）
    返回：{一级主题标题: 'right' | 'left'}
    """
    children = get_children(root_json)
    n = len(children)
    if n <= 4:
        # 主题较少，全部放右侧，图面更整洁
        return {c.get("title", ""): "right" for c in children}
    right_count = math.ceil(n / 2)
    side_map = {}
    for i, c in enumerate(children):
        title = c.get("title", "")
        side_map[title] = "right" if i < right_count else "left"
    return side_map


def compute_sh(title, depth):
    """
    根据节点标题折行后的实际行数和字体大小，估算该节点在 y 轴方向上
    需要占用的空间（slot height，简称 sh）。
    这个值用于布局时分配 y 坐标，防止多行节点与相邻节点重叠。
    公式：sh = 盒子实际高度 + 节点间留白（0.20）
    """
    fs = 14 if depth == 0 else (10 if depth == 1 else 9)  # 各层字号
    n_lines = len(wrap_text(title).split("\n"))
    bh = max(n_lines * fs * 0.024 + 0.18, 0.34)  # 盒子高度估算
    if depth == 0:
        bh = max(bh, 0.50)   # 根节点最小高度
    return max(Y_MIN_SH, bh + 0.20)   # 加上节点间留白


# ── 路径收集 ──────────────────────────────────────────────────────────────────

def collect_all_paths(node, path=None, out=None):
    """
    深度优先遍历整棵树，收集每个节点的完整路径。
    路径格式：[根节点标题, 一级标题, 二级标题, ..., 当前节点标题]
    跳过原有的 "You are here" 标记节点（这些是上次运行留下的，不是正式内容）。
    返回：[[路径1], [路径2], ...]
    """
    if out is None:
        out = []
    title = node.get("title", "")
    if "You are here" in title:   # 过滤掉源文件中残留的标记节点
        return out
    path = (path or []) + [title]
    out.append(list(path))
    for c in get_children(node):
        collect_all_paths(c, path, out)
    return out


# ── 路径过滤 ──────────────────────────────────────────────────────────────────

def parse_path_expr(expr):
    """
    将用户输入的路径表达式解析为路径片段列表。
    支持分隔符：/, \\, ->, →, __
    例如：
      "哲学/总论/哲学的分支体系"
      "哲学→总论→哲学的分支体系"
      "哲学__总论__哲学的分支体系"
    """
    expr = (expr or "").strip()
    if not expr:
        return []
    normalized = expr.replace("->", "/").replace("→", "/").replace("__", "/").replace("\\", "/")
    return [p.strip() for p in normalized.split("/") if p.strip()]


def filter_paths_by_prefix(all_paths, sheet_title, root_title, path_expr):
    """
    按路径前缀筛选节点路径，用于局部渲染。
    兼容两种写法：
      - 完整路径（从根节点开始）：哲学/总论
      - 画布局部路径（不含根）：总论（会自动尝试补根）
    返回：
      (filtered_paths, matched_prefix)
    """
    prefix_parts = parse_path_expr(path_expr)
    if not prefix_parts:
        return all_paths, []

    candidates = [prefix_parts]
    if root_title and prefix_parts[0] != root_title:
        candidates.append([root_title] + prefix_parts)
    if sheet_title and root_title and sheet_title != root_title and prefix_parts[0] != root_title:
        # 极少数文件中画布名与根标题不同，这里做兜底
        candidates.append([sheet_title] + prefix_parts)

    best_paths = []
    best_prefix = []
    for cand in candidates:
        matched = [p for p in all_paths if len(p) >= len(cand) and p[:len(cand)] == cand]
        if len(matched) > len(best_paths):
            best_paths = matched
            best_prefix = cand

    return best_paths, best_prefix


# ── 树构建 ────────────────────────────────────────────────────────────────────

def build_collapsed_tree(node, depth, bc, side, path_set, target_title, here_label, side_map):
    """
    递归构建用于渲染的"折叠树"结构。

    折叠逻辑：
      - 根节点（depth=0）：展开全部一级子节点
      - 路径节点（on_path=True）：展开其直接子节点
      - 其他节点：作为叶节点，不再展开（折叠状态）
      - 目标节点（is_target=True）：在其子节点末尾追加 "You are here" 节点

    参数：
      node         : 当前处理的 XMind 节点字典
      depth        : 当前深度（根节点为 0）
      bc           : 当前分支颜色（十六进制字符串）
      side         : 当前节点所在侧（'right' 或 'left'）
      path_set     : 目标路径上所有节点标题的集合，用于判断 on_path
      target_title : 目标节点的标题
      here_label   : "You are here" 标注文字（根据左右侧不同而不同）
      side_map     : 一级主题 → 侧的映射（由 compute_side_map 生成）

    返回的节点字典包含以下字段：
      title, depth, bc, side  : 基本属性
      is_here, is_target, on_path : 状态标记
      x, y                    : 坐标（x 在此阶段确定，y 由后续布局函数分配）
      kids                    : 子节点列表
      sh                      : 该节点子树占用的纵向空间（slot height）
    """
    title     = node.get("title", "")
    is_here   = "You are here" in title
    is_target = (title == target_title)
    on_path   = title in path_set

    # x 坐标：右侧为正值，左侧为负值，深度越深离根节点越远
    x = float(depth) * X_GAP if side == "right" else -float(depth) * X_GAP

    t = {
        "title": title, "depth": depth, "bc": bc, "side": side,
        "is_here": is_here, "is_target": is_target, "on_path": on_path,
        "x": x, "y": 0.0, "kids": [],
        "sh": compute_sh(title, depth),  # 初始 sh 为自身高度，有子节点时会被覆盖
    }

    children = get_children(node)

    if depth == 0:
        # 根节点：展开所有一级子节点，并按 side_map 分配左右侧
        for i, c in enumerate(children):
            c_title = c.get("title", "")
            if "You are here" in c_title:
                continue  # 跳过源文件中的旧标记节点
            c_bc   = BRANCH_COLORS[i % len(BRANCH_COLORS)]  # 循环分配颜色
            c_side = side_map.get(c_title, "right")
            child_t = build_collapsed_tree(c, 1, c_bc, c_side, path_set, target_title, here_label, side_map)
            t["kids"].append(child_t)

    elif on_path:
        # 路径节点：展开其直接子节点（这些子节点可能继续递归展开或折叠）
        for c in children:
            if "You are here" in c.get("title", ""):
                continue  # 同样跳过旧标记节点
            child_t = build_collapsed_tree(c, depth + 1, bc, side, path_set, target_title, here_label, side_map)
            t["kids"].append(child_t)

        if is_target:
            # 目标节点：在子节点列表末尾追加 "You are here" 标注节点
            t["kids"].append({
                "title": here_label, "depth": depth + 1, "bc": bc, "side": side,
                "is_here": True, "is_target": False, "on_path": False,
                "x": (float(depth + 1) * X_GAP if side == "right" else -float(depth + 1) * X_GAP),
                "y": 0.0, "kids": [],
                "sh": compute_sh(here_label, depth + 1),  # here 节点也需要正确的占用高度
            })

    # 有子节点时，父节点的 sh 等于所有子节点 sh 之和（子树总高度）
    if t["kids"]:
        t["sh"] = sum(k["sh"] for k in t["kids"])

    return t


# ── y 坐标分配 ────────────────────────────────────────────────────────────────

def _assign_y_side(kids):
    """
    对一组同侧的一级子节点分配 y 坐标（从下到上，第一个子节点在顶部）。
    返回这组节点的 y 坐标中心值，用于后续整体平移到 y=0 附近。

    使用反向迭代（reversed）是因为 matplotlib 的 y 轴向上为正，
    而我们希望列表中第一个节点显示在图片顶部（y 值最大）。
    """
    if not kids:
        return 0.0
    cursor = 0.0
    for k in reversed(kids):   # 反向迭代：最后一个子节点从 y=0 开始，第一个在顶部
        _assign_y_rec(k, cursor)
        cursor += k["sh"]
    return (kids[-1]["y"] + kids[0]["y"]) / 2.0   # 返回首尾中点作为整组中心


def _assign_y_rec(t, top):
    """
    递归为节点及其子树分配 y 坐标。
    top 是该子树底部的 y 坐标起点，节点自身的 y 为子树的几何中心。
    叶节点的 y = top + sh/2（自身高度的中心）。
    """
    kids = t["kids"]
    if not kids:
        t["y"] = top + t["sh"] / 2.0   # 叶节点：y 在自身 sh 的中心
        return
    cursor = top
    for k in reversed(kids):   # 同样反向，保持顺序一致
        _assign_y_rec(k, cursor)
        cursor += k["sh"]
    t["y"] = (kids[-1]["y"] + kids[0]["y"]) / 2.0   # 父节点 y 为首尾子节点的中点


def _shift_y(t, dy):
    """将节点及其整个子树的 y 坐标统一平移 dy。"""
    t["y"] += dy
    for k in t["kids"]:
        _shift_y(k, dy)


def assign_y_two_sides(root_t):
    """
    分别为左右两侧的子树分配 y 坐标，并各自以 y=0 为中心对齐。
    根节点固定在 y=0。
    左右两侧独立布局，互不影响各自的高度分配。
    """
    right_kids = [k for k in root_t["kids"] if k["side"] == "right"]
    left_kids  = [k for k in root_t["kids"] if k["side"] == "left"]

    r_center = _assign_y_side(right_kids)
    l_center = _assign_y_side(left_kids)

    # 将各侧整体平移，使中心对齐 y=0
    for k in right_kids:
        _shift_y(k, -r_center)
    for k in left_kids:
        _shift_y(k, -l_center)

    root_t["y"] = 0.0   # 根节点始终在 y=0


# ── 树遍历工具 ────────────────────────────────────────────────────────────────

def flatten_tree(t, out=None):
    """将树结构拍平为列表，方便后续统一处理所有节点。"""
    if out is None:
        out = []
    out.append(t)
    for k in t["kids"]:
        flatten_tree(k, out)
    return out


# ── 盒子尺寸计算 ──────────────────────────────────────────────────────────────

def node_box_size(n):
    """
    根据节点的标题文字和深度，估算渲染时矩形框的宽度（bw）和高度（bh）。
    同时返回折行处理后的标签文字（label）。
    注意：这里使用经验系数估算，与 matplotlib 实际渲染尺寸近似但不精确，
          足够用于避免节点溢出画布边界。
    """
    depth = n["depth"]
    title = n["title"]
    fs    = 14 if depth == 0 else (10 if depth == 1 else 9)  # 各深度字号
    label = wrap_text(title)
    lines = label.split("\n")
    cw = fs * 0.013   # 单个字符的估算宽度（经验值）
    ch = fs * 0.024   # 单行文字的估算高度（经验值）
    bw = max(max(len(l) for l in lines) * cw + 0.28, 0.6)   # 宽 = 最长行宽 + 内边距
    bh = max(len(lines) * ch + 0.18, 0.34)                   # 高 = 行数 × 行高 + 内边距
    if depth == 0:
        bw = max(bw, 1.1); bh = max(bh, 0.50)   # 根节点有最小尺寸限制
    return bw, bh, label


def precompute_boxes(nodes):
    """
    预计算所有节点的盒子尺寸，将 bw、bh、label 写入各节点字典。
    必须在绘制之前调用，因为绘图（draw_edges、draw_node）都依赖这些值。
    """
    for n in nodes:
        bw, bh, label = node_box_size(n)
        n["bw"] = bw
        n["bh"] = bh
        n["label"] = label


# ── 绘制连线 ──────────────────────────────────────────────────────────────────

def draw_edges(ax, t):
    """
    递归绘制节点与其子节点之间的连接线（贝塞尔曲线）。

    连线风格：采用"主干+分支"路由——
      1. 从父节点边框外侧出发（偏移 0.06 避免与边框重叠）
      2. 快速走到靠近父节点的主干位置（trunk_x，约在父子之间 15% 处）
      3. 沿主干垂直走到子节点所在的 y 坐标
      4. 水平接入子节点边框外侧（同样偏移 0.06）
    这种路由方式使所有子节点的连线共用一段竖直主干，
    避免曲线横向幅度过大、穿过兄弟节点方框的问题。

    路径上的连线（on_path=True）：加粗（lw=2.2）且不透明
    非路径连线：细（lw=1.0）且半透明（alpha=0.35）
    """
    px, py = t["x"], t["y"]
    p_bw   = t.get("bw", 0.6)   # 父节点宽度（precompute_boxes 后才有）

    for k in t["kids"]:
        cx, cy = k["x"], k["y"]
        k_bw   = k.get("bw", 0.6)
        side   = k["side"]

        on_path = k["on_path"] or k["is_here"] or k["is_target"]
        color = k["bc"]
        lw    = 2.2 if on_path else 1.0
        alpha = 1.0 if on_path else 0.35

        # 起止点各偏移 0.06，避免线头与方框边线重叠
        if side == "right":
            x_start = px + p_bw / 2 + 0.06   # 父节点右边框外侧
            x_end   = cx - k_bw / 2 - 0.06   # 子节点左边框外侧
        else:
            x_start = px - p_bw / 2 - 0.06   # 父节点左边框外侧
            x_end   = cx + k_bw / 2 + 0.06   # 子节点右边框外侧

        # trunk_x：主干所在的 x 位置，紧贴父节点，占父子间距的 15%
        trunk_ratio = 0.15
        trunk_x = x_start + (x_end - x_start) * trunk_ratio

        # 贝塞尔曲线四个控制点：
        #   MOVETO  : 起点（父节点边框外）
        #   CURVE4  : CP1（trunk_x 处，与起点同 y → 水平出发）
        #   CURVE4  : CP2（trunk_x 处，与终点同 y → 垂直走完）
        #   CURVE4  : 终点（子节点边框外，水平接入）
        verts = [
            (x_start, py),
            (trunk_x,  py),
            (trunk_x,  cy),
            (x_end,    cy),
        ]
        codes = [mpath.Path.MOVETO, mpath.Path.CURVE4,
                 mpath.Path.CURVE4, mpath.Path.CURVE4]
        ax.add_patch(mpatches.PathPatch(
            mpath.Path(verts, codes),
            facecolor="none", edgecolor=color,
            lw=lw, alpha=alpha, zorder=1
        ))
        draw_edges(ax, k)   # 递归绘制子节点的连线


# ── 绘制节点 ──────────────────────────────────────────────────────────────────

def draw_node(ax, n):
    """
    绘制单个节点的矩形框和文字标签。

    节点视觉状态分五种：
      1. You are here 节点  : 黄色背景，橙色边框
      2. 根节点（depth=0）  : 浅灰蓝背景，深色文字，明显边框
      3. 目标节点（当前位置）: 分支色淡底，深色文字，加粗边框 + 外发光效果
      4. 路径节点（祖先）   : 白色背景，深色文字，分支色边框
      5. 折叠节点（其他）   : 分支色极淡底色，分支色文字，半透明边框

    目标节点额外绘制一个微微放大的发光边框（alpha=0.3），增强视觉焦点。
    """
    from matplotlib.patches import FancyBboxPatch

    x, y      = n["x"], n["y"]
    depth     = n["depth"]
    is_here   = n["is_here"]
    is_target = n["is_target"]
    on_path   = n["on_path"]
    bc        = n["bc"]           # 所属分支的颜色
    bw        = n["bw"]           # 盒子宽度（precompute_boxes 已计算）
    bh        = n["bh"]           # 盒子高度
    label     = n["label"]        # 折行后的标签文字

    fs = 14 if depth == 0 else (10 if depth == 1 else 9)   # 字号
    fw = "bold" if (depth <= 1 or is_target or on_path) else "normal"   # 字重
    fa = 1.0 if (on_path or depth == 0 or is_here or is_target) else 0.85  # 透明度

    # 根据节点状态选择颜色方案
    if is_here:
        # "You are here" 标注：黄底橙框
        bg, fg, bd, lw_box = HERE_BG, HERE_FG, HERE_BD, 1.8
    elif depth == 0:
        # 根节点：浅蓝灰底，深色字
        bg, fg, bd, lw_box = ROOT_BG, ROOT_FG, "#94A3B8", 1.8
    elif is_target:
        # 目标节点（当前位置）：分支色极淡底（22 = 约 13% 透明度），加粗边框
        bg = bc + "22"; fg, bd, lw_box = TGT_FG, bc, 2.5
    elif on_path:
        # 路径祖先节点：白底，分支色半透明边框
        bg, fg, bd, lw_box = NODE_BG, NODE_FG, bc + "88", 1.4
    else:
        # 折叠的非路径节点：分支色淡底（30 = 约 19% 透明度），有颜色但明显偏淡
        bg = bc + "30"
        fg = bc           # 文字用分支色，保持可读性
        bd = bc + "88"    # 边框用半透明分支色
        lw_box = 1.0

    # 目标节点：绘制外发光圈（放大 0.07 的同色圆角矩形，低透明度）
    if is_target:
        ax.add_patch(FancyBboxPatch(
            (x - bw/2 - 0.07, y - bh/2 - 0.07), bw + 0.14, bh + 0.14,
            boxstyle="round,pad=0.05", facecolor="none", edgecolor=bc,
            linewidth=3.0, alpha=0.3, zorder=1
        ))

    # 主矩形框
    ax.add_patch(FancyBboxPatch(
        (x - bw/2, y - bh/2), bw, bh,
        boxstyle="round,pad=0.05",
        facecolor=bg, edgecolor=bd, linewidth=lw_box, zorder=2
    ))

    # 文字标签（居中对齐，z-order 最高确保在框上方）
    ax.text(x, y, label, ha="center", va="center",
            fontsize=fs, color=fg, fontfamily=FONT_NAME,
            fontweight=fw, alpha=fa, zorder=3)


# ── 渲染单张图 ────────────────────────────────────────────────────────────────

def render(root_json, target_path, side_map, out_path):
    """
    为指定的目标路径生成一张 PNG 图片，保存到 out_path。

    流程：
      1. 构建折叠树（只展开目标路径，其他折叠）
      2. 分配 y 坐标（左右两侧独立布局，各自居中）
      3. 预计算盒子尺寸
      4. 根据实际盒子边界计算画布尺寸（避免节点被裁切）
      5. 绘制连线和节点
      6. 保存为 PNG
    """
    path_set     = set(target_path)    # 路径节点标题集合，O(1) 查找
    target_title = target_path[-1]     # 目标节点标题（路径末尾）

    # 根据目标节点所在侧，确定标注方向（箭头朝向目标节点）
    branch_title = target_path[1] if len(target_path) >= 2 else ""
    target_side  = side_map.get(branch_title, "right")
    here_label   = "<- You are here" if target_side == "right" else "You are here ->"

    # 构建折叠树并分配坐标
    layout = build_collapsed_tree(
        root_json, 0, "#888", "right",
        path_set, target_title, here_label, side_map
    )
    assign_y_two_sides(layout)

    # 拍平、预计算盒子
    nodes = flatten_tree(layout)
    precompute_boxes(nodes)

    # 用实际盒子边界（而非节点中心）计算画布范围，确保所有节点完整显示
    x_min = min(n["x"] - n["bw"] / 2 for n in nodes) - 0.4
    x_max = max(n["x"] + n["bw"] / 2 for n in nodes) + 0.4
    y_min = min(n["y"] - n["bh"] / 2 for n in nodes) - 0.5
    y_max = max(n["y"] + n["bh"] / 2 for n in nodes) + 0.5

    # 图片尺寸（英寸）由坐标范围推导
    width  = max((x_max - x_min) * 1.05, 8)
    height = max((y_max - y_min) * Y_UNIT * 2.4, 4)

    fig, ax = plt.subplots(figsize=(width, height), dpi=DPI)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis("off")                         # 隐藏坐标轴
    fig.patch.set_facecolor("#F8FAFC")     # 图片背景色（极浅灰白）

    draw_edges(ax, layout)
    for n in nodes:
        draw_node(ax, n)

    plt.tight_layout(pad=0.3)
    plt.savefig(out_path, dpi=DPI, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)   # 释放内存，批量渲染时非常重要


# ── 文件名处理 ────────────────────────────────────────────────────────────────

def slugify(path_list):
    """
    将节点路径列表转换为合法的文件名字符串。
    规则：
      - 各层级标题用双下划线 __ 拼接
      - 替换 Windows/macOS 文件系统不允许的字符（\\/:*?"<>|）为下划线
      - 合并连续空白为单个空格
      - 若 UTF-8 编码后超过 220 字节则从末尾截断（避免文件系统路径长度限制）
    """
    name = "__".join(path_list)
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    while len(name.encode('utf-8')) > 220:
        name = name[:-1]
    return name


# ── 主程序 ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="学海计划 XMind 思维导图批量渲染工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python xmind_renderer_v4.py                  渲染全部画布
  python xmind_renderer_v4.py -s 哲学           只渲染"哲学"画布
  python xmind_renderer_v4.py -p 哲学/总论      只渲染该路径前缀下的节点
  python xmind_renderer_v4.py -s 哲学 -p 总论    在指定画布内渲染该局部
  python xmind_renderer_v4.py -i 其他.xmind     指定其他 XMind 文件
  python xmind_renderer_v4.py -o my_output      指定输出目录
        """
    )
    parser.add_argument("--sheet",  "-s", default="",
                        help="只渲染指定画布名称，留空则渲染全部（例如：-s 哲学）")
    parser.add_argument("--path",   "-p", default="",
                        help="只渲染指定路径前缀下的节点（例如：-p 哲学/总论 或 -s 哲学 -p 总论）")
    parser.add_argument("--input",  "-i", default=INPUT_FILE,
                        help=f"XMind 文件路径（默认：{INPUT_FILE}）")
    parser.add_argument("--output", "-o", default=OUTPUT_DIR,
                        help=f"PNG 输出目录（默认：{OUTPUT_DIR}）")
    args = parser.parse_args()

    only_sheet = args.sheet
    only_path  = args.path
    input_file = args.input
    output_dir = args.output

    print("📖 解析 XMind 文件...")
    sheets = load_xmind(input_file)
    print(f"   共 {len(sheets)} 个画布")

    for sheet_title, root in sheets:
        # 跳过不需要渲染的画布
        if only_sheet and sheet_title != only_sheet:
            print(f"⏭️  跳过画布：{sheet_title}")
            continue

        # 输出目录：output_dir / 画布名称 /
        sheet_dir = os.path.join(output_dir, re.sub(r'[\\/:*?"<>|]', '_', sheet_title))
        print(f"\n📂 画布：{sheet_title}")

        side_map  = compute_side_map(root)
        all_paths = collect_all_paths(root)
        root_title = root.get("title", "")

        if only_path:
            filtered_paths, matched_prefix = filter_paths_by_prefix(
                all_paths, sheet_title, root_title, only_path
            )
            if not filtered_paths:
                print(f"   ⚠️ 路径过滤未命中：{only_path}（本画布跳过）")
                continue
            all_paths = filtered_paths
            print(f"   路径过滤：{'/'.join(matched_prefix)}")

        print(f"   共 {len(all_paths)} 个节点")
        print(f"   右侧：{sum(1 for s in side_map.values() if s=='right')} 个一级主题  "
              f"左侧：{sum(1 for s in side_map.values() if s=='left')} 个一级主题")

        for i, path in enumerate(all_paths):
            # 子目录：output_dir / 画布名称 / 一级主题名称 /
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

    print(f"\n✅ 全部完成！输出目录: {output_dir}")


if __name__ == "__main__":
    main()
