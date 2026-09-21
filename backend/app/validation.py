"""Itemized validation of solve requests.

Every rule violation is collected (validation never stops at the first
error) so the UI can list all problems at once and stale results can be
cleared before the user fixes the input.
"""

from __future__ import annotations

MIN_FRAGMENTS = 4
MAX_FRAGMENTS = 48


def _err(loc, message):
    return {"loc": loc, "message": message}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_fragments(raw, errors):
    if raw is None:
        errors.append(_err("fragments", "缺少必填字段 fragments（片段编号数组）"))
        return []
    if not isinstance(raw, list):
        errors.append(_err("fragments", "fragments 必须是片段编号数组"))
        return []
    fragments, seen = [], set()
    for i, v in enumerate(raw):
        if not _is_int(v):
            errors.append(_err(f"fragments[{i}]", f"片段编号必须是整数，收到 {v!r}"))
            continue
        if v in seen:
            errors.append(_err(f"fragments[{i}]", f"片段编号 {v} 重复"))
            continue
        seen.add(v)
        fragments.append(v)
    if not MIN_FRAGMENTS <= len(seen) <= MAX_FRAGMENTS:
        errors.append(
            _err(
                "fragments",
                f"唯一片段数量为 {len(seen)}，必须在 {MIN_FRAGMENTS}–{MAX_FRAGMENTS} 之间",
            )
        )
    return fragments


def _parse_pair(item, loc, errors):
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        errors.append(_err(loc, "边必须是形如 [片段A, 片段B] 的二元数组"))
        return None
    a, b = item
    if not _is_int(a) or not _is_int(b):
        errors.append(_err(loc, "边的两个端点都必须是整数片段编号"))
        return None
    return a, b


def _parse_stitch(item, loc, errors):
    if isinstance(item, dict):
        if "pair" not in item:
            errors.append(_err(loc, "缝合边缺少 pair 字段"))
            return None
        pair = _parse_pair(item["pair"], f"{loc}.pair", errors)
        weight = item.get("weight")
    elif isinstance(item, (list, tuple)) and len(item) == 3:
        pair = _parse_pair(item[:2], loc, errors)
        weight = item[2]
    else:
        errors.append(
            _err(loc, '缝合边必须是 {"pair": [A, B], "weight": W} 或 [A, B, W]')
        )
        return None
    if pair is None:
        return None
    if not _is_int(weight) or weight < 1:
        errors.append(_err(loc, f"缝合权重必须是正整数，收到 {weight!r}"))
        return None
    return pair[0], pair[1], weight


def _validate_edges(raw, field, weighted, frag_set, errors):
    """Validate one edge list; returns (pair_set, edges with raw indices)."""
    pairs, edges = set(), []
    if raw is None:
        return pairs, edges
    if not isinstance(raw, list):
        errors.append(_err(field, f"{field} 必须是数组"))
        return pairs, edges
    for i, item in enumerate(raw):
        loc = f"{field}[{i}]"
        parsed = _parse_stitch(item, loc, errors) if weighted else _parse_pair(item, loc, errors)
        if parsed is None:
            continue
        a, b = parsed[0], parsed[1]
        w = parsed[2] if weighted else None
        if a == b:
            errors.append(_err(loc, f"不允许自环：端点 {a} 与 {b} 相同"))
            continue
        unknown = [ep for ep in (a, b) if ep not in frag_set]
        if unknown:
            errors.append(_err(loc, f"端点 {unknown[0]} 不在片段列表中"))
            continue
        key = (a, b) if a < b else (b, a)
        if key in pairs:
            errors.append(_err(loc, f"点对 {key[0]}-{key[1]} 重复出现"))
            continue
        pairs.add(key)
        edges.append((i, a, b, w))
    return pairs, edges


def validate_payload(data):
    """Validate a raw request body.

    Returns ``(errors, model)``; ``model`` is ``None`` unless every rule
    passes.  ``model`` is ready to unpack into ``solve_mask_assignment``.
    """
    if not isinstance(data, dict):
        return [_err("body", "请求体必须是 JSON 对象")], None

    errors = []
    fragments = _validate_fragments(data.get("fragments"), errors)
    frag_set = set(fragments)
    conflict_pairs, conflicts = _validate_edges(
        data.get("conflict_edges"), "conflict_edges", False, frag_set, errors
    )
    _stitch_pairs, stitches = _validate_edges(
        data.get("stitch_edges"), "stitch_edges", True, frag_set, errors
    )

    # An undirected pair may not appear in both edge types.
    for i, a, b, _w in stitches:
        key = (a, b) if a < b else (b, a)
        if key in conflict_pairs:
            errors.append(
                _err(
                    f"stitch_edges[{i}]",
                    f"点对 {key[0]}-{key[1]} 同时出现在冲突边与缝合边中",
                )
            )

    if errors:
        return errors, None
    model = {
        "fragments": fragments,
        "conflict_edges": [(a, b) for _i, a, b, _w in conflicts],
        "stitch_edges": [(a, b, w) for _i, a, b, w in stitches],
    }
    return errors, model
