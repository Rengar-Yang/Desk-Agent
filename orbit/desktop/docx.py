"""Read and edit .docx stories with the standard library."""

import copy
import os
import re
import zipfile
from xml.etree import ElementTree as ET

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_P = f"{{{_W}}}p"
_T = f"{{{_W}}}t"
_BODY = f"{{{_W}}}body"
_PPR = f"{{{_W}}}pPr"
_R = f"{{{_W}}}r"
_RPR = f"{{{_W}}}rPr"
_SECT = f"{{{_W}}}sectPr"
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_SKIP_ANCESTORS = {
    f"{{{_MC}}}Fallback",
    f"{{{_W}}}txbxContent",
    f"{{{_W}}}del",
}
_STORY = re.compile(r"word/(document|footnotes|endnotes|header\d+|footer\d+)\.xml$", re.I)


def _is_story(name):
    return bool(_STORY.fullmatch(name.replace("\\", "/")))


def _load(xml_bytes):
    head = xml_bytes[:12000].decode("utf-8", "ignore")
    for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9]+)="([^"]+)"', head):
        ET.register_namespace(prefix, uri)
    return ET.fromstring(xml_bytes)


def _parents(root):
    return {child: parent for parent in root.iter() for child in list(parent)}


def _hidden(node, parents):
    current = node
    while current is not None:
        if current.tag in _SKIP_ANCESTORS:
            return True
        current = parents.get(current)
    return False


def _text(nodes):
    return "".join(node.text or "" for node in nodes)


def _visible_paragraphs(root):
    """Body paragraphs in the same order Word numbers, skipping blanks and duplicates."""
    parents = _parents(root)
    for para in root.iter(_P):
        if _hidden(para, parents):
            continue
        nodes = [node for node in para.iter(_T) if not _hidden(node, parents)]
        if _text(nodes).strip():
            yield para, nodes


def _paragraph_list(root):
    return list(_visible_paragraphs(root))


def _set_preserve(node):
    text = node.text or ""
    if text[:1] == " " or text[-1:] == " ":
        node.set(_SPACE, "preserve")


def _set_nodes(nodes, updated):
    nodes[0].text = updated
    _set_preserve(nodes[0])
    for node in nodes[1:]:
        node.text = ""


_VAL = f"{{{_W}}}val"
_FORMAT_ALIASES = {
    "加粗": "bold", "粗体": "bold", "斜体": "italic", "下划线": "underline", "删除线": "strike",
    "字体": "font", "字号": "size", "大小": "size", "颜色": "color", "字体颜色": "color",
    "高亮": "highlight", "突出显示": "highlight", "对齐": "align", "对齐方式": "align",
    "样式": "style", "段落样式": "style",
}
_ALIGN = {
    "left": "left", "center": "center", "right": "right", "justify": "both", "both": "both",
    "左": "left", "左对齐": "left", "居中": "center", "中": "center", "右": "right", "右对齐": "right",
    "两端": "both", "两端对齐": "both",
}
_STYLES = {
    "normal": "Normal", "正文": "Normal",
    "title": "Title", "标题": "Title",
    "heading1": "Heading1", "h1": "Heading1", "标题1": "Heading1", "标题 1": "Heading1", "一级标题": "Heading1",
    "heading2": "Heading2", "h2": "Heading2", "标题2": "Heading2", "标题 2": "Heading2", "二级标题": "Heading2",
    "heading3": "Heading3", "h3": "Heading3", "标题3": "Heading3", "标题 3": "Heading3", "三级标题": "Heading3",
    "heading4": "Heading4", "h4": "Heading4", "标题4": "Heading4", "标题 4": "Heading4",
}
_STYLE_LABEL = {"Heading1": "标题1", "Heading2": "标题2", "Heading3": "标题3", "Heading4": "标题4", "Title": "标题"}
_STYLE_FALLBACK = {
    "Heading1": {"bold": True, "size": 16},
    "Heading2": {"bold": True, "size": 14},
    "Heading3": {"bold": True, "size": 12},
    "Heading4": {"bold": True, "size": 12},
    "Title": {"bold": True, "size": 22, "align": "center"},
}
_COLORS = {
    "red": "FF0000", "红": "FF0000", "blue": "0000FF", "蓝": "0000FF", "green": "00B050", "绿": "00B050",
    "black": "000000", "黑": "000000", "white": "FFFFFF", "白": "FFFFFF", "orange": "ED7D31", "橙": "ED7D31",
    "purple": "7030A0", "紫": "7030A0", "gray": "808080", "灰": "808080", "yellow": "FFC000", "黄": "FFC000",
}
_HIGHLIGHT = {
    "yellow": "yellow", "黄": "yellow", "green": "green", "绿": "green", "red": "red", "红": "red",
    "blue": "blue", "蓝": "blue", "cyan": "cyan", "青": "cyan", "pink": "magenta", "粉": "magenta",
    "gray": "lightGray", "灰": "lightGray", "none": "", "无": "", "取消": "",
}
_SIZES = {
    "初号": 42, "小初": 36, "一号": 26, "小一": 24, "二号": 22, "小二": 18, "三号": 16, "小三": 15,
    "四号": 14, "小四": 12, "五号": 10.5, "小五": 9, "六号": 7.5, "小六": 6.5, "七号": 5.5, "八号": 5,
}
_CHAR_KEYS = ("bold", "italic", "underline", "strike", "font", "size", "color", "highlight")
_PARA_KEYS = ("align", "style")


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "开"}:
        return True
    if text in {"0", "false", "no", "n", "否", "关"}:
        return False
    return None


