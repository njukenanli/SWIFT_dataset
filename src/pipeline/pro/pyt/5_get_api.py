import json
import traceback
import unidiff
import shutil, os
from src.utils.pyt.cov import CovInfo, CoverageExtractor
from src.utils.pyt.content_extract import Extractor as FunctionExtractor

import re
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed

from src.utils.runtime import SetupRuntime
import random

def get_fuc_call(code: str, file: str|None) -> List[Tuple[str, str]]:
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
        close_pos = find_matching_paren(effective_code, open_pos)
        if close_pos == -1:
            close_pos=len(code)-1
        start_pos = open_pos
        while start_pos-1 >=0 and (code[start_pos-1] == "." or code[start_pos-1] == "_" or code[start_pos-1].isalpha() or code[start_pos-1].isalnum()):
            start_pos-=1

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
                    else:
                        effective_content=file_content[starting_pos:]
                    funcs: List[Tuple[str, str]] = get_fuc_call(line.value, effective_content)
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
                if func_name.strip() == cov_info["func_name"].strip():
                    return {"file of api definition": cov_info["file_name"],
                            "lineno of api definition": cov_info["line_no"],
                            "api function name": func_name,
                            "api function should be called in this way": func_exp,
                            "the approximate lineno where this api appear in the file you need to modify": appear_path_lineno}
    return None


def extract_api_loc(instance):
    image = instance["image"]
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
    full_patch = instance["patch"] + "\n\n" + instance["test_patch"] 
    cmd = "; ".join(instance["addtional_setup_cmd"])
    cov_ext = CoverageExtractor(instance["instance_id"], container, full_patch, cmd)
    path_api_mapping: dict[str, list[tuple[str, str, int]]] = parse_patch(instance["patch"], cov_ext.container)
    if not path_api_mapping:
        instance["api"] = {}
        return instance
    patch_pairs: dict[str, list[tuple[str, str]]] = get_added_deleted_pieces(instance["patch"])
    cov = cov_ext.get_cov_class_func_lineno_inorder(instance["f2p_cmd"])
    #print(json.dumps(random.sample(cov,5),indent=True))
    cov_idx: dict[str, list[tuple[tuple[int,int],int]]] = cov_ext.build_idx(cov)
    #print(cov_idx.keys()) #300, 600
    #print(json.dumps(path_api_mapping, indent=True))
    api_res: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    for modify_file_path in path_api_mapping.keys():
        if not path_api_mapping[modify_file_path]:
            continue
        api_res[modify_file_path] = []
        api: tuple[str, str, int]
        for api in path_api_mapping[modify_file_path]:
            if f"/app/{modify_file_path}" not in cov_idx.keys():
                api_info = None
            else:
                api_info = get_api_loc(cov, cov_idx[f"/app/{modify_file_path}"], api)
            if api_info is None or (("python3." in api_info['file of api definition']) and ("packages" not in api_info['file of api definition'])):
                if api_info is None:
                    print(instance["instance_id"], api, "Location Not Found")
                api_res[modify_file_path].append({
                    "api function name": api[0],
                    "api function should be called in this way": api[1],
                    "the approximate lineno where this api appear in the file you need to modify": api[2],
                    "api function definition": "Not available."
                })
            else:
                res = container.send_command(f"cat {api_info['file of api definition']}")
                exit_code = res.metadata.exit_code
                if exit_code==0:
                    source = res.output.replace(f"cat {api_info["file of api definition"]}\n", "")
                    api_definition = FunctionExtractor._get_func(api_info["file of api definition"], source, [api_info["lineno of api definition"]], False)
                    #print(json.dumps(api_definition, indent=True))
                else:
                    print(instance["instance_id"], api_info["file of api definition"], "not found.")
                    api_definition={}
                api_info["api function definition"] = "\n".join(api_definition.values())
                if api_info["file of api definition"].replace("/app/", "") in patch_pairs.keys():
                    pairs  = patch_pairs[api_info["file of api definition"].replace("/app/", "")]
                    for deleted, added in pairs:
                        if (api_info["api function name"] in added) and (api_info["api function name"] not in deleted):
                            doc_string = "\n\n".join(re.findall(r'"""(.*?)"""', added, flags=re.DOTALL))
                            if doc_string.strip():
                                doc_string = "Function DocString:" + doc_string
                            api_info["api function definition"] = f"\n<<<<<<\nThis API Function needs to be implemented by you. {doc_string}\n>>>>>>\n"
                        elif added in api_info["api function definition"]:
                            api_info["api function definition"].replace(added, deleted)
                api_res[modify_file_path].append(api_info)
    print(instance["instance_id"], json.dumps(api_res, indent=True), flush=True)
    instance["api"] = api_res
    container.cleanup()
    return instance

def extract_api_loc_light(instance):
    image = instance["image"]
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
    full_patch = instance["patch"] + "\n\n" + instance["test_patch"] 
    cmd = "; ".join(instance["addtional_setup_cmd"])
    cov_ext = CoverageExtractor(instance["instance_id"], container, full_patch, cmd)
    path_api_mapping: dict[str, list[tuple[str, str, int]]] = parse_patch(instance["patch"], cov_ext.container)
    if not path_api_mapping:
        instance["api"] = {}
        return instance
    #print(cov_idx.keys()) #300, 600
    #print(json.dumps(path_api_mapping, indent=True))
    api_res: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    for modify_file_path in path_api_mapping.keys():
        if not path_api_mapping[modify_file_path]:
            continue
        api_res[modify_file_path] = []
        api: tuple[str, str, int]
        for api in path_api_mapping[modify_file_path]:
            api_res[modify_file_path].append({
                "api function name": api[0],
                "api function should be called in this way": api[1],
                "the approximate lineno where this api appear in the file you need to modify": api[2],
                "api function definition": "Not available."
            })
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
                    with open("data/pro/pyt/5_test_loc_context_api.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout processing instance: {instance['instance_id']}")
                instance["api"] = extract_api_loc_light(instance)
                with open("data/pro/pyt/5_test_loc_context_api.jsonl", "a") as f:
                    f.write(json.dumps(instance)+"\n")
            except Exception as e:
                print(f"Unexpected error processing instance {instance['instance_id']}: {str(e)}")


if __name__ == "__main__":
    done=[]
    if os.path.exists("data/pro/pyt/5_test_loc_context_api.jsonl"):
        with open("data/pro/pyt/5_test_loc_context_api.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/pro/pyt/4_test_loc_context.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    print(len(done))
    instances = [i for i in instances if i["instance_id"] not in done]
    print(len(instances))
    main(instances)