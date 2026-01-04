import json
import traceback
import unidiff
import shutil, os
from src.utils.go.content_extract import APIDefExtractor

import re
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from src.utils.runtime import SetupRuntime
import random

def get_func_call(code: str, file: str|None) -> List[Tuple[str, str]]:
    """
    Extract function calls from Go code.
    Returns list of (function_name, full_call_text) tuples.
    
    Handles:
    - Simple calls: foo()
    - Package calls: fmt.Println()
    - Method calls: obj.Method()
    - Chained calls: a.b.c(), a().b().c()
    - Indexed calls: list[i].Method(), map["key"].Func()
    - String literals: "hello", `raw string`
    - Comments: // line comment, /* block comment */
    """

    # Go control keywords that look like function calls
    skip = {
        "if", "for", "switch", "select", "go", "defer", "return",
        "range", "case", "default", "func", "type", "struct", "interface",
        "map", "chan", "make", "new", "append", "len", "cap", "copy",
        "delete", "close", "panic", "recover", "print", "println",
        "complex", "real", "imag", "clear", "min", "max",
        # Go keywords that aren't function calls
        "import", "package", "const", "var",
    }

    def skip_string_or_comment_forward(s: str, i: int) -> int:
        """Skip over string literals and comments starting at position i. Returns new position."""
        if i >= len(s):
            return i
        ch = s[i]
        
        # Double-quoted string
        if ch == '"':
            i += 1
            while i < len(s):
                if s[i] == '\\' and i + 1 < len(s):
                    i += 2
                elif s[i] == '"':
                    return i + 1
                else:
                    i += 1
            return i
        
        # Raw string (backtick)
        if ch == '`':
            i += 1
            while i < len(s) and s[i] != '`':
                i += 1
            return i + 1 if i < len(s) else i
        
        # Rune literal
        if ch == "'":
            i += 1
            while i < len(s):
                if s[i] == '\\' and i + 1 < len(s):
                    i += 2
                elif s[i] == "'":
                    return i + 1
                else:
                    i += 1
            return i
        
        # Line comment
        if ch == '/' and i + 1 < len(s) and s[i + 1] == '/':
            nl = s.find('\n', i)
            return nl + 1 if nl != -1 else len(s)
        
        # Block comment
        if ch == '/' and i + 1 < len(s) and s[i + 1] == '*':
            end = s.find('*/', i + 2)
            return end + 2 if end != -1 else len(s)
        
        return i

    def find_matching_paren(s: str, open_pos: int) -> int:
        """Return index of matching ')' for '(' at open_pos, or -1 if not found."""
        depth = 1
        i = open_pos + 1
        
        while i < len(s):
            ch = s[i]
            
            # Skip strings and comments
            if ch in '"\'`' or (ch == '/' and i + 1 < len(s) and s[i + 1] in '/*'):
                i = skip_string_or_comment_forward(s, i)
                continue
            
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
            i += 1

        return -1

    def find_matching_bracket(s: str, open_pos: int) -> int:
        """Return index of matching ']' for '[' at open_pos, or -1 if not found."""
        depth = 1
        i = open_pos + 1
        
        while i < len(s):
            ch = s[i]
            
            # Skip strings and comments
            if ch in '"\'`' or (ch == '/' and i + 1 < len(s) and s[i + 1] in '/*'):
                i = skip_string_or_comment_forward(s, i)
                continue
            
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return i
            i += 1

        return -1

    def extract_full_call_chain_backward(code: str, paren_pos: int) -> int:
        """
        Walk backwards from the '(' position to find the start of the call chain.
        Handles: a.b.c(, obj.Method(, pkg.Func(, a().b(, list[i].Method(
        Returns the start position of the call chain.
        """
        i = paren_pos - 1
        
        # Skip whitespace before (
        while i >= 0 and code[i] in ' \t\n':
            i -= 1
        
        if i < 0:
            return 0
        
        # Walk backwards collecting the chain
        while i >= 0:
            ch = code[i]
            
            # Identifier characters
            if ch.isalnum() or ch == '_':
                i -= 1
                continue
            
            # Dot separator - continue the chain
            if ch == '.':
                i -= 1
                # Skip whitespace before dot
                while i >= 0 and code[i] in ' \t\n':
                    i -= 1
                continue
            
            # Closing paren - find matching open paren (for chained calls like a().b())
            if ch == ')':
                paren_depth = 1
                i -= 1
                while i >= 0 and paren_depth > 0:
                    c = code[i]
                    # Handle strings inside parens when going backward
                    if c == '"':
                        # Find start of string (go backward)
                        i -= 1
                        while i >= 0:
                            if code[i] == '"' and (i == 0 or code[i-1] != '\\'):
                                break
                            i -= 1
                    elif c == '`':
                        i -= 1
                        while i >= 0 and code[i] != '`':
                            i -= 1
                    elif c == ')':
                        paren_depth += 1
                    elif c == '(':
                        paren_depth -= 1
                    i -= 1
                continue
            
            # Closing bracket - find matching open bracket (for indexed access like list[i].Method())
            if ch == ']':
                bracket_depth = 1
                i -= 1
                while i >= 0 and bracket_depth > 0:
                    c = code[i]
                    # Handle strings inside brackets when going backward
                    if c == '"':
                        i -= 1
                        while i >= 0:
                            if code[i] == '"' and (i == 0 or code[i-1] != '\\'):
                                break
                            i -= 1
                    elif c == '`':
                        i -= 1
                        while i >= 0 and code[i] != '`':
                            i -= 1
                    elif c == ']':
                        bracket_depth += 1
                    elif c == '[':
                        bracket_depth -= 1
                    i -= 1
                continue
            
            # Any other character - we've found the start
            break
        
        return i + 1

    def find_func_calls_in_code(code: str, effective_code: str) -> List[Tuple[str, str]]:
        """Find all function calls in code, using effective_code for argument extraction."""
        out: List[Tuple[str, str]] = []
        seen_calls = set()
        i = 0
        
        while i < len(code):
            ch = code[i]
            
            # Skip strings and comments
            if ch in '"\'`' or (ch == '/' and i + 1 < len(code) and code[i + 1] in '/*'):
                i = skip_string_or_comment_forward(code, i)
                continue
            
            # Found an opening paren - potential function call
            if ch == '(':
                # Check if this looks like a function call (identifier or ) or ] before it)
                j = i - 1
                while j >= 0 and code[j] in ' \t\n':
                    j -= 1
                
                if j >= 0 and (code[j].isalnum() or code[j] == '_' or code[j] == ')' or code[j] == ']'):
                    # Extract the function name (last identifier before the paren)
                    name_end = j
                    while name_end >= 0 and code[name_end] in ' \t\n':
                        name_end -= 1
                    
                    # Find the end of the identifier
                    name_start = name_end
                    while name_start > 0 and (code[name_start - 1].isalnum() or code[name_start - 1] == '_'):
                        name_start -= 1
                    
                    if name_start <= name_end and name_end >= 0:
                        func_name = code[name_start:name_end + 1]
                        
                        # Skip control keywords
                        if func_name not in skip and func_name and func_name[0].isalpha():
                            # Find matching close paren
                            close_pos = find_matching_paren(effective_code, i)
                            if close_pos == -1:
                                close_pos = len(effective_code) - 1
                            
                            # Extract full call chain
                            chain_start = extract_full_call_chain_backward(code, i)
                            
                            # Get the full call text
                            call_text = effective_code[chain_start:close_pos + 1].strip()
                            
                            # Skip if this looks like a function signature (has type declarations)
                            # Function signatures have patterns like "name Type" or "name pkg.Type" inside parens
                            args_content = effective_code[i+1:close_pos] if close_pos > i else ""
                            is_signature = False
                            if args_content.strip():
                                # Check for function signature patterns: "varName TypeName" without comma separation
                                # or patterns like "ctx context.Context, id string" which have type after variable
                                sig_pattern = re.search(r'\b[a-z_]\w*\s+[A-Z\*\[\]]\w*(?:\.\w+)?(?:\s*,\s*[a-z_]\w*\s+[A-Z\*\[\]]\w*(?:\.\w+)?)*\s*\)?\s*(?:\(|$)', args_content)
                                if sig_pattern:
                                    is_signature = True
                                # Also check for interface method signature pattern: ends with ) (return types)
                                if re.search(r'\)\s*\(?\s*[A-Za-z\*\[\]]\w*(?:\s*,\s*[A-Za-z\*\[\]]\w*)*\s*\)?\s*$', call_text):
                                    is_signature = True
                            
                            # Skip duplicates and signatures
                            if call_text and call_text not in seen_calls and not is_signature:
                                seen_calls.add(call_text)
                                out.append((func_name, call_text))
                
                i += 1
            else:
                i += 1
        
        return out

    effective_code = file if file is not None else code
    return find_func_calls_in_code(code, effective_code)

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
    """
    Parse a Go patch to extract function calls from added lines.
    Returns: {file_path: [(func_name, full_call_text, line_no), ...]}
    """
    result = {}
    
    try:
        patch_set = unidiff.PatchSet(patch)
    except Exception:
        # If patch parsing fails, return empty dict
        return result
    
    for patched_file in patch_set:
        file_path = patched_file.path
        if not file_path.strip().endswith(".go"):
            continue
        file_content = container.send_command(f"cat {file_path}").output
        added_apis: list[tuple[str, str, int]] = []
        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    code = line.value.strip()
                    # Skip Go function/type declarations
                    if code.startswith("func ") or code.startswith("type "):
                        continue
                    # Skip comments
                    if code.startswith("//") or code.startswith("/*"):
                        continue
                    starting_pos = file_content.find(line.value)
                    if starting_pos == -1:
                        effective_content = None
                    else:
                        effective_content = file_content[starting_pos:]
                    funcs: List[Tuple[str, str]] = get_func_call(line.value, effective_content)
                    for func in funcs:
                        added_apis.append((func[0], func[1], line.target_line_no))
        
        if added_apis:
            result[file_path] = added_apis
    
    return result


