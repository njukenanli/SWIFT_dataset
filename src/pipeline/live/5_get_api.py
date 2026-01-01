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
from src.utils.pyt.func_call import parse_patch, get_added_deleted_pieces, get_api_loc


def extract_api_loc(instance):
    image = "starryzhang/sweb.eval.x86_64." + instance["instance_id"].replace("__", "_1776_")
    full_patch = instance["patch"] + "\n\n" + instance["test_patch"] 
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
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
    # print(cov_idx.keys()) #300, 600
    #print(json.dumps(path_api_mapping, indent=True))
    api_res: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    for modify_file_path in path_api_mapping.keys():
        if not path_api_mapping[modify_file_path]:
            continue
        api_res[modify_file_path] = []
        api: tuple[str, str, int]
        for api in path_api_mapping[modify_file_path]:
            if f"/testbed/{modify_file_path}" not in cov_idx.keys():
                api_info = None
            else:
                api_info = get_api_loc(cov, cov_idx[f"/testbed/{modify_file_path}"], api)
            if api_info is None or (("python3." in api_info['file of api definition']) and ("packages" not in api_info['file of api definition'])):
                print(instance["instance_id"], api, "Location Not Found or Skipped")
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
                if api_info["file of api definition"].replace("/testbed/", "") in patch_pairs.keys():
                    pairs  = patch_pairs[api_info["file of api definition"].replace("/testbed/", "")]
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
    image = "starryzhang/sweb.eval.x86_64." + instance["instance_id"].replace("__", "_1776_")
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
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
    
    with ThreadPoolExecutor(max_workers=16) as executor:
        # Submit all tasks
        future_to_instance = {executor.submit(process_instance, instance): instance for instance in instances}
        
        # Process completed tasks
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result, error = future.result(timeout=1800)
                if error:
                    print(error, flush=True)
                elif result:
                    with open("data/live/5_test_loc_context_api.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout processing instance: {instance['instance_id']}")
                instance["api"] = extract_api_loc_light(instance)
                with open("data/live/5_test_loc_context_api.jsonl", "a") as f:
                    f.write(json.dumps(instance)+"\n")
            except Exception as e:
                print(f"Unexpected error processing instance {instance['instance_id']}: {str(e)}")


if __name__ == "__main__":
    done=[]
    if os.path.exists("data/live/5_test_loc_context_api.jsonl"):
        with open("data/live/5_test_loc_context_api.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/live/4_test_loc_context.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    print(len(done))
    instances = [i for i in instances if i["instance_id"] not in done]
    print(len(instances), flush=True)
    main(reversed(instances))