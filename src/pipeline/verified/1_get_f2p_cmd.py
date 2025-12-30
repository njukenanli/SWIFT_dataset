import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from src.utils.pyt.fix_django_nl_testcase import find_nl_testcase
from src.utils.runtime import SetupRuntime
import time

#ERROR = 0

# Thread lock for file writing
file_write_lock = threading.Lock()

def proc_pytest_tox(instance: dict, cmd: str, f2p: list[str]) -> tuple[str, list] | None:
    global ERROR
    comp = cmd.split()
    comp = [i for i in comp if ".py" not in i]
    comp.extend([f'"{i}"' for i in f2p])
    final_cmd = " ".join(comp)
    container = SetupRuntime.from_launch_image(f"swebench/sweb.eval.x86_64.{instance['instance_id']}".replace("__", "_1776_"), instance['instance_id'])
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["test_patch"]}\nNEW_PATCH""")
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["patch"]}\nNEW_PATCH""")
    res = container.send_command(final_cmd, timeout = 10*60)
    container.cleanup()
    if int(res.metadata.exit_code) != 0:
        print(f"{instance['instance_id']} :: {res.output}", flush = True)
        ERROR += 1
        return None
    return final_cmd, f2p

def proc_django(instance: dict, cmd: str, f2p: list[str], lock) -> tuple[str, list] | None:
    prefix = "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1  "
    test_list: list[str] = []
    parsed_f2p: list[str] = []
    for i in f2p:
        try:
            comp = i.strip().split()
            if len(comp) > 2:
                django_format, pytest_format = find_nl_testcase(instance, i, lock)
                test_list.append(django_format)
                parsed_f2p.append(pytest_format)
            else:
                testcase, mod = i.strip().split()
                mod = mod[1:-1]
                test_list.append(f"{mod}.{testcase}")
                parsed_f2p.append(f"{mod}.{testcase}")
        except Exception as e:
            print(f"{instance['instance_id']} :: {e}", flush = True)
            global ERROR
            ERROR+=1
            return None
    return prefix + " ".join(test_list), parsed_f2p

def proc_sympy(instance: dict, cmd, f2p: list[str]) ->  tuple[str, list] | None:
    instance_id = instance["instance_id"]
    comp = cmd.strip().split()
    files = [i for i in comp if ".py" in i]
    container = SetupRuntime.from_launch_image(f"swebench/sweb.eval.x86_64.{instance_id}".replace("__", "_1776_"), instance_id)
    full_name_list = []
    for testcase in f2p:
        for file in files:
            container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["test_patch"]}\nNEW_PATCH""")
            container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["patch"]}\nNEW_PATCH""")
            container.send_command(f"pip install pytest")
            res = container.send_command(f"pytest -rA {file}::{testcase}", timeout = 10*60)
            container.cleanup()
            if int(res.metadata.exit_code) == 0:
                full_name_list.append(f"{file}::{testcase}")
                break
        else:
            print(f"{instance_id}::{testcase} not runnable!", flush = True)
            print(res.output)
            global ERROR
            ERROR+=1
            return None
    return "pytest -rA  " + " ".join(full_name_list), full_name_list

def process_single_instance(instance_dict: dict, lock) -> dict | None:
    """Process a single instance with timeout handling."""
    try:
        instance_id = instance_dict["instance_id"]
        cmd = instance_dict["test_cmd"]
        f2p = instance_dict["FAIL_TO_PASS"]
        f2p = json.loads(f2p) if isinstance(f2p, str) else f2p
        
        if "pytest" in cmd or "tox" in cmd:
            addtional_setup_cmd = []
            res = proc_pytest_tox(instance_dict, cmd, f2p)
        elif "django" in instance_id:
            addtional_setup_cmd = []
            res = proc_django(instance_dict, cmd, f2p, lock)
        elif "sympy" in instance_id:
            addtional_setup_cmd = ["pip install pytest", ]
            res = proc_sympy(instance_dict, cmd, f2p)
        else:
            raise ValueError(f"{instance_id} not covered!")

        time.sleep(30) 
        
        if res is None:
            return None
            
        instance_dict["f2p_cmd"], instance_dict["f2p_parsed"] = res
        instance_dict["addtional_setup_cmd"] = addtional_setup_cmd
        with lock:
            with open("data/verified_test_cmd_added.jsonl", "a") as f:
                json.dump(instance_dict, f)
                f.write("\n")
        print(f"Success! {instance_id}", flush=True)
        return instance_dict
        
    except Exception as e:
        print(f"Error processing {instance_dict.get('instance_id', 'unknown')}: {e}", flush=True)
        global ERROR
        ERROR += 1
        return None

def transform(info_list: list[dict]) -> list[dict]:
    global ERROR
    success_list = []
    timeout_seconds = 30 * 60  # 30 minutes in seconds
    
    # Use ThreadPoolExecutor for multi-threading
    with ThreadPoolExecutor(max_workers=32) as executor:  # Adjust max_workers as needed
        # Submit all tasks
        future_to_instance = {
            executor.submit(process_single_instance, instance_dict, file_write_lock): instance_dict["instance_id"]
            for instance_dict in info_list
        }
        
        # Collect results with timeout
        for future in future_to_instance:
            instance_id = future_to_instance[future]
            try:
                result = future.result(timeout=timeout_seconds)
                if result is not None:
                    success_list.append(result)
            except TimeoutError:
                print(f"Timeout (30 min) exceeded for {instance_id}", flush=True)
                ERROR += 1
            except Exception as e:
                print(f"Unexpected error for {instance_id}: {e}", flush=True)
                ERROR += 1
    
    print(f"Total errors: {ERROR}")
    return success_list

def main():
    global ERROR
    ERROR = 0
    with open("data/test_cmd.json") as f:
        cmd_dict = dict(json.load(f))
    with open("data/swe_bench_verified.jsonl") as f:
        info_list = [json.loads(i) for i in f.read().splitlines()]
    with open("data/real_testcase.json") as f:
        real_testcase = json.load(f)
    filtered_info_list = []
    processed = []
    if os.path.exists("data/verified_test_cmd_added.jsonl"):
        with open("data/verified_test_cmd_added.jsonl") as f:
            processed = [json.loads(i) for i in f]
    processed_id = set([i["instance_id"] for i in processed])
    for idx in range(len(info_list)):
        if info_list[idx]["instance_id"] not in real_testcase.keys():
            continue
        if info_list[idx]["instance_id"] in processed_id:
            continue
        info_list[idx]["test_cmd"] = cmd_dict[info_list[idx]["instance_id"]]
        info_list[idx]["FAIL_TO_PASS"] = real_testcase[info_list[idx]["instance_id"]]["FAIL_TO_PASS"]
        info_list[idx]["PASS_TO_PASS"] = real_testcase[info_list[idx]["instance_id"]]["PASS_TO_PASS"]
        filtered_info_list.append(info_list[idx])
    print("before:", len(filtered_info_list) + len(processed))
    print("processing:", len(filtered_info_list) )
    filtered_info_list = transform(filtered_info_list) + processed
    print("after:", len(filtered_info_list))

if __name__ == "__main__":
    main()