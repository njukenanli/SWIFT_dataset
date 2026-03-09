import re
from unidiff import PatchSet

_SIG_RE = re.compile(
    r'(?:class\s+([A-Za-z_][A-Za-z0-9_]*))|(?:def\s+([A-Za-z_][A-Za-z0-9_]*))'
)

def extract_changed_symbols(patch_text: str) -> dict[str, list[str]]:
    """
    Return {file_path: [ 'class C: def m', 'def f', ... ]} for every file
    mentioned in the unified diff in *patch_text*.
    Added/removed files are mapped to [].
    """
    patch = PatchSet(patch_text.splitlines())
    result: dict[str, list[str]] = {}

    for pf in patch:
        if pf.is_added_file:
            continue
        if pf.is_removed_file:
            continue
        if pf.path.strip().lower().split(".")[-1] in ["md", "rst", "pyi"]:
            continue
        if "/doc/" in pf.path or "/docs/" in pf.path:
            continue

        symbols: set[str] = set()

        for hunk in pf:
            has_name = False

            start = hunk.source_start
            length = hunk.source_length
            end = start + length - 1
            lineinfo = f" (start lineno: {start} , end lineno: {end}) "
            

            cache = ""
            header = (hunk.section_header or "").strip()
            head_cls, fn = "", ""
            for c, f in _SIG_RE.findall(header):
                head_cls = head_cls or c
                fn = fn or f
            if fn and head_cls:
                cache = f"class {head_cls}: def {fn}" + lineinfo
            elif fn:
                cache = f"def {fn}" + lineinfo
            elif head_cls:
                cache = f"class {head_cls}" + lineinfo

            for ln in hunk:
                txt = ln.value.lstrip(" \t+-")
                m = _SIG_RE.match(txt)
                if m:
                    c, f = m.groups()
                    if f:
                        loc = (f"class {head_cls}: def {f}" if head_cls.strip() else f"def {f}")
                    elif c:
                        loc = f"class {c}"
                    loc = loc + lineinfo
                    if getattr(ln, "is_removed", False):
                        symbols.add(loc)
                        has_name = True
                        cache = ""
                    else:
                        cache = loc
                elif getattr(ln, "is_added", False) or getattr(ln, "is_removed", False):
                    if cache:
                        symbols.add(cache)
                        cache = ""
                        has_name = True

            if not has_name:
                symbols.add(lineinfo)
        result[pf.path] = sorted(symbols, key = lambda x: int(x.split("(start lineno:")[1].split(",")[0]))

    return result


def extract_file_line(patch_text: str) -> dict[str, list[tuple[int]]]:
    """
    Return {file_path: [ 'class C: def m', 'def f', ... ]} for every file
    mentioned in the unified diff in *patch_text*.
    Added/removed files are mapped to [].
    """
    patch = PatchSet(patch_text.splitlines())
    result: dict[str, list[tuple[int]]] = {}

    for pf in patch:
        if pf.is_added_file:
            continue
        if pf.is_removed_file:
            continue
        if pf.path.strip().lower().split(".")[-1] in ["md", "rst", "pyi"]:
            continue
        if "/doc/" in pf.path or "/docs/" in pf.path:
            continue

        symbols: set[tuple[int]] = set()

        for hunk in pf:
            start = hunk.source_start
            length = hunk.source_length
            end = start + length - 1
            symbols.add((start, end))
        result[pf.path] = sorted(symbols)

    return result


def get_deleted_loc(patch: str) -> dict[str, list[int]]:
    '''
    returns: file_path : [lineno1, lineno2, ...]
    merge different deleted line numbers into one file path.
    '''
    patch_set = PatchSet(patch.splitlines())
    result: dict[str, set[int]] = {}

    for pf in patch_set:
        if pf.is_added_file:
            continue
        if pf.is_removed_file:
            continue
        if pf.path.strip().lower().split(".")[-1] in ["md", "rst", "pyi"]:
            continue
        if "/doc/" in pf.path or "/docs/" in pf.path:
            continue

        norm_path = pf.path.replace("/testbed/", "").replace("/app/", "").strip("/")
        deleted_lines: set[int] = result.setdefault(norm_path, set())
        for hunk in pf:
            for line in hunk:
                if getattr(line, "is_removed", False):
                    line_no = getattr(line, "source_line_no", None)
                    if isinstance(line_no, int):
                        deleted_lines.add(line_no)

    return {path: list(sorted(list(lines))) for path, lines in result.items() if lines}
