
import unidiff

import re
from typing import List, Tuple

from src.utils.runtime import SetupRuntime

from src.utils.pyt.cov import CovInfo

def get_fuc_call(code: str, file: str|None, starting_pos_in_file: int) -> List[Tuple[str, str]]:
    pattern = r'(?<!\w)(?:\w+\.)*([A-Za-z_]\w*)\s*\('
    rx = re.compile(pattern)

    # Optional: avoid common control keywords that look like calls in some languages
    skip = {
        "if", "for", "while", "switch", "catch", "return", "sizeof",
        "def", "class", "lambda", "with", "in",
    }

    def find_matching_paren(s: str, open_pos: int) -> int:
        """Return index of matching ')' for '(' at open_pos, or -1 if not found."""
        depth = 0
        i = open_pos
        in_str = None   # "'" or '"'
        esc = False
        while i < len(s):
            ch = s[i]

            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == in_str:
                    in_str = None
                i += 1
                continue

            # crude comment handling for Python (# ... endline)
            if ch == "#":
                nl = s.find("\n", i)
                if nl == -1:
                    return -1
                i = nl + 1
                continue

            if ch == "'" or ch == '"':
                in_str = ch
                i += 1
                continue

            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1

        return -1
    
    def get_starting_pos(code, start_pos):
        allow_white_space = True
        while start_pos-1 >=0:
            if (code[start_pos-1] == "_" or code[start_pos-1].isalpha() or code[start_pos-1].isalnum()):
                start_pos-=1
                allow_white_space=False
                continue
            elif code[start_pos-1] == ".":
                start_pos-=1
                allow_white_space=True
                continue
            elif code[start_pos-1].isspace() and allow_white_space:
                start_pos-=1
                continue
            break
        return start_pos

    out: List[Tuple[str, str]] = []
    for m in rx.finditer(code):
        name = m.group(1)
        if name in skip:
            continue

        open_pos = m.end() - 1  # points at the '(' matched by the regex
        if file is None:
            effective_code = code
        else:
            effective_code = file
            open_pos = open_pos+starting_pos_in_file
        close_pos = find_matching_paren(effective_code, open_pos)
        if close_pos == -1:
            close_pos=len(code)-1 if file is None else starting_pos_in_file+len(code)-1
        start_pos = get_starting_pos(effective_code, open_pos)

        call_text = effective_code[start_pos: close_pos + 1]  # from name to matching ')'
        out.append((name, call_text))

    return out

def get_added_deleted_pieces(patch: str) -> dict[str, list[tuple[str, str]]]:
    '''
    return:
    file_path:
        - (deleted piece (without "-" prefix, can be empty), added piece (without "+" prefix,  can be empty))
        - next pair
    '''
    result = {}
    
    try:
        patch_set = unidiff.PatchSet(patch)
    except Exception:
        # If patch parsing fails, return empty dict
        return result
    
    for patched_file in patch_set:
        file_path = patched_file.path
        if file_path not in result:
            result[file_path] = []
        
        for hunk in patched_file:
            deleted_lines = []
            added_lines = []
            
            for line in hunk:
                if line.is_removed:
                    # Remove the "-" prefix
                    deleted_lines.append(line.value.rstrip("\n"))
                elif line.is_added:
                    # Remove the "+" prefix
                    added_lines.append(line.value.rstrip("\n"))
                elif line.is_context:
                    # Context line marks end of a change block
                    if deleted_lines or added_lines:
                        deleted_piece = "\n".join(deleted_lines)
                        added_piece = "\n".join(added_lines)
                        result[file_path].append((deleted_piece, added_piece))
                        deleted_lines = []
                        added_lines = []
            
            # Don't forget the last block in the hunk
            if deleted_lines or added_lines:
                deleted_piece = "".join(deleted_lines)
                added_piece = "".join(added_lines)
                result[file_path].append((deleted_piece, added_piece))
    
    return result

def parse_patch(patch: str, container: SetupRuntime) -> dict[str, list[tuple[str, str, int]]]:

    result = {}
    
    try:
        patch_set = unidiff.PatchSet(patch)
    except Exception:
        # If patch parsing fails, return empty dict
        return result
    
    for patched_file in patch_set:
        file_path = patched_file.path
        if not file_path.strip().endswith(".py"):
            continue
        file_content = container.send_command(f"cat {file_path}").output
        added_apis: list[tuple[str, str, int]] = []
        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    code = line.value.strip()
                    if "def" in code or "class" in code:
                        continue
                    starting_pos = file_content.find(line.value)
                    if starting_pos==-1:
                        effective_content=None
                        starting_pos=0
                    else:
                        effective_content=file_content
                    funcs: List[Tuple[str, str]] = get_fuc_call(line.value, effective_content, starting_pos)
                    for func in funcs:
                        added_apis.append((func[0], func[1], line.target_line_no))
        
        if added_apis:
            result[file_path] = added_apis
    
    return result


def get_api_loc(cov: list[CovInfo], 
                cov_idx: list[tuple[tuple[int,int],int]], 
                api: tuple[str, str, int]) -> CovInfo | None:
    func_name, func_exp, appear_path_lineno = api
    for interval, order_in_cov in cov_idx:
        if interval[0]<=appear_path_lineno<=interval[1]:
            for cov_info in cov[max(0,order_in_cov-50):]:
                if (func_name.strip() == cov_info["func_name"].strip()) \
                    or ((func_name.strip() == str(cov_info["class_name"]).strip()) and ("__init__" in cov_info["func_name"].strip())):
                    return {"file of api definition": cov_info["file_name"],
                            "lineno of api definition": cov_info["line_no"],
                            "api function name": cov_info["func_name"],
                            "api function should be called in this way": func_exp,
                            "the approximate lineno where this api appear in the file you need to modify": appear_path_lineno}
    return None