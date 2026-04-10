#!/usr/bin/env python3
"""
批量将文章中的本地图片路径替换为 GitHub Raw URL。

定位规则：
1. 只处理 `## 写在前面` 与 `## 封面图` 两个标题块里的第一张 Markdown 图片
2. 文章的真实主题路径优先取正文中的 `本文讨论：...。`
3. 如果正文里还没有 `本文讨论：...`，则退回到“文件路径”定位 XMind 节点
4. 主题路径以 `学海计划.xmind` 为准解析
5. 路径图使用 `maps` 的树形落盘规则，封面图使用 `covers` 的树形落盘规则

补写规则：
- 如果文件为空，会直接生成一个最小可编辑模板
- 如果缺少 `## 写在前面` / `## 封面图`，会自动插入，而不是跳过
- `## 封面图` 块会确保包含 `> 设计师 | 南国微雪`

默认行为：
- 仅 dry-run，不写回文件
- 默认跳过文件名带 `【新增】` 的草稿

示例：
  python3 scripts/replace_article_image_urls.py
  python3 scripts/replace_article_image_urls.py --write
  python3 scripts/replace_article_image_urls.py --write 哲学/总论/哲学的定义.md
  python3 scripts/replace_article_image_urls.py --include-new --write 金融学
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "TinySnow/GithubImageHosting/main/blog/patchouli-project"
)
SUPPORTED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
DEFAULT_DESIGNER_LINE = "> 设计师 | 南国微雪"
HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
# Markdown 图片匹配：
# - 支持常规写法：![](url)
# - 支持尖括号目的地：![](<url>)（可包含中文、空格、括号）
IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<url><[^>\n]+>|[^)\n]+)\s*\)"
)
DISCUSSION_RE = re.compile(r"本文讨论：\s*(?P<topic>.+?)(?:。|\n)")
H1_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
DESIGNER_RE = re.compile(r"(?m)^>\s*设计师\b.*$")
EXCLUDED_DIRS = {".git", "covers", "fonts", "maps", "scripts", "venv"}


@dataclass
class TopicNode:
    sheet_name: str
    raw_title: str
    clean_title: str
    parent: TopicNode | None = None
    children: list["TopicNode"] = field(default_factory=list)

    @property
    def depth(self) -> int:
        depth = 0
        curr = self.parent
        while curr is not None:
            depth += 1
            curr = curr.parent
        return depth

    @property
    def is_non_leaf(self) -> bool:
        return bool(self.children)

    def path_nodes(self) -> list["TopicNode"]:
        nodes: list[TopicNode] = []
        curr: TopicNode | None = self
        while curr is not None:
            nodes.append(curr)
            curr = curr.parent
        nodes.reverse()
        return nodes

    def title_candidates(self) -> list[str]:
        titles: list[str] = []
        for title in (self.raw_title, self.clean_title):
            if title and title not in titles:
                titles.append(title)
        return titles


@dataclass(frozen=True)
class SectionChange:
    name: str
    old_url: str
    new_url: str
    action: str = "replace"


def strip_parenthesized(text: str) -> str:
    cleaned = re.sub(r"\s*(?:（.*?）|\(.*?\))\s*", "", text or "")
    last_colon = max(cleaned.rfind("："), cleaned.rfind(":"))
    if last_colon != -1:
        cleaned = cleaned[:last_colon]
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def cover_sanitize(name: str) -> str:
    return (
        (name or "")
        .replace("/", "／")
        .replace("\\", "＼")
        .replace(":", "：")
        .replace("*", "＊")
        .replace("?", "？")
        .replace('"', "＂")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("|", "｜")
        .strip()
    )


def map_sanitize(name: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text or "未命名节点"


def cover_filename_base(title: str) -> str:
    base = cover_sanitize((title or "").strip()) or "未命名主题"
    return base


def map_filename_base(raw_title: str) -> str:
    cleaned = strip_parenthesized(raw_title)
    base = map_sanitize(cleaned if cleaned else raw_title)
    while len(base.encode("utf-8")) > 220:
        base = base[:-1]
    return base


def merge_cover_folders(sheet_name: str, tree_folders: list[str]) -> list[str]:
    parts = [sheet_name, *(tree_folders or [])]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        left = cover_sanitize(strip_parenthesized(parts[0]) or parts[0])
        right = cover_sanitize(strip_parenthesized(parts[1]) or parts[1])
        if left == right:
            parts.pop(1)
    return parts


def merge_map_folders(sheet_name: str, tree_folders: list[str]) -> list[str]:
    parts = [sheet_name, *(tree_folders or [])]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and map_sanitize(parts[0]) == map_sanitize(parts[1]):
        parts.pop(1)
    return parts


def load_xmind_roots(xmind_path: Path) -> list[TopicNode]:
    try:
        with zipfile.ZipFile(xmind_path) as archive:
            if "content.json" not in archive.namelist():
                raise RuntimeError(f"{xmind_path} 内未找到 content.json")
            sheets = json.loads(archive.read("content.json"))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"{xmind_path} 不是有效的 .xmind 文件") from exc

    roots: list[TopicNode] = []

    def build(node_dict: dict, sheet_name: str, parent: TopicNode | None = None) -> TopicNode | None:
        raw_title = (node_dict.get("title") or "").strip()
        if not raw_title or "You are here" in raw_title:
            return None

        clean_title = strip_parenthesized(raw_title) or raw_title
        node = TopicNode(
            sheet_name=sheet_name,
            raw_title=raw_title,
            clean_title=clean_title,
            parent=parent,
        )

        attached = node_dict.get("children", {}).get("attached", [])
        for child_dict in attached:
            child = build(child_dict, sheet_name=sheet_name, parent=node)
            if child is not None:
                node.children.append(child)

        return node

    for sheet in sheets:
        sheet_name = (sheet.get("title") or "").strip() or "未命名画布"
        root = build(sheet.get("rootTopic", {}), sheet_name=sheet_name)
        if root is not None:
            roots.append(root)

    return roots


def match_discussion(node: TopicNode, discussion: str) -> list[TopicNode]:
    matches: list[TopicNode] = []
    for title in node.title_candidates():
        if not discussion.startswith(title):
            continue

        remainder = discussion[len(title) :]
        if not remainder:
            matches.append(node)
            continue

        if not remainder.startswith("-"):
            continue

        tail = remainder[1:]
        for child in node.children:
            matches.extend(match_discussion(child, tail))

    return matches


def dedupe_nodes(nodes: Iterable[TopicNode]) -> list[TopicNode]:
    unique: list[TopicNode] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for node in nodes:
        key = (node.sheet_name, tuple(n.raw_title for n in node.path_nodes()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(node)
    return unique


def extract_article_title(content: str, article_path: Path) -> str:
    """
    取文章标题。

    优先使用文中的 H1；空文件或未写标题时，再退回到文件名。
    """
    match = H1_RE.search(content)
    if match:
        return match.group(1).strip()
    return article_path.stem.replace("【新增】", "").strip()


def normalize_article_part(text: str) -> str:
    """
    将文件路径里的目录名/文件名归一化到较稳定的比较口径。

    处理点：
    - 去掉 `【新增】`
    - 去掉首尾空白
    - 去掉括号与“冒号后副标题”
    """
    text = (text or "").replace("【新增】", "").strip()
    return strip_parenthesized(text) or text


def path_part_variants(text: str) -> list[str]:
    """
    为单个路径片段生成一组候选值，用于和 XMind 标题比对。

    例如：
    - `计划概述` -> `计划概述`, `计划`
    - `【新增】第一篇 基础与大脑：你是谁？` -> 清洗后的稳定版本
    """
    raw = (text or "").replace("【新增】", "").strip()
    normalized = normalize_article_part(raw)
    variants = [value for value in (raw, normalized) if value]

    for base in list(variants):
        for suffix in ("概述", "导论", "导读", "绪论"):
            if base.endswith(suffix) and len(base) > len(suffix):
                variants.append(base[: -len(suffix)].strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for value in variants:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def part_matches_node(part: str, node: TopicNode) -> bool:
    """
    判断“文件路径片段”是否可以视为该 XMind 节点。

    这里不要求完全相等，而是比较多种稳定候选：
    - 原始标题
    - 清洗后标题
    - 路径片段的变体（如去掉“概述”）
    """
    node_titles = set(node.title_candidates())
    node_titles.add(normalize_article_part(node.raw_title))
    node_titles.add(normalize_article_part(node.clean_title))

    for variant in path_part_variants(part):
        if variant in node_titles:
            return True
    return False


def resolve_discussion_once(roots: list[TopicNode], discussion: str, article_title: str) -> TopicNode | None:
    all_matches: list[TopicNode] = []
    for root in roots:
        all_matches.extend(match_discussion(root, discussion))

    matches = dedupe_nodes(all_matches)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    title = strip_parenthesized(article_title) or article_title
    narrowed = []
    for node in matches:
        if title in node.title_candidates():
            narrowed.append(node)
            continue
        if title.endswith(node.clean_title) or title.endswith(node.raw_title):
            narrowed.append(node)

    narrowed = dedupe_nodes(narrowed)
    if len(narrowed) == 1:
        return narrowed[0]

    deepest = max(node.depth for node in matches)
    deepest_nodes = [node for node in matches if node.depth == deepest]
    deepest_nodes = dedupe_nodes(deepest_nodes)
    if len(deepest_nodes) == 1:
        return deepest_nodes[0]

    return None


def discussion_variants(discussion: str) -> list[str]:
    """为 `本文讨论` 生成若干兜底写法，处理末尾“概述/导论/导读/绪论”等差异。"""
    variants = [discussion]
    for suffix in ("-概述", "-导论", "-导读", "-绪论"):
        if discussion.endswith(suffix):
            variants.append(discussion[: -len(suffix)])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def resolve_discussion(roots: list[TopicNode], discussion: str, article_title: str) -> TopicNode | None:
    """按 `本文讨论：...` 去 XMind 里找唯一节点。"""
    for variant in discussion_variants(discussion):
        resolved = resolve_discussion_once(roots, variant, article_title)
        if resolved is not None:
            return resolved
    return None


def resolve_from_article_path_once(node: TopicNode, parts: list[str], index: int) -> list[TopicNode]:
    """
    递归地用“仓库里的文章路径”匹配 XMind 节点路径。

    这是空文件或尚未写 `本文讨论` 时的兜底。
    """
    if index >= len(parts) or not part_matches_node(parts[index], node):
        return []

    # 正好消费完所有路径片段，当前节点就是候选结果。
    if index == len(parts) - 1:
        return [node]

    matches: list[TopicNode] = []
    for child in node.children:
        matches.extend(resolve_from_article_path_once(child, parts, index + 1))

    if matches:
        return matches

    # 处理“非叶子文章文件位于同名文件夹内”的情况：
    # 例如 `心理学/心理学.md`、`保险概述/保险概述.md`。
    if part_matches_node(parts[index + 1], node):
        return [node]

    return []


def article_path_candidates(article_path: Path, repo_root: Path) -> list[list[str]]:
    """
    将文章相对路径转成若干可尝试的“主题路径候选”。

    例：
    - `哲学/总论/哲学的定义.md` -> `["哲学", "总论", "哲学的定义"]`
    - `心理学/心理学.md` -> 同时保留 `["心理学", "心理学"]` 与 `["心理学"]`
    """
    rel = article_path.relative_to(repo_root)
    parts = list(rel.parts[:-1]) + [article_path.stem]
    candidates = [parts]

    # 非叶子节点的文章通常是 `目录名/目录名.md`，去掉尾部重复值再试一次。
    if len(parts) >= 2 and normalize_article_part(parts[-1]) == normalize_article_part(parts[-2]):
        candidates.append(parts[:-1])

    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def resolve_from_article_path(roots: list[TopicNode], article_path: Path, repo_root: Path) -> TopicNode | None:
    """按文章文件路径去 XMind 中定位节点。"""
    for parts in article_path_candidates(article_path, repo_root):
        all_matches: list[TopicNode] = []
        for root in roots:
            all_matches.extend(resolve_from_article_path_once(root, parts, 0))

        matches = dedupe_nodes(all_matches)
        if len(matches) == 1:
            return matches[0]
        if matches:
            deepest = max(node.depth for node in matches)
            deepest_nodes = [node for node in matches if node.depth == deepest]
            deepest_nodes = dedupe_nodes(deepest_nodes)
            if len(deepest_nodes) == 1:
                return deepest_nodes[0]

    return None


def build_map_stem(node: TopicNode) -> Path:
    path_nodes = node.path_nodes()
    folder_parts_raw = [item.raw_title for item in path_nodes if item.is_non_leaf]
    merged = merge_map_folders(node.sheet_name, folder_parts_raw)
    return Path("maps", *[map_sanitize(part) for part in merged], map_filename_base(node.raw_title))


def build_cover_stem(node: TopicNode) -> Path:
    path_nodes = node.path_nodes()
    folder_parts_clean = [item.clean_title for item in path_nodes if item.is_non_leaf]
    merged = merge_cover_folders(node.sheet_name, folder_parts_clean)
    return Path("covers", *[cover_sanitize(part) for part in merged], cover_filename_base(node.clean_title))


def find_asset_path(repo_root: Path, stem: Path) -> Path | None:
    for ext in SUPPORTED_EXTS:
        candidate = repo_root / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def path_to_url(base_url: str, relative_path: Path) -> str:
    """
    组装 GitHub Raw URL。

    这里刻意不做 `%` 编码，保留中文可读性。
    """
    return f"{base_url.rstrip('/')}/{relative_path.as_posix()}"


def markdown_url_destination(url: str) -> str:
    """
    将 URL 转成 Markdown 链接目标。

    使用 `<...>` 包裹，确保带空格/中文/括号时 Markdown 仍能稳定解析。
    """
    stripped = (url or "").strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return stripped
    return f"<{stripped}>"


def extract_discussion_topic(content: str) -> str | None:
    match = DISCUSSION_RE.search(content)
    if not match:
        return None
    return match.group("topic").strip()


def find_section_bounds(content: str, section_name: str) -> tuple[int, int, int, int] | None:
    """
    返回某个二级标题块的位置：
    - heading_start / heading_end
    - body_start / body_end
    """
    headings = list(HEADING_RE.finditer(content))
    for index, heading in enumerate(headings):
        if heading.group(1).strip() != section_name:
            continue

        body_start = heading.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        return heading.start(), heading.end(), body_start, body_end
    return None


def splice_block(content: str, insert_pos: int, block: str) -> str:
    """在指定位置插入一个 Markdown 标题块，并统一前后空行。"""
    prefix = content[:insert_pos].rstrip("\n")
    suffix = content[insert_pos:].lstrip("\n")
    pieces = [piece for piece in (prefix, block.rstrip("\n"), suffix) if piece]
    return "\n\n".join(pieces) + "\n"


def insert_missing_section(content: str, section_name: str, new_url: str) -> str:
    """
    插入缺失的图片标题块。

    规则：
    - `写在前面`：优先插到 `## 正文` 之前；没有正文就插到 H1 后
    - `封面图`：总是追加到文末
    """
    block = f"## {section_name}\n\n![]({markdown_url_destination(new_url)})\n"
    if section_name == "封面图":
        block = f"{block}\n{DEFAULT_DESIGNER_LINE}\n"

    if section_name == "封面图":
        base = content.rstrip("\n")
        return f"{base}\n\n{block}" if base else block

    body_heading = re.search(r"(?m)^##\s+正文\s*$", content)
    if body_heading:
        return splice_block(content, body_heading.start(), block)

    h1 = H1_RE.search(content)
    if h1:
        return splice_block(content, h1.end(), block)

    return splice_block(content, 0, block)


def upsert_section_image(content: str, section_name: str, new_url: str) -> tuple[str, SectionChange | None]:
    """
    更新或补写某个图片标题块。

    分三种情况：
    1. 标题块存在且已有图片：替换 URL
    2. 标题块存在但没写图片：补一张图片进去
    3. 标题块不存在：直接插入整个标题块
    """
    new_dest = markdown_url_destination(new_url)
    bounds = find_section_bounds(content, section_name)
    if bounds is None:
        updated = insert_missing_section(content, section_name, new_url)
        return updated, SectionChange(
            name=section_name,
            old_url="<missing section>",
            new_url=new_url,
            action="insert",
        )

    _, _, body_start, body_end = bounds
    body = content[body_start:body_end]
    image_match = IMAGE_RE.search(body)
    if image_match is None:
        new_body = f"\n\n![]({new_dest})" + body
        updated = content[:body_start] + new_body + content[body_end:]
        return updated, SectionChange(
            name=section_name,
            old_url="<missing image>",
            new_url=new_url,
            action="insert",
        )

    # 为避免“多次运行后图片行残留脏尾巴”问题，统一重写整行图片语法，
    # 而不是只替换 URL 片段。
    line_start = body.rfind("\n", 0, image_match.start()) + 1
    line_end = body.find("\n", image_match.end())
    if line_end == -1:
        line_end = len(body)

    current_line = body[line_start:line_end]
    line_indent = re.match(r"[ \t]*", current_line).group(0)
    canonical_line = f"{line_indent}![{image_match.group('alt')}]({new_dest})"
    old_url = image_match.group("url").strip()

    # URL 与整行都一致时不写回，保证幂等。
    if old_url == new_dest and current_line == canonical_line:
        return content, None

    new_body = body[:line_start] + canonical_line + body[line_end:]
    updated = content[:body_start] + new_body + content[body_end:]
    change = SectionChange(name=section_name, old_url=old_url, new_url=new_url, action="replace")
    return updated, change


def ensure_cover_designer_line(content: str) -> tuple[str, bool]:
    """
    确保 `## 封面图` 标题块中包含设计师信息行。

    仅在缺失时补一行，已有 `> 设计师 ...` 时不改动原文。
    """
    bounds = find_section_bounds(content, "封面图")
    if bounds is None:
        return content, False

    _, _, body_start, body_end = bounds
    body = content[body_start:body_end]
    if DESIGNER_RE.search(body):
        return content, False

    patched_body = body.rstrip("\n") + "\n\n" + DEFAULT_DESIGNER_LINE + "\n"
    updated = content[:body_start] + patched_body + content[body_end:]
    return updated, True


def build_empty_article_template(article_title: str, map_url: str | None, cover_url: str | None) -> tuple[str, list[SectionChange]]:
    """
    为空文件生成最小模板，避免脚本遇到空稿时只能跳过。

    模板结构：
    - H1
    - `## 写在前面`
    - `## 正文`
    - `## 封面图`
    """
    lines = [f"# {article_title}", ""]
    changes: list[SectionChange] = []

    if map_url is not None:
        lines.extend(["## 写在前面", "", f"![]({markdown_url_destination(map_url)})", ""])
        changes.append(
            SectionChange(
                name="写在前面",
                old_url="<empty file>",
                new_url=map_url,
                action="insert",
            )
        )

    lines.extend(["## 正文", "", ""])

    if cover_url is not None:
        lines.extend(
            [
                "## 封面图",
                "",
                f"![]({markdown_url_destination(cover_url)})",
                "",
                DEFAULT_DESIGNER_LINE,
                "",
            ]
        )
        changes.append(
            SectionChange(
                name="封面图",
                old_url="<empty file>",
                new_url=cover_url,
                action="insert",
            )
        )

    return "\n".join(lines).rstrip() + "\n", changes


def wrap_content_with_standard_sections(
    content: str,
    article_title: str,
    map_url: str | None,
    cover_url: str | None,
) -> tuple[str, list[SectionChange]]:
    """
    对“已有正文，但缺少两大图片块”的文件补出标准结构。

    这类文件常见于只写了 H1 和一句简介的入口页。
    """
    h1_match = H1_RE.search(content)
    title_line = h1_match.group(0) if h1_match else f"# {article_title}"
    body = content[h1_match.end() :] if h1_match else content
    body = body.strip("\n")

    lines = [title_line, ""]
    changes: list[SectionChange] = []

    if map_url is not None:
        lines.extend(["## 写在前面", "", f"![]({markdown_url_destination(map_url)})", ""])
        changes.append(
            SectionChange(
                name="写在前面",
                old_url="<missing section>",
                new_url=map_url,
                action="insert",
            )
        )

    lines.extend(["## 正文", ""])
    if body:
        lines.extend([body, ""])
    else:
        lines.append("")

    if cover_url is not None:
        lines.extend(
            [
                "## 封面图",
                "",
                f"![]({markdown_url_destination(cover_url)})",
                "",
                DEFAULT_DESIGNER_LINE,
                "",
            ]
        )
        changes.append(
            SectionChange(
                name="封面图",
                old_url="<missing section>",
                new_url=cover_url,
                action="insert",
            )
        )

    return "\n".join(lines).rstrip() + "\n", changes


def resolve_node_for_article(
    roots: list[TopicNode],
    article_path: Path,
    repo_root: Path,
    content: str,
    article_title: str,
) -> tuple[TopicNode | None, str]:
    """
    统一封装“文章 -> XMind 节点”的定位逻辑。

    顺序：
    1. 优先按 `本文讨论：...`
    2. 再按文件路径兜底
    """
    discussion = extract_discussion_topic(content)
    if discussion:
        node = resolve_discussion(roots, discussion, article_title)
        if node is not None:
            return node, "discussion"

    node = resolve_from_article_path(roots, article_path, repo_root)
    if node is not None:
        return node, "path"

    return None, "unresolved"


def expand_targets(repo_root: Path, raw_targets: list[str], include_new: bool) -> list[Path]:
    paths: list[Path] = []

    def should_skip(path: Path) -> bool:
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            rel = path
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            return True
        if not include_new and "【新增】" in path.name:
            return True
        return False

    def collect(path: Path) -> None:
        if path.is_file():
            if path.suffix.lower() == ".md" and not should_skip(path):
                paths.append(path)
            return

        if path.is_dir():
            for child in sorted(path.rglob("*.md")):
                if not should_skip(child):
                    paths.append(child)
            return

        raise FileNotFoundError(path)

    if raw_targets:
        for raw_target in raw_targets:
            target = Path(raw_target)
            if not target.is_absolute():
                target = repo_root / target
            collect(target.resolve())
    else:
        for child in sorted(repo_root.rglob("*.md")):
            if not should_skip(child):
                paths.append(child)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def process_file(
    article_path: Path,
    repo_root: Path,
    roots: list[TopicNode],
    base_url: str,
    write: bool,
) -> tuple[str, list[SectionChange], list[str]]:
    """
    处理单篇文章。

    输出状态：
    - `updated`   : 本次会改动文件
    - `unchanged` : 已经是目标状态
    - `skipped`   : 无法定位节点，或地图/封面都不存在
    """
    content = article_path.read_text(encoding="utf-8")
    warnings: list[str] = []
    article_title = extract_article_title(content, article_path)
    node, resolved_by = resolve_node_for_article(
        roots=roots,
        article_path=article_path,
        repo_root=repo_root,
        content=content,
        article_title=article_title,
    )
    if node is None:
        return "skipped", [], ["无法通过 `本文讨论` 或文件路径在 XMind 中定位主题"]

    map_asset = find_asset_path(repo_root, build_map_stem(node))
    cover_asset = find_asset_path(repo_root, build_cover_stem(node))
    if map_asset is None:
        warnings.append("未找到对应的路径图文件")
    if cover_asset is None:
        warnings.append("未找到对应的封面图文件")
    if map_asset is None and cover_asset is None:
        return "skipped", [], warnings

    # 先把本地图片路径换算成最终 GitHub Raw URL。
    map_url = None
    if map_asset is not None:
        rel_map = map_asset.relative_to(repo_root)
        map_url = path_to_url(base_url, rel_map)

    cover_url = None
    if cover_asset is not None:
        rel_cover = cover_asset.relative_to(repo_root)
        cover_url = path_to_url(base_url, rel_cover)

    updated = content
    changes: list[SectionChange] = []
    has_front = "## 写在前面" in content
    has_cover = "## 封面图" in content

    # 空文件：直接生成最小模板，不再要求用户先手写两个标题块。
    if not content.strip():
        updated, changes = build_empty_article_template(article_title, map_url, cover_url)
    # 非空但两大图片区块都没有：补成统一结构，保留原正文。
    elif not has_front and not has_cover and "## 正文" not in content:
        updated, changes = wrap_content_with_standard_sections(content, article_title, map_url, cover_url)
    else:
        # 常规情况：有块就替换，没块就补进去。
        if map_url is not None:
            updated, change = upsert_section_image(updated, "写在前面", map_url)
            if change is not None:
                changes.append(change)

        if cover_url is not None:
            updated, change = upsert_section_image(updated, "封面图", cover_url)
            if change is not None:
                changes.append(change)

    # 统一兜底：封面图块里若缺失设计师信息，就补上。
    updated, designer_added = ensure_cover_designer_line(updated)
    if designer_added:
        changes.append(
            SectionChange(
                name="封面图设计师信息",
                old_url="<missing>",
                new_url=DEFAULT_DESIGNER_LINE,
                action="insert",
            )
        )

    if not changes:
        if resolved_by == "path":
            warnings.append("未写 `本文讨论`，本次是按文件路径推断到 XMind 节点的")
        return "unchanged", [], warnings

    if write:
        article_path.write_text(updated, encoding="utf-8")

    if resolved_by == "path":
        warnings.append("未写 `本文讨论`，本次是按文件路径推断到 XMind 节点的")
    return "updated", changes, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="批量替换文章里的路径图/封面图链接")
    parser.add_argument(
        "targets",
        nargs="*",
        help="要处理的 Markdown 文件或目录；留空则扫描整个仓库",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="写回文件；默认只 dry-run",
    )
    parser.add_argument(
        "--include-new",
        action="store_true",
        help="包含文件名带 `【新增】` 的草稿",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="仓库根目录，默认当前目录",
    )
    parser.add_argument(
        "--xmind",
        default="学海计划.xmind",
        help="XMind 文件路径，默认 `学海计划.xmind`",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="GitHub Raw URL 的前缀",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    xmind_path = Path(args.xmind)
    if not xmind_path.is_absolute():
        xmind_path = repo_root / xmind_path
    xmind_path = xmind_path.resolve()

    try:
        roots = load_xmind_roots(xmind_path)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        targets = expand_targets(repo_root, args.targets, include_new=args.include_new)
    except FileNotFoundError as exc:
        print(f"[ERROR] 目标不存在：{exc}", file=sys.stderr)
        return 1

    if not targets:
        print("没有找到可处理的 Markdown 文件。")
        return 0

    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"[{mode}] XMind: {xmind_path}")
    print(f"[{mode}] Repo : {repo_root}")
    print(f"[{mode}] Files: {len(targets)}")

    updated_count = 0
    unchanged_count = 0
    skipped_count = 0
    warned_count = 0

    for article_path in targets:
        status, changes, warnings = process_file(
            article_path=article_path,
            repo_root=repo_root,
            roots=roots,
            base_url=args.base_url,
            write=args.write,
        )

        rel_path = article_path.relative_to(repo_root)
        if status == "updated":
            updated_count += 1
            print(f"[UPDATE] {rel_path}")
            for change in changes:
                print(f"  - {change.name} ({change.action}):")
                print(f"    old: {change.old_url}")
                print(f"    new: {change.new_url}")
        elif status == "unchanged":
            unchanged_count += 1
        else:
            skipped_count += 1
            print(f"[SKIP]   {rel_path}")

        if warnings:
            warned_count += 1
            for warning in warnings:
                print(f"  ! {warning}")

    print(
        "\n完成："
        f" updated={updated_count},"
        f" unchanged={unchanged_count},"
        f" skipped={skipped_count},"
        f" warned={warned_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
