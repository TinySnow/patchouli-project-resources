#!/usr/bin/env python3
# =============================================================================
# 学海计划 - 标题图片批量生成工具
# =============================================================================
#
# 【功能】
#   读取 XMind 思维导图或纯文本列表，为每个主题生成一张标题图片。
#   图片样式：纯白背景，第一行"学海计划"，第二行为具体主题，
#   高度固定 200px，宽度根据文字自适应（含左右留白）。
#   主题清洗规则（用于标题展示与文件名）：
#     - 删除（...）与 (...) 括号段（非贪婪）
#     - 删除最后一个冒号（：或 :）及其后内容（常见副标题）
#     - 清理多余空白
#   树形落盘规则（XMind 模式）：
#     - 非叶子结点：创建同名文件夹，并在该文件夹内生成同名封面图
#     - 叶子结点：在其父级非叶子文件夹内生成同名封面图
#   文件名只使用当前结点名称，不拼接父路径（避免文件名过长）。
#
# 【依赖】
#   pip install Pillow
#
# 【用法】
#
#   ① 从 XMind 文件生成全部画布：
#         python3 generate_titles.py
#      （默认读取 ../学海计划.xmind，也可显式指定：--xmind 其他路径.xmind）
#      输出结构：
#         output/
#         ├── 画布名/
#         │   └── 根结点/
#         │       ├── 根结点.png
#         │       ├── 叶子结点A.png
#         │       └── 非叶子结点B/
#         │           ├── 非叶子结点B.png
#         │           └── 叶子结点C.png
#         └── ...
#      若“画布名 == 根结点名”，会自动去重，避免出现“法学/法学/...”。
#
#   ② 只重新渲染某一个画布（XMind 源文件有改动时使用）：
#         python3 generate_titles.py --xmind 学海计划.xmind --sheet 医学
#      仅处理名为"医学"的画布，其他画布不受影响。
#      画布名称需与 XMind 中的标签页名称完全一致。
#      也可进一步局部更新：
#         python3 generate_titles.py --xmind 学海计划.xmind --sheet 医学 --path 药理学
#      仅处理指定路径前缀下的主题。
#
#   ③ 列出 XMind 中所有画布名称（不生成图片，用于确认 --sheet 的名称）：
#         python3 generate_titles.py --xmind 学海计划.xmind --list-sheets
#
#   ④ 生成单张图片（临时测试用）：
#         python3 generate_titles.py --topic "不良反应"
#      图片直接输出到 OUTPUT_DIR 根目录，不建子文件夹。
#      若希望指定输出目录路径（无需手打文件名）：
#         python3 generate_titles.py --topic-path "心理学/普通心理学/归因错误"
#      未传 --topic 时，会自动取最后一段作为标题和文件名；
#      只传 --topic-path 时，最后一段为文件名，其余段为目录。
#
#   ⑤ 从纯文本文件批量生成（不使用 XMind 时）：
#         python3 generate_titles.py --list topics.txt
#      topics.txt 格式：每行一个主题，# 开头为注释，空行忽略。
#      若文件不存在，脚本会自动创建一个示例文件。
#
# 【配置】
#   修改下方"配置区"中的变量即可调整字体、尺寸、颜色等。
# =============================================================================

import os
import sys
import argparse
import zipfile
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ─── 配置区（按需修改） ───────────────────────────────────────
FONT_PATH  = "../fonts/仓耳与墨W02.ttf"  # 字体文件路径（.ttf / .otf）
OUTPUT_DIR = "../covers"            # 图片输出根目录
FONT_SIZE  = 60                    # 字号（px）
IMG_HEIGHT = 400                   # 图片高度（px），固定不变
PADDING_X  = 200                    # 左右留白（px），图片宽度 = 最长文字宽 + PADDING_X * 2
LINE_GAP   = 12                    # 两行文字之间的间距（px）
TEXT_COLOR = "#3d3d3d"             # 文字颜色
BG_COLOR   = "#ffffff"             # 背景颜色
TITLE_LINE = "学海计划"            # 固定第一行文字
# ─────────────────────────────────────────────────────────────


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """
    加载字体文件。
    若字体文件不存在，打印明确提示后退出，避免静默使用默认字体产生错误输出。
    """
    path = Path(FONT_PATH)
    if not path.exists():
        print(f"[ERROR] 字体文件未找到：{FONT_PATH}")
        print("        请将字体文件放到脚本同目录，或修改脚本顶部 FONT_PATH 变量。")
        sys.exit(1)
    return ImageFont.truetype(str(path), size)


