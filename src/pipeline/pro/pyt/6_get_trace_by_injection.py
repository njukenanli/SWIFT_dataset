import json
import shutil, os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from threading import Lock
from src.utils.pro_utils import _load_env_exports, _resolve_test_cmd
from src.utils.pyt.cov import StackTraceExtractorByInjection, Stack
from src.utils.runtime import SetupRuntime
from src.utils.pyt.diff import get_deleted_loc, get_added_loc, get_neighbor_loc

write_lock = Lock()



def proc_instance(instance):
    image = instance["image"]
    tgt_locs: dict[str, list[int]] = get_deleted_loc(instance["patch"])
    logs = ""
    logs += instance["instance_id"] + " deleted lines:" + json.dumps(tgt_locs, indent=True) + "\n"
    cmds = _load_env_exports(instance["instance_id"]) + [
        f"git reset --hard {instance["base_commit"]}", 
        f"git checkout {instance["base_commit"]}", 
        instance['before_repo_set_cmd'].strip().split('\n')[-1], 
        instance['addtional_setup_cmd'][0]]
    # instance['addtional_setup_cmd'] = cmds
    test_cmd = _resolve_test_cmd(instance)
    instance["f2p_cmd"] = test_cmd
    if tgt_locs:
        logs += instance["instance_id"] + " Case 1\n"
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
        patch = "" #instance["test_patch"] 
        tracer = StackTraceExtractorByInjection(instance["instance_id"], container, patch, cmds)
        print("hit1", flush=True)
        trace_list: list[Stack] = tracer.extract(instance["f2p_cmd"], tgt_locs)
        print("hit2", flush=True)
        logs += f'{instance["instance_id"]} {[len(i) for i in trace_list]}\n'
        container.cleanup()
    if (not tgt_locs) or (not trace_list):
        logs += instance["instance_id"] + " Case 2\n"
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
        added_locs = get_added_loc(instance["patch"]) # post solution patch
        logs += instance["instance_id"] + json.dumps(added_locs, indent=True) + "\n"
        neighbor_locs = get_neighbor_loc(instance["patch"]) # pre patch
        logs += instance["instance_id"] + json.dumps(neighbor_locs, indent=True) + "\n"
        patch = instance["patch"] # instance["test_patch"] + "\n\n" + instance["patch"] 
        post_tracer = StackTraceExtractorByInjection(instance["instance_id"], container, patch, cmds)
        print("hit3", flush=True)
        added_trace_list: list[Stack] = post_tracer.extract(instance["f2p_cmd"], added_locs)
        logs += f'{instance["instance_id"]} {[len(i) for i in added_trace_list]}\n'
        trace_list: list[Stack] = []
        for trace in added_trace_list:
            cutoff = post_tracer.cutoff_trace_by_loc(trace, added_locs)
            if cutoff:
                trace_list.append(cutoff)
        container.cleanup()
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
        patch = ""
        pre_tracer = StackTraceExtractorByInjection(instance["instance_id"], container, patch, cmds)
        print("hit4", flush=True)
        neighbor_trace_list: list[Stack] = pre_tracer.extract(instance["f2p_cmd"], neighbor_locs)
        logs += f'{instance["instance_id"]} {[len(i) for i in neighbor_trace_list]}\n'
        trace_list = pre_tracer.merge_trace_by_func(trace_list+neighbor_trace_list)
        logs += f'{instance["instance_id"]} {[len(i) for i in trace_list]}\n'
        container.cleanup()
    print("hit5", flush=True)
    assert len(trace_list) > 0, "No valid trace found..."
    logs += f'{instance["instance_id"]} {json.dumps(trace_list, indent=True)}\n'
    print(logs, flush=True)
    instance["error_context"] = trace_list
    return instance


def main(instances):
    #shutil.rmtree("data/logs", ignore_errors=True)
    #os.makedirs("data/logs", exist_ok=True)
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_instance = {executor.submit(proc_instance, instance): instance for instance in instances}
        
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result = future.result(timeout=1800)
                with write_lock:
                    with open("data/pro/pyt/6_test_loc_context_api_injected.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout after 30 minutes: {instance['instance_id']}")
            except Exception as e:
                print(f"Failed: {instance['instance_id']} - {str(e)}")
    return

if __name__ == "__main__":
    done=[]
    if os.path.exists("data/pro/pyt/6_test_loc_context_api_injected.jsonl"):
        with open("data/pro/pyt/6_test_loc_context_api_injected.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/pro/pyt/5_test_loc_context_api.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    instances = [i for i in instances if (i["instance_id"] not in done) and (not i["error_context"])]
    print(len(instances))
    #import random
    #instances=main(random.sample(instances,5))
    main(instances)
