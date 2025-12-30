import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from src.utils.runtime import SetupRuntime

parser_script = """
def get_passed(log: str) -> set[str]:
    import sys
    sys.path.append("/home/v-kenanli/workspace/ablation/fork/SWE-bench-Pro/run_scripts/{instance_id}")
    from parser import parse_test_output
    results = parse_test_output(log,"")
    return set([result.name for result in results if result.status.name == "PASSED"])
"""

def parse_log(instance_id: str, log: str) -> set[str]:
    script = parser_script.format(instance_id=instance_id)
    namespace = {}
    exec(script, {}, namespace)
    return namespace["get_passed"](log)

def proc_instance(d):
    instance_id = d["instance_id"]
    with open(f"/home/v-kenanli/workspace/ablation/fork/SWE-bench-Pro/run_scripts/{instance_id}/run_script.sh") as f:
        script = f.read()
    container = SetupRuntime.from_launch_image(d["image"], instance_id, "linux", None, "/app")
    d["addtional_setup_cmd"] = ["cat > test_script.sh <<'TEST_PATCH_FILE'\n" + script + "\nTEST_PATCH_FILE\n"]
    res = container.send_command(d["addtional_setup_cmd"][0])
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{d["test_patch"]}\nNEW_PATCH""")
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{d["patch"]}\nNEW_PATCH""")
    d["FAIL_TO_PASS"] = eval(d["fail_to_pass"])
    d["PASS_TO_PASS"] = eval(d["pass_to_pass"]) 
    del d["fail_to_pass"], d["pass_to_pass"]
    d["test_cmd"] =  "bash test_script.sh  " + "  ".join([f'"{i}"' for i in eval(d["selected_test_files_to_run"])])
    res = container.send_command(d["test_cmd"])
    if int(res.metadata.exit_code) != 0 and set(parse_log(instance_id, res.output)) & set(d["PASS_TO_PASS"]) != set(d["PASS_TO_PASS"]):
        os.makedirs(f"data/pro/{lang}/error", exist_ok=True)
        with open(f"data/pro/{lang}/error/{d['instance_id']}.txt", "w") as f:
            print(res.output, file=f)
        container.cleanup()
        return None
    d["f2p_cmd"] = "bash test_script.sh  " + "  ".join([f'"{i}"' for i in d["FAIL_TO_PASS"]])
    d["f2p_parsed"] = d["FAIL_TO_PASS"]
    res = container.send_command(d["f2p_cmd"])
    if int(res.metadata.exit_code) != 0 and "not found" in res.output.lower():
        d["f2p_parsed"] = list(set([i.split("(")[0].split("[")[0] for i in d["FAIL_TO_PASS"]]))
        d["f2p_cmd"] = "bash test_script.sh  " + "  ".join([f'"{i}"' for i in d["f2p_parsed"]])
        res = container.send_command(d["f2p_cmd"])
    container.cleanup()
    if int(res.metadata.exit_code) == 0 or set(parse_log(instance_id, res.output)) & set(d["FAIL_TO_PASS"]) == set(d["FAIL_TO_PASS"]):
        return d
    if ("fatal IO error" in res.output) and ("FAILED" not in res.output):
        return d
    os.makedirs(f"data/pro/{lang}/error", exist_ok=True)
    with open(f"data/pro/{lang}/error/{d['instance_id']}.txt", "w") as f:
        print(res.output, file=f)
    return None

def process_with_timeout(instance):
    return proc_instance(instance)

def main(lang):
    input_dir = f"data/pro/{lang}/original_pro.jsonl"
    output_dir = f"data/pro/{lang}/1_test_cmd.jsonl"
    done = set()
    if os.path.exists(output_dir):
        with open(output_dir) as f:
            done_list = [json.loads(i) for i in f]
            done = set(i["instance_id"] for i in done_list)
    print("Done", len(done))
    with open(input_dir) as f:
        l = [json.loads(i) for i in f]
        print("All", len(l))
        l = [i for i in l if i["instance_id"] not in done]
        print("Left", len(l))
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_with_timeout, instance): instance for instance in l}
        
        for future in futures:
            try:
                d = future.result(timeout=1800)
                if d is not None:
                    with open(output_dir, "a") as f:
                        print("Success!", d["instance_id"], flush = True)
                        f.write(json.dumps(d) + "\n")
                else:
                    instance = futures[future]
                    print("Error!", instance["instance_id"], flush = True)
            except TimeoutError:
                instance = futures[future]
                print(f"Timeout processing {instance['instance_id']}", flush=True)
            except Exception as e:
                instance = futures[future]
                print(f"Exception processing {instance['instance_id']}: {e}", flush=True)


if __name__ == "__main__":
    for lang in ["pyt", 
                 #"go", 
                 #"node",
                 ]:
        main(lang)