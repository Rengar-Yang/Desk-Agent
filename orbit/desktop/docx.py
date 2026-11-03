"""Read and edit .docx paragraphs with the standard library."""

import os
import re
import zipfile
from xml.etree import ElementTree as ET

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_P = f"{{{_W}}}p"
_T = f"{{{_W}}}t"
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_STORY = re.compile(r"word/(document|footnotes|endnotes|header\d+|footer\d+)\.xml$", re.I)


def _is_story(name):
    return bool(_STORY.fullmatch(name.replace("\\", "/")))


def _load(xml_bytes):
    head = xml_bytes[:12000].decode("utf-8", "ignore")
    for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9]+)="([^"]+)"', head):
        ET.register_namespace(prefix, uri)
    return ET.fromstring(xml_bytes)


def _paragraphs(root):
    for para in root.iter(_P):
        nodes = [node for node in para.iter(_T)]
        if nodes:
            yield nodes


def _text(nodes):
    return "".join(node.text or "" for node in nodes)


def _count_root(root, old):
    total = 0
    for nodes in _paragraphs(root):
        text = _text(nodes)
        start = 0
        while True:
            found = text.find(old, start)
            if found < 0:
                break
            total += 1
            start = found + max(1, len(old))
    return total


def _apply_root(root, old, new):
    for nodes in _paragraphs(root):
        text = _text(nodes)
        found = text.find(old)
        if found < 0:
            continue
        _set_nodes(nodes, text[:found] + new + text[found + len(old):])
        return True
    return False


def _stories(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        blobs = {info.filename: archive.read(info.filename) for info in infos}
    return infos, blobs


def read_docx_paragraphs(path, limit=80):
    _infos, blobs = _stories(path)
    root = _load(blobs["word/document.xml"])
    lines = []
    index = 0
    for nodes in _paragraphs(root):
        text = _text(nodes).strip()
        if not text:
            continue
        index += 1
        lines.append(f"{index}. {text}")
        if index >= limit:
            break
    return "\n".join(lines)


def _nonempty(root):
    found = []
    for nodes in _paragraphs(root):
        if _text(nodes).strip():
            found.append(nodes)
    return found


def paragraph_text(path, index):
    """Return the 1-based non-empty paragraph, matching read_docx_paragraphs numbering."""
    _infos, blobs = _stories(path)
    paragraphs = _nonempty(_load(blobs["word/document.xml"]))
    if index < 1 or index > len(paragraphs):
        return None
    return _text(paragraphs[index - 1]).strip()


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


def _set_nodes(nodes, updated):
    nodes[0].text = updated
    if updated[:1] == " " or (updated and updated[-1] == " "):
        nodes[0].set(_SPACE, "preserve")
    for node in nodes[1:]:
        node.text = ""


def rewrite_paragraph(path, index, new):
    """Replace one numbered paragraph. Returns 1 when that paragraph existed."""
    infos, blobs = _stories(path)
    root = _load(blobs["word/document.xml"])
    paragraphs = _nonempty(root)
    if index < 1 or index > len(paragraphs):
        return 0
    _set_nodes(paragraphs[index - 1], new)
    blobs["word/document.xml"] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    _write_zip(path, infos, blobs)
    return 1


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


def count_docx(path, old):
    _infos, blobs = _stories(path)
    total = 0
    for name, blob in blobs.items():
        if not _is_story(name):
            continue
        total += _count_root(_load(blob), old)
    return total


def rewrite_docx(path, old, new):
    """Replace the single occurrence of old. Returns how many matches were seen."""
    infos, blobs = _stories(path)
    parsed = []
    total = 0
    for info in infos:
        if not _is_story(info.filename):
            continue
        root = _load(blobs[info.filename])
        count = _count_root(root, old)
        total += count
        if count:
            parsed.append((info.filename, root))
    if total != 1:
        return total
    name, root = parsed[0]
    if not _apply_root(root, old, new):
        return 0
    blobs[name] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    _write_zip(path, infos, blobs)
    return 1