def make_image(topic: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    """
    生成单张标题图片。

    原理：
      1. 用 1x1 的临时画布预测量两行文字的宽高（避免真正绘图前不知道尺寸）。
      2. 图片宽度 = max(第一行宽, 第二行宽) + 左右留白；高度固定为 IMG_HEIGHT。
      3. 两行文字整体在垂直方向居中，每行在水平方向居中。
    """
    # ── 步骤 1：测量文字尺寸 ──
    tmp  = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(tmp)

    def measure(text):
        # textbbox 返回 (left, top, right, bottom)，差值即为宽高
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    w1, h1 = measure(TITLE_LINE)   # 第一行"学海计划"的宽高
    w2, h2 = measure(topic)        # 第二行主题文字的宽高

    # ── 步骤 2：计算图片尺寸和文字起始坐标 ──
    img_width    = max(w1, w2) + PADDING_X * 2
    total_text_h = h1 + LINE_GAP + h2
    y_start      = (IMG_HEIGHT - total_text_h) // 2   # 整体垂直居中的起点

    # ── 步骤 3：绘制 ──
    img  = Image.new("RGB", (img_width, IMG_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # 每行分别水平居中：x = (图片宽 - 文字宽) / 2
    draw.text(((img_width - w1) // 2, y_start),                   TITLE_LINE, font=font, fill=TEXT_COLOR)
    draw.text(((img_width - w2) // 2, y_start + h1 + LINE_GAP),   topic,      font=font, fill=TEXT_COLOR)

    return img


def sanitize(name: str) -> str:
    """
    将字符串中 Windows / Linux 文件系统不允许出现在文件名或目录名中的字符，
    替换为视觉相近的全角字符，确保文件名合法且显示内容基本不变。

    非法字符对照：
      /  →  ／      \\  →  ＼     :  →  ：
      *  →  ＊      ?  →  ？     "  →  ＂
      <  →  ＜      >  →  ＞     |  →  ｜
    """
    return (name
        .replace("/",  "／").replace("\\", "＼").replace(":",  "：")
        .replace("*",  "＊").replace("?",  "？").replace('"',  "＂")
        .replace("<",  "＜").replace(">",  "＞").replace("|",  "｜"))


def strip_parenthesized(text: str) -> str:
    """
    标题清洗核心函数（用于 XMind / --topic / --list 三种输入）。

    处理顺序：
      1) 删除括号段（支持中文全角括号与英文半角括号），非贪婪匹配
      2) 删除最后一个冒号（：或 :）及其后内容
      3) 合并多余空白并 trim

    设计意图：
      - 括号内容多数是示例、人物、补充说明，不适合作为封面主标题
      - 冒号后的内容多为副标题，删除后可显著缩短文件名并提升统一性

    注意：
      - “删除最后一个冒号后内容”是按最后一个冒号裁剪，不是第一个
      - 若清洗后为空，由调用方决定回退策略（例如回退原标题）

    例：
      （注意）结点（补充）  ->  结点
      node(foo)bar(baz)     ->  nodebar
      第一篇 基础与大脑：你是谁？ -> 第一篇 基础与大脑
      A:B:C                 -> A:B
    """
    cleaned = re.sub(r"\s*(?:（.*?）|\(.*?\))\s*", "", text or "")
    last_colon = max(cleaned.rfind("："), cleaned.rfind(":"))
    if last_colon != -1:
        cleaned = cleaned[:last_colon]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_path_expr(expr: str) -> list[str]:
    """
    将路径表达式解析为片段列表，支持分隔符：
      /, \\, ->, →, __

    说明：
      - 允许用户在不同输入习惯下混用分隔符
      - 连续分隔符/空片段会被自动忽略
    """
    expr = (expr or "").strip()
    if not expr:
        return []
    normalized = expr.replace("->", "/").replace("→", "/").replace("__", "/").replace("\\", "/")
    return [p.strip() for p in normalized.split("/") if p.strip()]


def filter_items_by_prefix(items: list[tuple], path_expr: str, sheet_hint: str = "") -> tuple[list[tuple], list[str]]:
    """
    按路径前缀筛选 XMind 条目。
    每个 item 结构为：
      (sheet_name, topic, path_parts, folder_parts, is_non_leaf)
    返回：(过滤后条目, 命中的前缀片段列表)。

    匹配策略：
      - 既支持“完整路径”（从根开始），也支持“局部路径”（自动补根）
      - 候选前缀按命中条目数量择优，最大化用户输入命中概率
    """
    prefix_parts = parse_path_expr(path_expr)
    if not prefix_parts:
        return items, []

    roots = []
    for _, _, path_parts, _, _ in items:
        if path_parts:
            root = path_parts[0]
            if root not in roots:
                roots.append(root)

    # 候选前缀既支持完整路径（从根开始），也支持局部路径（自动补根后再匹配）
    candidates = [prefix_parts]
    for root in roots:
        if prefix_parts[0] != root:
            candidates.append([root] + prefix_parts)
    if sheet_hint and prefix_parts[0] != sheet_hint:
        candidates.append([sheet_hint] + prefix_parts)

    # 去重并保持顺序
    uniq_candidates = []
    seen = set()
    for cand in candidates:
        key = tuple(cand)
        if key in seen:
            continue
        seen.add(key)
        uniq_candidates.append(cand)

    best_items = []
    best_prefix = []
    for cand in uniq_candidates:
        # 前缀命中规则：节点完整路径以 cand 开头
        matched = [it for it in items if len(it[2]) >= len(cand) and it[2][:len(cand)] == cand]
        if len(matched) > len(best_items):
            best_items = matched
            best_prefix = cand

    return best_items, best_prefix


def build_filename(topic: str) -> str:
    """
    生成文件名：仅使用当前结点名称，不拼接父路径。
    """
    name = sanitize((topic or "").strip()) or "未命名主题"
    return f"{name}.png"


def merge_sheet_and_tree_folders(sheet_name: str, tree_folders: list[str]) -> list[str]:
    """
    合并“画布目录 + 树形目录”并做首层去重。

    典型场景：
      - 画布名为“法学”
      - 根节点也为“法学”
    默认路径会变成 法学/法学/...，这里自动去重为 法学/...。

    细节：
      - 比较前会先做 strip_parenthesized + sanitize，避免因标点/空白差异导致漏判
    """
    parts = [sheet_name, *(tree_folders or [])]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        a = sanitize(strip_parenthesized(parts[0]))
        b = sanitize(strip_parenthesized(parts[1]))
        if a == b:
            parts.pop(1)
    return parts


def save_image(img: Image.Image, topic: str, subfolders: list[str] | None = None) -> str:
    """
    将图片保存到指定位置。

    参数：
      img       - 已生成的 PIL Image 对象
      topic     - 主题文字（用于图片第二行展示）
      subfolders - 输出目录片段列表（会依次拼接为树形目录）

    返回保存后的完整路径。
    """
    folder_parts = [sanitize(p) for p in (subfolders or []) if p]
    out_dir = os.path.join(OUTPUT_DIR, *folder_parts) if folder_parts else OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)

    filename = build_filename(topic)
    out_path = os.path.join(out_dir, filename)
    img.save(out_path, "PNG")

    rel_dir = os.path.relpath(out_dir, OUTPUT_DIR)
    prefix = f"[{rel_dir}] " if rel_dir != "." else ""
    print(f"  ✓ {prefix}{filename}")
    return out_path


def parse_xmind(xmind_path: str) -> list[tuple]:
    """
    解析 XMind ZEN 格式（.xmind）文件，提取所有画布中的主题节点。

    XMind .xmind 文件本质上是一个 ZIP 压缩包，其中 content.json 记录了
    所有画布（sheet）和节点（topic）的树形结构。

    返回值：
      [(画布名, 主题文字, 路径片段列表, 落盘目录片段列表, 是否非叶子), ...]
      按文件中原始顺序排列。

    跳过规则：
      - 含有 'You are here' 的节点（路径标记，非内容节点）
      - 空标题节点
    """
    try:
        results = []

        def walk(node, sheet_name, path_parts=None, non_leaf_anc=None):
            """
            递归遍历节点树，并计算每个节点在“树形落盘模式”下的目标目录。

            path_parts     : 当前节点完整路径（用于 --path 前缀匹配）
            non_leaf_anc   : 当前路径上“非叶子祖先”的标题链（用于目录构建）
            """
            raw_title = node.get("title", "").strip()
            if "You are here" in raw_title:
                return

            title     = strip_parenthesized(raw_title)
            children = node.get("children", {}).get("attached", [])
            visible_children = [c for c in children if "You are here" not in c.get("title", "")]
            is_non_leaf = bool(visible_children)

            curr_path = list(path_parts or [])
            if title:
                curr_path.append(title)

            curr_non_leaf_anc = list(non_leaf_anc or [])
            if title and is_non_leaf:
                # 非叶子结点：
                #   目录 = 祖先非叶子目录 + 自己
                #   文件 = 自己（自己目录内）
                folder_parts = curr_non_leaf_anc + [title]
                child_non_leaf_anc = folder_parts
            else:
                # 叶子结点：
                #   目录 = 祖先非叶子目录（不额外建自己的目录）
                #   文件 = 自己（父目录内）
                folder_parts = curr_non_leaf_anc
                child_non_leaf_anc = curr_non_leaf_anc

            if title:
                results.append((sheet_name, title, curr_path, folder_parts, is_non_leaf))

            for child in visible_children:
                walk(child, sheet_name, curr_path, child_non_leaf_anc)

        with zipfile.ZipFile(xmind_path) as z:
            if "content.json" not in z.namelist():
                print("[WARN] 未找到 content.json，请确认使用的是 XMind ZEN（.xmind）格式。")
                return results

            data = json.loads(z.read("content.json"))

            for sheet in data:
                # 画布名取 XMind 标签页的 title，空时兜底为"未命名画布"
                sheet_name = sheet.get("title", "").strip() or "未命名画布"
                walk(sheet.get("rootTopic", {}), sheet_name, path_parts=[], non_leaf_anc=[])

        return results

    except zipfile.BadZipFile:
        print(f"[ERROR] 无法打开文件，请确认 {xmind_path} 是有效的 .xmind 文件。")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 解析 XMind 失败：{e}")
        sys.exit(1)


def read_topics_file(path: str) -> list[str]:
    """
    从纯文本文件逐行读取主题列表。
    格式规则：# 开头的行视为注释，空行忽略，其余每行作为一个主题。
    """
    with open(path, encoding="utf-8") as f:
        topics = []
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            cleaned = strip_parenthesized(raw)
            if cleaned:
                topics.append(cleaned)
        return topics


# ─── 主流程 ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="学海计划标题图片批量生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 generate_titles.py\n"
            "  python3 generate_titles.py --sheet 医学\n"
            "  python3 generate_titles.py --sheet 医学 --path 药理学\n"
            "  python3 generate_titles.py --path 哲学/总论\n"
            "  python3 generate_titles.py --list-sheets\n"
            "  python3 generate_titles.py --xmind 其他路径.xmind\n"
            "  python3 generate_titles.py --topic \"不良反应\"\n"
            "  python3 generate_titles.py --topic \"主标题\" --topic-path \"学科/章节\"\n"
            "  python3 generate_titles.py --topic-path \"心理学/普通心理学/归因错误\"\n"
            "  python3 generate_titles.py --list topics.txt\n"
        )
    )

    parser.add_argument("--xmind",       metavar="FILE", default="../学海计划.xmind",
                        help="指定 XMind 文件路径（默认：../学海计划.xmind）")
    parser.add_argument("--sheet",       metavar="NAME",
                        help="仅处理指定名称的画布（配合 --xmind 使用）")
    # --path 与 v4 保持一致：按路径前缀做“局部更新”，避免全量重跑
    parser.add_argument("--path",        "-p", metavar="PATH",
                        help="仅处理指定路径前缀下的主题（支持 /, ->, →, __）")
    parser.add_argument("--list-sheets", action="store_true",
                        help="列出 XMind 中所有画布名称后退出，不生成图片（配合 --xmind 使用）")
    parser.add_argument("--topic",       metavar="TEXT",
                        help="生成单张图片，直接传入主题文字")
    # --topic-path 用于“单图模式下的路径化落盘”：
    # 例如：心理学/普通心理学/归因错误 -> covers/心理学/普通心理学/归因错误.png
    parser.add_argument("--topic-path",  metavar="PATH",
                        help="单图模式：指定输出目录路径；未给 --topic 时，默认用最后一段作标题并作为文件名")
    parser.add_argument("--list",        metavar="FILE",  nargs="?", const="topics.txt",
                        help="从文本文件批量生成（默认 topics.txt）")

    args = parser.parse_args()
    # 模式优先级（从高到低）：
    #   --list-sheets  >  XMind 批量模式  >  单图模式(--topic/--topic-path)  >  文本列表模式

    # ── 模式一：列出画布名称 ──────────────────────────────────
    # 仅打印画布列表，方便用户确认 --sheet 参数应填写的名称，不生成任何图片
    if args.list_sheets:
        if not args.xmind:
            print("[ERROR] --list-sheets 需要配合 --xmind 使用。")
            sys.exit(1)
        items  = parse_xmind(args.xmind)
        # 用 dict.fromkeys 去重且保持原始顺序（Python 3.7+ 字典有序）
        sheets = list(dict.fromkeys(s for s, _, _, _, _ in items))
        print(f"共 {len(sheets)} 个画布：")
        for i, name in enumerate(sheets, 1):
            count = sum(1 for s, _, _, _, _ in items if s == name)
            print(f"  {i:>3}. {name}  ({count} 个主题)")
        return

    # ── 加载字体（所有生成模式共用） ─────────────────────────
    font = load_font(FONT_SIZE)
    print(f"字体加载成功：{FONT_PATH}")
    print(f"输出目录：{OUTPUT_DIR}/\n")

    # ── 模式二：从 XMind 文件生成 ────────────────────────────
    if args.xmind:
        all_items = parse_xmind(args.xmind)

        if args.sheet:
            # 过滤：只保留与 --sheet 完全匹配的画布条目
            items = [it for it in all_items if it[0] == args.sheet]
            if not items:
                # 找不到时列出可用画布名，方便用户核对拼写
                all_sheets = list(dict.fromkeys(s for s, _, _, _, _ in all_items))
                print(f"[ERROR] 未找到名为「{args.sheet}」的画布。")
                print("        可用的画布名称（可用 --list-sheets 查看完整列表）：")
                for name in all_sheets:
                    print(f"          - {name}")
                sys.exit(1)
            print(f"仅处理画布「{args.sheet}」，共 {len(items)} 个主题\n")
        else:
            # 未指定 --sheet，处理全部画布
            items  = all_items
            sheets = list(dict.fromkeys(s for s, _, _, _, _ in items))
            print(f"处理全部 {len(sheets)} 个画布，共 {len(items)} 个主题\n")

        if args.path:
            # XMind 模式的增量更新入口：只生成命中该路径前缀的封面
            filtered, matched_prefix = filter_items_by_prefix(items, args.path, sheet_hint=args.sheet or "")
            if not filtered:
                print(f"[ERROR] 路径过滤未命中：{args.path}")
                sys.exit(1)
            items = filtered
            print(f"路径过滤：{'/'.join(matched_prefix)}，命中 {len(items)} 个主题\n")

        for sheet, topic, _, folder_parts, _ in items:
            img = make_image(topic, font)
            # 这里做“画布目录 + 树目录”的首层去重（避免 法学/法学）
            save_image(img, topic, subfolders=merge_sheet_and_tree_folders(sheet, folder_parts))

        print(f"\n全部完成，共生成 {len(items)} 张图片。")
        return

    # ── 模式三：生成单张 ──────────────────────────────────────
    if args.topic or args.topic_path:
        # 单图模式下，允许只给路径：最后一段作为展示标题和文件名
        path_parts = [strip_parenthesized(p) for p in parse_path_expr(args.topic_path or "")]
        path_parts = [p for p in path_parts if p]

        if args.topic:
            topic = strip_parenthesized(args.topic)
        elif path_parts:
            topic = path_parts[-1]
        else:
            topic = ""

        if not topic:
            print("[ERROR] 主题在去除括号内容后为空，请提供更明确的主题文字。")
            sys.exit(1)

        # 只给 --topic-path 时，最后一段用于文件名，其余作为输出目录
        # 同时给了 --topic 和 --topic-path 时，--topic-path 全部作为目录片段
        if args.topic and path_parts:
            subfolders = path_parts
        elif path_parts:
            subfolders = path_parts[:-1]
        else:
            subfolders = []

        img = make_image(topic, font)
        save_image(img, topic, subfolders=subfolders)
        print("\n完成。")
        return

    # ── 模式四：从文本文件批量生成 ───────────────────────────
    list_path = args.list or "topics.txt"
    if not Path(list_path).exists():
        # 自动创建示例文件，降低首次使用门槛
        Path(list_path).write_text(
            "# 每行一个主题，# 开头为注释，空行忽略\n"
            "不良反应\n药代动力学\n受体理论\n",
            encoding="utf-8"
        )
        print(f"已创建示例文件 {list_path}，请填写主题后重新运行。")
        return

    topics = read_topics_file(list_path)
    print(f"从 {list_path} 读取到 {len(topics)} 个主题\n")
    for topic in topics:
        img = make_image(topic, font)
        save_image(img, topic)
    print(f"\n全部完成，共生成 {len(topics)} 张图片。")


if __name__ == "__main__":
    main()