def extract_api_loc(instance):
    # Maximum total size for all API definitions per instance (500KB)
    MAX_TOTAL_API_SIZE = 500000
    
    image = instance["image"]
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
    path_api_mapping: dict[str, list[tuple[str, str, int]]] = parse_patch(instance["patch"], container)
    if not path_api_mapping:
        instance["api"] = {}
        container.cleanup()
        return instance
    patch_pairs: dict[str, list[tuple[str, str]]] = get_added_deleted_pieces(instance["patch"])
    #print(cov_idx.keys()) #300, 600
    #print(json.dumps(path_api_mapping, indent=True))
    ext = APIDefExtractor(container, instance["base_commit"], instance["patch"] + "\n" + instance["test_patch"], "/app" )
    api_res: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    
    # Track seen API definitions to avoid duplicates
    seen_api_defs: set[tuple[str, tuple[int, int]]] = set()  # (file, line_range)
    total_api_size = 0
    
    for modify_file_path in path_api_mapping.keys():
        if not path_api_mapping[modify_file_path]:
            continue
        api_res[modify_file_path] = []
        api: tuple[str, str, int]
        for api in path_api_mapping[modify_file_path]:
                # Check if we've exceeded total size limit
                if total_api_size >= MAX_TOTAL_API_SIZE:
                    break
                    
                api_info = ext.find_api(api[1], modify_file_path, api[2], True)
                
                # Skip duplicate API definitions (same file + line range)
                if "file of api definition" in api_info and "lineno of api definition" in api_info:
                    def_key = (api_info["file of api definition"], tuple(api_info["lineno of api definition"]))
                    if def_key in seen_api_defs:
                        # Still record the call but without the definition
                        api_info = {
                            "api function name": api[0],
                            "api function should be called in this way": api[1],
                            "the approximate lineno where this api appear in the file you need to modify": api[2],
                            "api function definition": "[duplicate - see above]",
                        }
                        api_res[modify_file_path].append(api_info)
                        continue
                    seen_api_defs.add(def_key)
                
                api_info = {
                    "api function name": api[0],
                    "api function should be called in this way": api[1],
                    "the approximate lineno where this api appear in the file you need to modify": api[2],
                    **api_info,
                }
                
                # Track total size
                if "api function definition" in api_info:
                    total_api_size += len(str(api_info["api function definition"]))

                # Only process if we found a valid definition file
                if "file of api definition" in api_info and api_info["file of api definition"]:
                    def_file = api_info["file of api definition"].replace("/app/", "")
                    if def_file in patch_pairs.keys():
                        pairs = patch_pairs[def_file]
                        for deleted, added in pairs:
                            if (api_info["api function name"] in added) and (api_info["api function name"] not in deleted):
                                # For Go, extract doc comments (// or /* */)
                                doc_comments = re.findall(r'//\s*(.*?)$', added, flags=re.MULTILINE)
                                block_comments = re.findall(r'/\*\s*(.*?)\s*\*/', added, flags=re.DOTALL)
                                doc_string = "\n".join(doc_comments + block_comments)
                                if doc_string.strip():
                                    doc_string = "Function Comments:\n" + doc_string
                                api_info["api function definition"] = f"\n<<<<<<\nThis API Function needs to be implemented by you. {doc_string}\n>>>>>>\n"
                            elif added in api_info.get("api function definition", ""):
                                api_info["api function definition"] = api_info["api function definition"].replace(added, deleted)
                api_res[modify_file_path].append(api_info)
    print(instance["instance_id"], json.dumps(api_res, indent=True), flush=True)
    instance["api"] = api_res
    container.cleanup()
    return instance


def process_instance(instance):
    """Process a single instance with error handling"""
    try:
        result = extract_api_loc(instance)
        return result, None
    except Exception as e:
        return None, f"Error processing instance {instance['instance_id']}: {str(e)}\n{traceback.format_exc()}"
        
def main(instances):
    #shutil.rmtree("data/logs", ignore_errors=True)
    #os.makedirs("data/logs", exist_ok=True)
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all tasks
        future_to_instance = {executor.submit(process_instance, instance): instance for instance in instances}
        
        # Process completed tasks
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result, error = future.result(timeout=1)
                if error:
                    print(error, flush=True)
                elif result:
                    with open("data/pro/go/4_test_loc_api.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout processing instance: {instance['instance_id']}")
            except Exception as e:
                print(f"Unexpected error processing instance {instance['instance_id']}: {str(e)}")


if __name__ == "__main__":
    done=[]
    if os.path.exists("data/pro/go/4_test_loc_api.jsonl"):
        with open("data/pro/go/4_test_loc_api.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/pro/go/3_test_loc.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    print(len(done))
    instances = [i for i in instances if i["instance_id"] not in done]
    print(len(instances), flush=True)
    main(instances)