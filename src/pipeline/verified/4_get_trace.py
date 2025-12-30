import json
import shutil, os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from threading import Lock
from src.utils.pyt.cov import ErrorStackExtractor, Trace
from src.utils.runtime import SetupRuntime

write_lock = Lock()

def proc_instance(instance):
    image = "swebench/sweb.eval.x86_64." + instance["instance_id"].replace("__", "_1776_")
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"])
    patch = instance["test_patch"] 
    cmd = "; ".join(instance["addtional_setup_cmd"])
    tracer = ErrorStackExtractor(instance["instance_id"], container, patch, cmd)
    trace_list: list[Trace] = tracer.get_last_error_stack_trace(instance["f2p_cmd"])
    container.cleanup()
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
                    with open("data/verified/5_test_loc_context.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout after 30 minutes: {instance['instance_id']}")
            except Exception as e:
                print(f"Failed: {instance['instance_id']} - {str(e)}")
    return

if __name__ == "__main__":
    done=[]
    if os.path.exists("data/verified/5_test_loc_context.jsonl"):
        with open("data/verified/5_test_loc_context.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/verified/4_verified_test_loc.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    instances = [i for i in instances if i["instance_id"] not in done]
    print(len(instances))
    #import random
    #instances=main(random.sample(instances,5))
    main(instances)