def _points(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace("pt", "").replace("磅", "")
    if text in _SIZES:
        return float(_SIZES[text])
    try:
        return float(text)
    except ValueError:
        return None


def _color(value):
    text = str(value).strip()
    named = _COLORS.get(text.lower()) or _COLORS.get(text)
    if named:
        return named
    hex_text = text.lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", hex_text):
        return hex_text.upper()
    return None


def normalize_format(raw):
    """Turn a tool argument into the formatting shared by the file and Word."""
    if not isinstance(raw, dict):
        return None
    source = {}
    for key, value in raw.items():
        source[_FORMAT_ALIASES.get(str(key), str(key))] = value
    fmt = {}
    for key in ("bold", "italic", "underline", "strike"):
        if key not in source or source[key] in (None, ""):
            continue
        flag = _as_bool(source[key])
        if flag is not None:
            fmt[key] = flag
    font = str(source.get("font") or "").strip()
    if font:
        fmt["font"] = font
    if source.get("size") not in (None, ""):
        size = _points(source.get("size"))
        if size and 1 <= size <= 72:
            fmt["size"] = size
    if source.get("color") not in (None, ""):
        color = _color(source.get("color"))
        if color:
            fmt["color"] = color
    if source.get("highlight") not in (None,):
        mark = str(source.get("highlight") or "").strip()
        if mark:
            named = _HIGHLIGHT.get(mark.lower())
            if named is None:
                named = _HIGHLIGHT.get(mark, mark)
            fmt["highlight"] = named
    align = _ALIGN.get(str(source.get("align") or "").strip().lower()) or _ALIGN.get(str(source.get("align") or "").strip())
    if align:
        fmt["align"] = align
    style_key = str(source.get("style") or "").strip()
    style = _STYLES.get(style_key.lower()) or _STYLES.get(style_key)
    if style_key in {"Normal", "Title", "Heading1", "Heading2", "Heading3", "Heading4"}:
        style = style_key
    if style:
        fmt["style"] = style
    if not fmt:
        return None
    fallback = _STYLE_FALLBACK.get(fmt.get("style"), {})
    merged = dict(fallback)
    merged.update(fmt)
    return merged


def _has_char(fmt):
    return any(key in (fmt or {}) for key in _CHAR_KEYS)


def _has_para(fmt):
    return any(key in (fmt or {}) for key in _PARA_KEYS)


def _ensure_child(parent, tag):
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    return child


def _rpr(run):
    props = run.find(_RPR)
    if props is None:
        props = ET.Element(_RPR)
        run.insert(0, props)
    return props


def _toggle(props, tag, on):
    child = _ensure_child(props, tag)
    if on:
        if _VAL in child.attrib:
            del child.attrib[_VAL]
    else:
        child.set(_VAL, "0")


def _merge_rpr(run, fmt):
    if not _has_char(fmt):
        return
    props = _rpr(run)
    if "bold" in fmt:
        _toggle(props, f"{{{_W}}}b", fmt["bold"])
        _toggle(props, f"{{{_W}}}bCs", fmt["bold"])
    if "italic" in fmt:
        _toggle(props, f"{{{_W}}}i", fmt["italic"])
        _toggle(props, f"{{{_W}}}iCs", fmt["italic"])
    if "underline" in fmt:
        child = _ensure_child(props, f"{{{_W}}}u")
        child.set(_VAL, "single" if fmt["underline"] else "none")
    if "strike" in fmt:
        _toggle(props, f"{{{_W}}}strike", fmt["strike"])
    if fmt.get("font"):
        child = _ensure_child(props, f"{{{_W}}}rFonts")
        for name in ("ascii", "hAnsi", "eastAsia", "cs"):
            child.set(f"{{{_W}}}{name}", fmt["font"])
    if fmt.get("size"):
        half = str(int(round(float(fmt["size"]) * 2)))
        for tag in (f"{{{_W}}}sz", f"{{{_W}}}szCs"):
            _ensure_child(props, tag).set(_VAL, half)
    if fmt.get("color"):
        child = _ensure_child(props, f"{{{_W}}}color")
        for key in list(child.attrib):
            if key != _VAL:
                del child.attrib[key]
        child.set(_VAL, fmt["color"])
    if "highlight" in fmt:
        tag = f"{{{_W}}}highlight"
        if not fmt["highlight"]:
            child = props.find(tag)
            if child is not None:
                props.remove(child)
        else:
            _ensure_child(props, tag).set(_VAL, fmt["highlight"])


def _apply_paragraph_format(paragraph, fmt):
    if not _has_para(fmt):
        return
    props = paragraph.find(_PPR)
    if props is None:
        props = ET.Element(_PPR)
        paragraph.insert(0, props)
    if fmt.get("align"):
        _ensure_child(props, f"{{{_W}}}jc").set(_VAL, fmt["align"])
    if fmt.get("style"):
        _ensure_child(props, f"{{{_W}}}pStyle").set(_VAL, fmt["style"])


def _visible_run_text(run, parents):
    return "".join(node.text or "" for node in run.iter(_T) if not _hidden(node, parents))


def _set_run_text(run, text):
    nodes = list(run.iter(_T))
    if not nodes:
        nodes = [ET.SubElement(run, _T)]
    nodes[0].text = text
    _set_preserve(nodes[0])
    for node in nodes[1:]:
        node.text = ""


def _paragraph_runs(paragraph):
    parents = _parents(paragraph)
    found = []
    cursor = 0
    for run in paragraph.iter(_R):
        if _hidden(run, parents):
            continue
        piece = _visible_run_text(run, parents)
        if not piece:
            continue
        found.append((run, cursor, cursor + len(piece)))
        cursor += len(piece)
    return found


def _split_run(paragraph, run, local_start, local_end):
    parents = _parents(paragraph)
    parent = parents.get(run)
    text = _visible_run_text(run, parents)
    if parent is None or local_start <= 0 and local_end >= len(text):
        return run
    index = list(parent).index(run)
    pieces = []
    if local_start > 0:
        pieces.append((text[:local_start], False))
    pieces.append((text[local_start:local_end], True))
    if local_end < len(text):
        pieces.append((text[local_end:], False))
    parent.remove(run)
    target = None
    for step, (piece, marked) in enumerate(pieces):
        clone = copy.deepcopy(run)
        _set_run_text(clone, piece)
        parent.insert(index + step, clone)
        if marked:
            target = clone
    return target


def _format_span(paragraph, start, end, fmt):
    if not fmt:
        return
    if _has_char(fmt) and end > start:
        for run, abs_start, abs_end in reversed(_paragraph_runs(paragraph)):
            if abs_end <= start or abs_start >= end:
                continue
            length = abs_end - abs_start
            local_start = max(0, start - abs_start)
            local_end = min(length, end - abs_start)
            target = run if local_start == 0 and local_end == length else _split_run(paragraph, run, local_start, local_end)
            if target is not None:
                _merge_rpr(target, fmt)
    _apply_paragraph_format(paragraph, fmt)


def _format_paragraph(paragraph, fmt):
    if not fmt:
        return
    _apply_paragraph_format(paragraph, fmt)
    if not _has_char(fmt):
        return
    for run, start, end in _paragraph_runs(paragraph):
        if end > start:
            _merge_rpr(run, fmt)


def _flag_on(element):
    if element is None:
        return False
    value = (element.get(_VAL) or "").lower()
    return value not in {"0", "false", "off", "none"}


def _format_label(paragraph):
    props = paragraph.find(_PPR)
    marks = []
    if props is not None:
        style = props.find(f"{{{_W}}}pStyle")
        style_name = _STYLE_LABEL.get(style.get(_VAL) if style is not None else "")
        if style_name:
            marks.append(style_name)
        align = props.find(f"{{{_W}}}jc")
        align_name = {"center": "居中", "right": "右对齐", "both": "两端对齐"}.get(align.get(_VAL) if align is not None else "")
        if align_name:
            marks.append(align_name)
    fonts, sizes, colors, highlights = set(), set(), set(), set()
    bold = italic = underline = strike = False
    parents = _parents(paragraph)
    for run in paragraph.iter(_R):
        if _hidden(run, parents) or not _visible_run_text(run, parents):
            continue
        run_props = run.find(_RPR)
        if run_props is None:
            continue
        bold = bold or _flag_on(run_props.find(f"{{{_W}}}b"))
        italic = italic or _flag_on(run_props.find(f"{{{_W}}}i"))
        underline_el = run_props.find(f"{{{_W}}}u")
        underline = underline or (underline_el is not None and (underline_el.get(_VAL) or "single") != "none")
        strike = strike or _flag_on(run_props.find(f"{{{_W}}}strike"))
        fonts_el = run_props.find(f"{{{_W}}}rFonts")
        if fonts_el is not None:
            font = fonts_el.get(f"{{{_W}}}eastAsia") or fonts_el.get(f"{{{_W}}}ascii") or ""
            if font:
                fonts.add(font)
        size_el = run_props.find(f"{{{_W}}}sz")
        if size_el is not None and str(size_el.get(_VAL) or "").isdigit():
            sizes.add(int(size_el.get(_VAL)) / 2)
        color_el = run_props.find(f"{{{_W}}}color")
        if color_el is not None:
            color = (color_el.get(_VAL) or "").upper()
            if color and color not in {"AUTO", "000000"}:
                colors.add("#" + color)
        mark = run_props.find(f"{{{_W}}}highlight")
        if mark is not None and mark.get(_VAL):
            highlights.add(mark.get(_VAL))
    if bold:
        marks.append("加粗")
    if italic:
        marks.append("斜体")
    if underline:
        marks.append("下划线")
    if strike:
        marks.append("删除线")
    if len(fonts) == 1:
        marks.append(next(iter(fonts)))
    elif len(fonts) > 1:
        marks.append("多种字体")
    if len(sizes) == 1:
        size = next(iter(sizes))
        marks.append(f"{int(size) if size == int(size) else size}磅")
    elif len(sizes) > 1:
        marks.append("多种字号")
    if len(colors) == 1:
        marks.append(next(iter(colors)))
    if highlights:
        marks.append("高亮")
    return " ".join(marks)


def _replace_span(nodes, start, end, new):
    cursor = 0
    started = False
    for node in nodes:
        piece = node.text or ""
        next_cursor = cursor + len(piece)
        if next_cursor <= start or cursor >= end:
            cursor = next_cursor
            continue
        local_start = max(0, start - cursor)
        local_end = min(len(piece), end - cursor)
        if not started:
            node.text = piece[:local_start] + new + piece[local_end:]
            started = True
        else:
            node.text = piece[:local_start] + piece[local_end:]
        _set_preserve(node)
        cursor = next_cursor


def _spans(text, old):
    found = []
    if not old:
        return found
    start = 0
    while True:
        at = text.find(old, start)
        if at < 0:
            break
        found.append((at, at + len(old)))
        start = at + len(old)
    return found


def _replace_nodes(nodes, old, new, replace_all, paragraph=None, fmt=None):
    spans = _spans(_text(nodes), old)
    if not spans:
        return 0
    chosen = spans if replace_all else spans[:1]
    placed = []
    for start, end in reversed(chosen):
        _replace_span(nodes, start, end, new)
        placed.append(start)
    if fmt and paragraph is not None:
        for start in placed:
            _format_span(paragraph, start, start + len(new), fmt)
    return len(chosen)


def _lines(text):
    parts = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()
    return parts


def _make_paragraph(template, text, fmt=None):
    paragraph = ET.Element(_P)
    if template is not None:
        props = template.find(_PPR)
        if props is not None:
            paragraph.append(copy.deepcopy(props))
    run = ET.Element(_R)
    if template is not None and not fmt:
        source = template.find(_R)
        if source is not None:
            style = source.find(_RPR)
            if style is not None:
                run.append(copy.deepcopy(style))
    node = ET.SubElement(run, _T)
    node.text = text
    _set_preserve(node)
    paragraph.append(run)
    if fmt:
        _merge_rpr(run, fmt)
        _apply_paragraph_format(paragraph, fmt)
    return paragraph


def _insert_relative(root, paragraph, texts, before=False, fmt=None):
    parents = _parents(root)
    parent = parents.get(paragraph)
    if parent is None or not texts:
        return 0
    index = list(parent).index(paragraph)
    offset = index if before else index + 1
    for step, text in enumerate(texts):
        parent.insert(offset + step, _make_paragraph(paragraph, text, fmt))
    return len(texts)


def _append_body(root, texts, fmt=None):
    """Insert paragraphs at the end of the document body, before the section mark."""
    body = root.find(_BODY)
    if body is None or not texts:
        return 0
    slot = len(list(body))
    for index, child in enumerate(list(body)):
        if child.tag == _SECT:
            slot = index
            break
    template = None
    paragraphs = _paragraph_list(root)
    if paragraphs:
        template = paragraphs[-1][0]
    for step, text in enumerate(texts):
        body.insert(slot + step, _make_paragraph(template, text, fmt))
    return len(texts)


def _remove_paragraph(root, paragraph):
    parents = _parents(root)
    parent = parents.get(paragraph)
    if parent is None:
        return False
    parent.remove(paragraph)
    if not any(child.tag == _P for child in list(parent)):
        parent.append(ET.Element(_P))
    return True


def _stories(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        blobs = {info.filename: archive.read(info.filename) for info in infos}
    return infos, blobs


def _write_zip(path, infos, blobs):
    temporary = path.with_name(path.name + ".orbit-tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as out:
            for info in infos:
                cloned = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                cloned.compress_type = info.compress_type or zipfile.ZIP_DEFLATED
                cloned.external_attr = info.external_attr
                out.writestr(cloned, blobs[info.filename])
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _dump(root):
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def read_docx_paragraphs(path, limit=120):
    _infos, blobs = _stories(path)
    paragraphs = _paragraph_list(_load(blobs["word/document.xml"]))
    total = len(paragraphs)
    shown = paragraphs[: max(1, int(limit))]
    lines = []
    for index, (para, nodes) in enumerate(shown, start=1):
        label = _format_label(para)
        text = _text(nodes).strip()
        lines.append(f"{index}. [{label}] {text}" if label else f"{index}. {text}")
    if total > len(shown):
        lines.append(f"（只列出了前 {len(shown)} 段，共 {total} 段。需要后面的内容时把 limit 调大。）")
    elif total:
        lines.append(f"（共 {total} 段）")
    return "\n".join(lines)


def paragraph_text(path, index):
    """Return the 1-based non-empty paragraph, matching read_docx_paragraphs numbering."""
    _infos, blobs = _stories(path)
    paragraphs = _paragraph_list(_load(blobs["word/document.xml"]))
    if index < 1 or index > len(paragraphs):
        return None
    return _text(paragraphs[index - 1][1]).strip()


def _count_root(root, old):
    total = 0
    for _para, nodes in _visible_paragraphs(root):
        total += len(_spans(_text(nodes), old))
    return total


def count_docx(path, old):
    _infos, blobs = _stories(path)
    total = 0
    for name, blob in blobs.items():
        if not _is_story(name):
            continue
        total += _count_root(_load(blob), old)
    return total


def _apply_lines(root, paragraph, nodes, new, fmt=None):
    parts = _lines(new)
    if not parts:
        parts = [""]
    _set_nodes(nodes, parts[0])
    if fmt:
        _format_paragraph(paragraph, fmt)
    if len(parts) > 1:
        _insert_relative(root, paragraph, parts[1:], before=False, fmt=fmt)
    return True


def _format_matches(paragraph, old, fmt):
    parents = _parents(paragraph)
    text = "".join(_visible_run_text(run, parents) for run, _start, _end in _paragraph_runs(paragraph))
    spans = _spans(text, old)
    for start, end in reversed(spans):
        _format_span(paragraph, start, end, fmt)
    return len(spans)


def edit_docx(path, mode="replace", paragraph=None, old="", new="", fmt=None):
    """Edit the document story.

    Returns (code, info). code is ok, missing, ambiguous, or badindex.
    info is the number of edits, or an error detail.
    """
    infos, blobs = _stories(path)
    mode = mode or "replace"
    old = "" if old is None else str(old)
    new = "" if new is None else str(new)
    fmt = normalize_format(fmt)
    index = None if paragraph in (None, "") else int(paragraph)
    if mode == "replace_all":
        index = None
    changed = False
    count = 0

    def save(name, root):
        nonlocal changed
        blobs[name] = _dump(root)
        changed = True

    if mode == "format":
        if not fmt or (index is None and not old):
            return "missing", 0
        for info in infos:
            story = info.filename.replace("\\", "/")
            if not _is_story(story) or (index is not None and story != "word/document.xml"):
                continue
            root = _load(blobs[info.filename])
            paragraphs = _paragraph_list(root)
            if index is not None:
                if index < 1 or index > len(paragraphs):
                    return "badindex", len(paragraphs)
                chosen = [paragraphs[index - 1]]
            else:
                chosen = paragraphs
            edited = 0
            for para, _nodes in chosen:
                if old:
                    edited += _format_matches(para, old, fmt)
                else:
                    _format_paragraph(para, fmt)
                    edited += 1
            if edited:
                count += edited
                save(info.filename, root)
        if count == 0:
            return "missing", 0
    elif mode in {"insert", "delete"} or (mode == "replace" and index is not None and not old):
        name = "word/document.xml"
        if name not in blobs:
            return "missing", 0
        root = _load(blobs[name])
        paragraphs = _paragraph_list(root)
        if mode == "insert":
            parts = _lines(new)
            if index is None or index < 0:
                count = _append_body(root, parts, fmt)
            elif index == 0:
                body = root.find(_BODY)
                if body is None:
                    return "missing", 0
                template = paragraphs[0][0] if paragraphs else None
                for step, text in enumerate(parts):
                    body.insert(step, _make_paragraph(template, text, fmt))
                count = len(parts)
            elif index > len(paragraphs):
                return "badindex", len(paragraphs)
            else:
                count = _insert_relative(root, paragraphs[index - 1][0], parts, before=False, fmt=fmt)
            if not count:
                return "missing", 0
            save(name, root)
        elif mode == "delete":
            if index is None or index < 1 or index > len(paragraphs):
                return "badindex", len(paragraphs)
            if not _remove_paragraph(root, paragraphs[index - 1][0]):
                return "missing", 0
            count = 1
            save(name, root)
        else:
            if index < 1 or index > len(paragraphs):
                return "badindex", len(paragraphs)
            para, nodes = paragraphs[index - 1]
            _apply_lines(root, para, nodes, new, fmt)
            count = 1
            save(name, root)
    else:
        replace_all = mode == "replace_all" or mode == "format"
        if index is not None:
            name = "word/document.xml"
            if name not in blobs:
                return "missing", 0
            root = _load(blobs[name])
            paragraphs = _paragraph_list(root)
            if index < 1 or index > len(paragraphs):
                return "badindex", len(paragraphs)
            para, nodes = paragraphs[index - 1]
            found = len(_spans(_text(nodes), old))
            if found == 0:
                return "missing", 0
            if found > 1 and not replace_all:
                return "ambiguous", found
            count = _replace_nodes(nodes, old, new, replace_all, para, fmt)
            save(name, root)
        else:
            parsed = []
            for info in infos:
                story = info.filename.replace("\\", "/")
                if not _is_story(story):
                    continue
                root = _load(blobs[info.filename])
                found = _count_root(root, old)
                if found:
                    parsed.append((info.filename, root, found))
                    count += found
            if count == 0:
                return "missing", 0
            if count > 1 and not replace_all:
                return "ambiguous", count
            for name, root, _found in parsed:
                for para, nodes in _visible_paragraphs(root):
                    _replace_nodes(nodes, old, new, bool(replace_all), para, fmt)
                save(name, root)
    if changed:
        _write_zip(path, infos, blobs)
    return ("ok", count) if count else ("missing", 0)


def rewrite_paragraph(path, index, new):
    """Replace one numbered paragraph. Returns 1 when that paragraph existed."""
    code, _info = edit_docx(path, "replace", paragraph=index, new=new)
    return 1 if code == "ok" else 0


def rewrite_docx(path, old, new):
    """Replace the single occurrence of old. Returns how many matches were seen."""
    code, info = edit_docx(path, "replace", old=old, new=new)
    if code == "ok":
        return 1
    if code == "ambiguous":
        return info
    return 0


def read_docx_preview(path, limit_chars=4000):
    suffix = path.suffix.lower()
    if suffix not in {".docx", ".docm"}:
        return None
    try:
        if path.stat().st_size > 40_000_000:
            return None
        text = read_docx_paragraphs(path, 40)
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return None
    if not text:
        return None
    return text[:limit_chars]
