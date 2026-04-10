#!/usr/bin/env python3
"""
根据学海计划.xmind，同步根目录各学科下 Markdown 文件的树结构。

目标：
1. 只做“重命名/移动”，不新建文章文件。
2. 让可确定的文件路径与 XMind 树保持一致。
3. 无法唯一定位的文件保持不动，并输出清单，避免误改。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    # 以仓库根目录执行时可用（python -m / REPL 场景）
    from scripts.replace_article_image_urls import (
        TopicNode,
        extract_article_title,
        extract_discussion_topic,
        load_xmind_roots,
        resolve_discussion,
        resolve_from_article_path,
        strip_parenthesized,
    )
except ModuleNotFoundError:
    # 直接执行脚本时可用（python3 scripts/sync_subject_tree_with_xmind.py）
    from replace_article_image_urls import (  # type: ignore
        TopicNode,
        extract_article_title,
        extract_discussion_topic,
        load_xmind_roots,
        resolve_discussion,
        resolve_from_article_path,
        strip_parenthesized,
    )


SUBJECTS = {
    "亲密关系",
    "医学",
    "哲学",
    "心理学",
    "文学",
    "法学",
    "税法",
    "管理学",
    "经济学",
    "自我提升",
    "药理学",
    "语言学",
    "金融学",
}

INVALID_FS_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
SPACE_RE = re.compile(r"\s+")
MARKUP_RE = re.compile(r"[*`_]+")
COPY_SUFFIX_RE = re.compile(r"^(.*?)[\s　]*\((\d+)\)$")
# 识别冲突后缀（用于匹配阶段忽略后缀噪声）
CONFLICT_SUFFIX_RE = re.compile(r"^(.*?)(?:__(?:冲突|dup)\d+)$")


@dataclass(frozen=True)
class NodeKey:
    """可哈希的节点标识：同一 sheet 下的完整标题路径。"""

    sheet_name: str
    path: tuple[str, ...]


@dataclass
class UnresolvedItem:
    """记录无法唯一定位的文件，供人工后续处理。"""

    path: Path
    reason: str
    article_title: str
    discussion: str | None


def iter_nodes(node: TopicNode) -> Iterable[TopicNode]:
    """深度优先遍历节点树。"""
    yield node
    for child in node.children:
        yield from iter_nodes(child)


def to_node_key(node: TopicNode) -> NodeKey:
    """将 TopicNode 转为可哈希键。"""
    return NodeKey(
        sheet_name=node.sheet_name,
        path=tuple(part.raw_title for part in node.path_nodes()),
    )


def fs_sanitize(name: str) -> str:
    """
    将 XMind 标题转换为文件系统安全名称。

    说明：
    - 仅替换文件系统非法字符，不做额外语义裁剪。
    - 连续空白折叠为一个空格，避免肉眼不可见差异。
    """
    text = INVALID_FS_CHARS_RE.sub("_", (name or "").strip())
    text = SPACE_RE.sub(" ", text).strip()
    return text or "未命名节点"


def lookup_normalize(text: str) -> str:
    """
    归一化字符串，用于“唯一标题”匹配。

    处理点：
    - 去掉 `【新增】`
    - 去掉轻量 Markdown 包装符
    - 中英文括号/冒号统一
    - 去空白并小写
    """
    value = (text or "").replace("【新增】", "").strip()
    value = MARKUP_RE.sub("", value)
    value = value.replace("（", "(").replace("）", ")").replace("：", ":")
    value = SPACE_RE.sub("", value).lower()
    return value


def text_key_variants(text: str, *, allow_loose: bool) -> set[str]:
    """
    为一个标题生成查找 key。

    - strict: 只保留原始语义（用于高置信匹配）
    - loose: 额外加“去括号/冒号副标题”的宽松写法（用于兜底）
    """
    raw = (text or "").replace("【新增】", "").strip()
    variants = {raw, SPACE_RE.sub("", raw)}

    # 处理 "标题 (2)" 这类复制后缀。
    match = COPY_SUFFIX_RE.match(raw)
    if match:
        base = match.group(1).strip()
        variants.add(base)
        variants.add(SPACE_RE.sub("", base))

    # 处理冲突后缀：`xxx__冲突2` / `xxx__dup2`。
    suffix_match = CONFLICT_SUFFIX_RE.match(raw)
    if suffix_match:
        base = suffix_match.group(1).strip()
        variants.add(base)
        variants.add(SPACE_RE.sub("", base))

    if allow_loose:
        loose = strip_parenthesized(raw)
        if loose:
            variants.add(loose)
            variants.add(SPACE_RE.sub("", loose))

    keys = {lookup_normalize(item) for item in variants if item}
    keys.discard("")
    return keys


def build_title_indexes(
    roots: list[TopicNode],
) -> tuple[
    dict[str, dict[str, set[NodeKey]]],
    dict[str, dict[str, set[NodeKey]]],
    dict[NodeKey, TopicNode],
]:
    """
    构建两套索引：
    1. strict_index：raw/clean 标题
    2. loose_index：在 strict 基础上增加 strip_parenthesized 结果
    """
    strict_index: dict[str, dict[str, set[NodeKey]]] = defaultdict(lambda: defaultdict(set))
    loose_index: dict[str, dict[str, set[NodeKey]]] = defaultdict(lambda: defaultdict(set))
    node_lookup: dict[NodeKey, TopicNode] = {}

    for root in roots:
        subject = root.raw_title
        for node in iter_nodes(root):
            key = to_node_key(node)
            node_lookup[key] = node

            strict_titles = {
                node.raw_title,
                node.clean_title,
            }
            loose_titles = set(strict_titles)
            loose_titles.add(strip_parenthesized(node.raw_title))
            loose_titles.add(strip_parenthesized(node.clean_title))

            for title in strict_titles:
                normalized = lookup_normalize(title)
                if normalized:
                    strict_index[subject][normalized].add(key)
                    loose_index[subject][normalized].add(key)

            for title in loose_titles:
                normalized = lookup_normalize(title)
                if normalized:
                    loose_index[subject][normalized].add(key)

    return strict_index, loose_index, node_lookup


def resolve_by_unique_title(
    article_path: Path,
    article_title: str,
    strict_index: dict[str, dict[str, set[NodeKey]]],
    loose_index: dict[str, dict[str, set[NodeKey]]],
    node_lookup: dict[NodeKey, TopicNode],
) -> tuple[TopicNode | None, str]:
    """
    通过“同学科唯一标题”做兜底。

    返回:
    - (node, method) 成功时 node 非空
    - (None, reason) 失败时 method 为无法判定原因
    """
    subject = article_path.parts[0]

    strict_candidates: set[NodeKey] = set()
    for key in text_key_variants(article_path.stem, allow_loose=False):
        strict_candidates |= strict_index.get(subject, {}).get(key, set())
    for key in text_key_variants(article_title, allow_loose=False):
        strict_candidates |= strict_index.get(subject, {}).get(key, set())

    if len(strict_candidates) == 1:
        only = next(iter(strict_candidates))
        return node_lookup[only], "title_strict"
    if len(strict_candidates) > 1:
        return None, "title_ambiguous"

    loose_candidates: set[NodeKey] = set()
    for key in text_key_variants(article_path.stem, allow_loose=True):
        loose_candidates |= loose_index.get(subject, {}).get(key, set())
    for key in text_key_variants(article_title, allow_loose=True):
        loose_candidates |= loose_index.get(subject, {}).get(key, set())

    if len(loose_candidates) == 1:
        only = next(iter(loose_candidates))
        return node_lookup[only], "title_loose"
    if len(loose_candidates) > 1:
        return None, "title_ambiguous"

    return None, "title_unresolved"


def resolve_node_for_article(
    *,
    repo_root: Path,
    article_path: Path,
    content: str,
    roots: list[TopicNode],
    strict_index: dict[str, dict[str, set[NodeKey]]],
    loose_index: dict[str, dict[str, set[NodeKey]]],
    node_lookup: dict[NodeKey, TopicNode],
) -> tuple[TopicNode | None, str, str, str | None]:
    """
    依次尝试多级定位策略。

    优先级：
    1. `本文讨论：...`
    2. 当前文件路径
    3. 同学科唯一标题（strict -> loose）
    """
    article_title = extract_article_title(content, article_path)
    discussion = extract_discussion_topic(content)

    if discussion:
        node = resolve_discussion(roots, discussion, article_title)
        if node is not None:
            return node, "discussion", article_title, discussion

    node = resolve_from_article_path(roots, article_path, repo_root)
    if node is not None:
        return node, "path", article_title, discussion

    node, method = resolve_by_unique_title(
        article_path=article_path.relative_to(repo_root),
        article_title=article_title,
        strict_index=strict_index,
        loose_index=loose_index,
        node_lookup=node_lookup,
    )
    if node is not None:
        return node, method, article_title, discussion

    return None, method, article_title, discussion


def build_target_relative_path(node: TopicNode, source_relative_path: Path) -> Path:
    """
    按 XMind 节点生成目标路径。

    规则：
    - 叶子节点 -> 文件名
    - 非叶子节点 -> 目录名；其自身文章位于同名目录下
    - 保留原文件的 `【新增】` 前缀
    """
    path_nodes = node.path_nodes()
    subject = fs_sanitize(path_nodes[0].raw_title)

    # 目录名保持 XMind 原标题（仅做文件系统安全替换）：
    # 如果把“去括号/去冒号后缀”也用于目录，会导致同层目录大面积同名冲突。
    folder_parts = [fs_sanitize(part.raw_title) for part in path_nodes[1:-1]]
    if node.is_non_leaf:
        folder_parts.append(fs_sanitize(node.raw_title))

    draft_prefix = "【新增】" if source_relative_path.stem.startswith("【新增】") else ""
    # 仅叶子节点文件名执行“去括号 + 去最后一个冒号后缀”。
    # 非叶子节点（例如“第一章：人际关系的构成”）保持完整标题，
    # 避免章节名被截断成“第一章”。
    if node.is_non_leaf:
        filename_core = node.raw_title
    else:
        filename_core = strip_parenthesized(node.raw_title) or node.raw_title
    filename = f"{draft_prefix}{fs_sanitize(filename_core)}.md"
    return Path(subject, *folder_parts, filename)


def collect_markdown_files(repo_root: Path) -> list[Path]:
    """收集各学科目录下全部 Markdown 文件。"""
    files: list[Path] = []
    for subject in sorted(SUBJECTS):
        subject_dir = repo_root / subject
        if subject_dir.is_dir():
            files.extend(sorted(subject_dir.rglob("*.md")))
    return files


def filter_conflicts(
    repo_root: Path,
    planned_moves: dict[Path, Path],
) -> tuple[dict[Path, Path], dict[Path, str]]:
    """
    过滤会冲突的移动计划。

    冲突场景：
    - 多个源文件映射到同一个目标路径
    - 目标路径已存在，且该目标不是本批次的源路径之一
    """
    destination_to_sources: dict[Path, list[Path]] = defaultdict(list)
    for source, destination in planned_moves.items():
        destination_to_sources[destination].append(source)

    blocked_sources: dict[Path, str] = {}
    source_set = set(planned_moves.keys())

    for destination, sources in destination_to_sources.items():
        if len(sources) > 1:
            reason = f"multiple_sources_to_same_target:{destination.as_posix()}"
            for source in sources:
                blocked_sources[source] = reason
            continue

        source = sources[0]
        destination_abs = repo_root / destination
        if destination_abs.exists() and destination not in source_set:
            blocked_sources[source] = f"target_exists:{destination.as_posix()}"

    safe_moves = {
        source: destination
        for source, destination in planned_moves.items()
        if source not in blocked_sources
    }
    return safe_moves, blocked_sources


def allocate_conflict_suffix_moves(
    repo_root: Path,
    planned_moves: dict[Path, Path],
    safe_moves: dict[Path, Path],
    blocked_sources: dict[Path, str],
    conflict_suffix: str,
) -> dict[Path, Path]:
    """
    为冲突项分配“带后缀”的目标文件名。

    规则：
    - 仅修改文件名，不改目录层级
    - 从 `...__冲突1.md` 开始递增，直到命中一个未占用路径
    - 既避开当前仓库已存在文件，也避开本轮计划中的目标路径
    """
    if not conflict_suffix:
        return {}

    used_destinations: set[Path] = set()
    for file_path in repo_root.rglob("*.md"):
        used_destinations.add(file_path.relative_to(repo_root))
    used_destinations.update(safe_moves.values())

    suffix_moves: dict[Path, Path] = {}
    suffix_stem_re = re.compile(rf"^(.*?){re.escape(conflict_suffix)}(\d+)$")

    for source_rel in sorted(blocked_sources.keys(), key=lambda p: p.as_posix()):
        base_target = planned_moves[source_rel]
        parent = base_target.parent
        stem = base_target.stem
        ext = base_target.suffix

        # 已经是同一目标的“冲突后缀文件”时，保持原地不动，避免每次运行继续变号。
        if source_rel.parent == parent and source_rel.suffix == ext:
            source_match = suffix_stem_re.match(source_rel.stem)
            if source_match and source_match.group(1) == stem:
                suffix_moves[source_rel] = source_rel
                continue

        index = 1
        while True:
            candidate = parent / f"{stem}{conflict_suffix}{index}{ext}"
            if candidate not in used_destinations:
                suffix_moves[source_rel] = candidate
                used_destinations.add(candidate)
                break
            index += 1

    return suffix_moves


def execute_moves(repo_root: Path, moves: dict[Path, Path]) -> int:
    """
    执行两阶段移动，避免重命名链/循环导致覆盖。

    阶段1：源路径 -> 临时路径
    阶段2：临时路径 -> 目标路径
    """
    if not moves:
        return 0

    temp_root = repo_root / ".tmp_xmind_tree_sync"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    staged: list[tuple[Path, Path]] = []
    sorted_moves = sorted(
        moves.items(),
        key=lambda item: (len(item[0].parts), item[0].as_posix()),
        reverse=True,
    )

    for index, (source_rel, destination_rel) in enumerate(sorted_moves):
        source_abs = repo_root / source_rel
        if not source_abs.exists():
            continue

        temp_abs = temp_root / f"{index:06d}.md"
        temp_abs.parent.mkdir(parents=True, exist_ok=True)
        source_abs.rename(temp_abs)
        staged.append((temp_abs, repo_root / destination_rel))

    for temp_abs, destination_abs in staged:
        destination_abs.parent.mkdir(parents=True, exist_ok=True)
        temp_abs.rename(destination_abs)

    shutil.rmtree(temp_root, ignore_errors=True)
    return len(staged)


def remove_empty_subject_dirs(repo_root: Path) -> int:
    """删除各学科目录内移动后留下的空目录。"""
    removed = 0
    for subject in sorted(SUBJECTS):
        subject_dir = repo_root / subject
        if not subject_dir.is_dir():
            continue

        # 逆深度删除，确保先删最深层目录。
        for directory in sorted(subject_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if not directory.is_dir():
                continue
            try:
                directory.rmdir()
                removed += 1
            except OSError:
                pass

    return removed


def format_unresolved(item: UnresolvedItem) -> str:
    """输出未决项的一行文本。"""
    discussion = item.discussion if item.discussion else "<none>"
    return (
        f"{item.path.as_posix()}\t"
        f"reason={item.reason}\t"
        f"title={item.article_title}\t"
        f"discussion={discussion}"
    )


def run(
    repo_root: Path,
    xmind_path: Path,
    write: bool,
    show_unresolved: int,
    conflict_suffix: str,
) -> int:
    roots = load_xmind_roots(xmind_path)
    strict_index, loose_index, node_lookup = build_title_indexes(roots)
    markdown_files = collect_markdown_files(repo_root)

    resolved_count = 0
    aligned_count = 0
    planned_moves: dict[Path, Path] = {}
    unresolved_items: list[UnresolvedItem] = []
    method_counter: dict[str, int] = defaultdict(int)

    for file_path in markdown_files:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        node, method, article_title, discussion = resolve_node_for_article(
            repo_root=repo_root,
            article_path=file_path,
            content=content,
            roots=roots,
            strict_index=strict_index,
            loose_index=loose_index,
            node_lookup=node_lookup,
        )

        if node is None:
            unresolved_items.append(
                UnresolvedItem(
                    path=file_path.relative_to(repo_root),
                    reason=method,
                    article_title=article_title,
                    discussion=discussion,
                )
            )
            method_counter[method] += 1
            continue

        method_counter[method] += 1
        resolved_count += 1

        source_rel = file_path.relative_to(repo_root)
        destination_rel = build_target_relative_path(node, source_rel)
        if source_rel == destination_rel:
            aligned_count += 1
            continue

        planned_moves[source_rel] = destination_rel

    safe_moves, blocked_sources = filter_conflicts(repo_root, planned_moves)

    suffix_moves = allocate_conflict_suffix_moves(
        repo_root=repo_root,
        planned_moves=planned_moves,
        safe_moves=safe_moves,
        blocked_sources=blocked_sources,
        conflict_suffix=conflict_suffix,
    )
    suffix_noop_count = sum(1 for source, dest in suffix_moves.items() if source == dest)
    suffix_rename_moves = {
        source: dest for source, dest in suffix_moves.items() if source != dest
    }

    final_moves = dict(safe_moves)
    final_moves.update(suffix_rename_moves)

    remaining_blocked = {
        source: reason
        for source, reason in blocked_sources.items()
        if source not in suffix_moves
    }

    if suffix_moves:
        method_counter["conflict_suffix"] += len(suffix_rename_moves)
    if suffix_noop_count:
        method_counter["conflict_suffix_noop"] += suffix_noop_count

    for blocked_source, reason in remaining_blocked.items():
        unresolved_items.append(
            UnresolvedItem(
                path=blocked_source,
                reason=reason,
                article_title=blocked_source.stem.replace("【新增】", "").strip(),
                discussion=None,
            )
        )
        method_counter["blocked_conflict"] += 1

    print(f"files_total={len(markdown_files)}")
    print(f"resolved={resolved_count}")
    print(f"aligned={aligned_count}")
    print(f"planned_renames={len(planned_moves)}")
    print(f"safe_renames={len(safe_moves)}")
    print(f"suffix_conflict_renames={len(suffix_rename_moves)}")
    print(f"suffix_conflict_noop={suffix_noop_count}")
    print(f"blocked_conflicts={len(remaining_blocked)}")
    print(f"unresolved={len(unresolved_items)}")

    if method_counter:
        print("methods:")
        for method in sorted(method_counter.keys()):
            print(f"  {method}={method_counter[method]}")

    if write:
        moved_count = execute_moves(repo_root, final_moves)
        removed_dirs = remove_empty_subject_dirs(repo_root)
        print(f"renamed={moved_count}")
        print(f"removed_empty_dirs={removed_dirs}")

    if unresolved_items and show_unresolved > 0:
        print(f"unresolved_sample(limit={show_unresolved}):")
        for item in unresolved_items[:show_unresolved]:
            print(f"  {format_unresolved(item)}")

    # 返回 0，表示脚本本身执行成功；未决项仅用于人工继续处理。
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="根据 XMind 同步学科文章树结构")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="仓库根目录（默认当前目录）",
    )
    parser.add_argument(
        "--xmind",
        default="学海计划.xmind",
        help="xmind 文件路径（相对 repo-root）",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="执行重命名；默认仅 dry-run",
    )
    parser.add_argument(
        "--show-unresolved",
        type=int,
        default=80,
        help="输出未决项样本条数（默认 80，0 表示不输出）",
    )
    parser.add_argument(
        "--conflict-suffix",
        default="__冲突",
        help="冲突重命名时追加到文件名后的后缀前缀（默认 __冲突；留空可禁用）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    xmind_path = (repo_root / args.xmind).resolve()

    if not xmind_path.exists():
        print(f"xmind file not found: {xmind_path}", file=sys.stderr)
        return 1

    return run(
        repo_root=repo_root,
        xmind_path=xmind_path,
        write=args.write,
        show_unresolved=args.show_unresolved,
        conflict_suffix=args.conflict_suffix,
    )


if __name__ == "__main__":
    raise SystemExit(main())
