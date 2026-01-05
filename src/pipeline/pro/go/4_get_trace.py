import json
import shutil, os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from threading import Lock
from src.utils.go.cov import ErrorStackExtractor, Trace
from src.utils.runtime import SetupRuntime

write_lock = Lock()

def proc_instance(instance):
    image = instance["image"]
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
    #patch = instance["test_patch"] 
    patch= instance["patch"]
    cmd = "; ".join(instance["before_repo_set_cmd"].split("\n")+instance["addtional_setup_cmd"])
    tracer = ErrorStackExtractor(instance["instance_id"], container, patch, cmd)
    trace_list: list[Trace] = tracer.get_last_error_stack_trace("bash test_script.sh  "+",".join(eval(instance["selected_test_files_to_run"])))
    container.cleanup()
    instance["error_context"] = trace_list
    return instance


def main(instances):
    #shutil.rmtree("data/logs", ignore_errors=True)
    #os.makedirs("data/logs", exist_ok=True)
    
    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_instance = {executor.submit(proc_instance, instance): instance for instance in instances}
        
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result = future.result(timeout=1800)
                with write_lock:
                    with open("data/pro/go/4_test_loc_context.jsonl", "a") as f:
                        f.write(json.dumps(result)+"\n")
            except TimeoutError:
                print(f"Timeout after 30 minutes: {instance['instance_id']}")
                instance["error_context"] = []
                with write_lock:
                    with open("data/pro/go/4_test_loc_context.jsonl", "a") as f:
                        f.write(json.dumps(instance)+"\n")
            except Exception as e:
                print(f"Failed: {instance['instance_id']} - {str(e)}")
    return

if __name__ == "__main__":
    done=[]
    if os.path.exists("data/pro/go/4_test_loc_context.jsonl"):
        with open("data/pro/go/4_test_loc_context.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/pro/go/3_test_loc.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    print(len(done))
    instances = [i for i in instances if i["instance_id"] not in done]
    print(len(instances),flush=True)
    import random
    sample = random.sample(instances, 5)
    main(sample)
    #main(instances)
