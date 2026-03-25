import json
import shutil, os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from threading import Lock
from src.utils.pyt.cov import StackTraceExtractorByInjection, Stack
from src.utils.runtime import SetupRuntime
from src.utils.pyt.diff import get_deleted_loc, get_added_loc, get_neighbor_loc

write_lock = Lock()



def proc_instance(instance):
    image = "swebench/sweb.eval.x86_64." + instance["instance_id"].replace("__", "_1776_")
    tgt_locs: dict[str, list[int]] = get_deleted_loc(instance["patch"])
    logs = ""
    logs += instance["instance_id"] + " deleted lines:" + json.dumps(tgt_locs, indent=True) + "\n"
    if tgt_locs:
        logs += instance["instance_id"] + " Case 1\n"
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
        patch = instance["test_patch"] 
        cmd = "; ".join(instance["addtional_setup_cmd"])
        tracer = StackTraceExtractorByInjection(instance["instance_id"], container, patch, cmd)
        print("hit1", flush=True)
        trace_list: list[Stack] = tracer.extract(instance["f2p_cmd"], tgt_locs)
        print("hit2", flush=True)
        logs += f'{instance["instance_id"]} {[len(i) for i in trace_list]}\n'
        container.cleanup()
    if (not tgt_locs) or (not trace_list):
        logs += instance["instance_id"] + " Case 2\n"
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
        added_locs = get_added_loc(instance["patch"]) # post solution patch
        logs += instance["instance_id"] + json.dumps(added_locs, indent=True) + "\n"
        neighbor_locs = get_neighbor_loc(instance["patch"]) # pre patch
        logs += instance["instance_id"] + json.dumps(neighbor_locs, indent=True) + "\n"
        patch = instance["test_patch"] + "\n\n" + instance["patch"] 
        cmd = "; ".join(instance["addtional_setup_cmd"])
        post_tracer = StackTraceExtractorByInjection(instance["instance_id"], container, patch, cmd)
        print("hit3", flush=True)
        added_trace_list: list[Stack] = post_tracer.extract(instance["f2p_cmd"], added_locs)
        logs += f'{instance["instance_id"]} {[len(i) for i in added_trace_list]}\n'
        trace_list: list[Stack] = []
        for trace in added_trace_list:
            cutoff = post_tracer.cutoff_trace_by_loc(trace, added_locs)
            if cutoff:
                trace_list.append(cutoff)
        container.cleanup()
        container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
        pre_tracer = StackTraceExtractorByInjection(instance["instance_id"], container, instance["test_patch"], cmd)
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
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future_to_instance = {executor.submit(proc_instance, instance): instance for instance in instances}
        
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result = future.result(timeout=1800)
                with write_lock:
                    with open("data/verified/7_test_loc_context_api_injected.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout after 30 minutes: {instance['instance_id']}")
            except Exception as e:
                print(f"Failed: {instance['instance_id']} - {str(e)}")
    return

if __name__ == "__main__":
    done=[]
    if os.path.exists("data/verified/7_test_loc_context_api_injected.jsonl"):
        with open("data/verified/7_test_loc_context_api_injected.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/verified/6_test_loc_context_api.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    instances = [i for i in instances if (i["instance_id"] not in done) and (not i["error_context"])]
    print(len(instances))
    #import random
    #instances=main(random.sample(instances,5))
    main(instances)
