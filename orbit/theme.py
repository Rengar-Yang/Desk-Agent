"""Colors, window labels, and the suggestion chips shown in chat."""

from orbit.config import get_config

_FALLBACK_ACCENTS = {
    "files": "#7869d9",
    "editor": "#6684dc",
    "browser": "#d8966f",
    "terminal": "#2f9d86",
    "other": "#8370da",
}
KIND_LABELS = {
    "files": "文件管理器",
    "editor": "代码编辑器",
    "browser": "浏览器",
    "terminal": "终端",
    "other": "窗口",
}
_FALLBACK_PROMPTS = {
    "files": ["这个文件夹里有什么？", "帮我整理这些文件", "找出需要处理的文件"],
    "editor": ["解释当前文件", "检查可能的问题", "基于这个文件继续改"],
    "browser": ["总结当前页面", "提炼三个要点", "我下一步该看什么？"],
    "terminal": ["当前目录在做什么？", "解释正在运行的命令", "下一步该运行什么？"],
    "other": ["这个窗口是做什么的？", "给我下一步建议", "帮我处理手头的事"],
    None: ["聊聊今天的计划", "给我一个灵感", "你能做什么？"],
}
_PROMPT_KEYS = {
    "files": "files",
    "editor": "editor",
    "browser": "browser",
    "terminal": "terminal",
    "other": "other",
    "idle": None,
}


def _accents():
    accents = dict(_FALLBACK_ACCENTS)
    table = get_config().raw.get("accent") or {}
    if not isinstance(table, dict):
        return accents
    for key in accents:
        value = table.get(key)
        if isinstance(value, str) and value.strip():
            accents[key] = value.strip()
    return accents


def _prompts():
    prompts = {key: list(value) for key, value in _FALLBACK_PROMPTS.items()}
    table = get_config().raw.get("prompts") or {}
    if not isinstance(table, dict):
        return prompts
    for name, key in _PROMPT_KEYS.items():
        section = table.get(name)
        items = section.get("items") if isinstance(section, dict) else None
        if isinstance(items, list) and items and all(isinstance(item, str) and item.strip() for item in items):
            prompts[key] = [item.strip() for item in items]
    return prompts


ACCENTS = _accents()
CONTEXT_PROMPTS = _prompts()

_LEVEL_HEADLINE = {
    "directory": "已读取文件夹",
    "file": "已读取文件开头",
    "file_meta": "只定位到文件，没有正文",
    "cwd": "已读取所在目录",
    "url": "只读到页面地址，没有正文",
    "title": "只看到窗口标题",
}


def context_headline(level):
    return _LEVEL_HEADLINE.get(level or "title", "已连接窗口")


def suggestion_prompts(target):
    """Chips must match what was actually captured."""
    if not target:
        return list(CONTEXT_PROMPTS[None])
    kind = target.get("kind", "other")
    level = target.get("detail_level") or "title"
    if kind == "browser" and level != "url":
        return ["根据标题说说这可能是什么", "把链接发我后帮我总结", "我下一步该看什么？"]
    if kind == "editor" and level == "title":
        return ["这个窗口是做什么的？", "我把文件路径发给你", "给我下一步建议"]
    if level == "title":
        return ["这个窗口是做什么的？", "你现在能读到什么？", "给我下一步建议"]
    if kind == "browser" and level == "url":
        return ["抓取并总结这个地址", "提炼三个要点", "我下一步该看什么？"]
    return list(CONTEXT_PROMPTS.get(kind, CONTEXT_PROMPTS["other"]))
