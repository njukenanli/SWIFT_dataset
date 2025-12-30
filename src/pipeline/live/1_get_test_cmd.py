import json
import os
import re
import traceback
from src.utils.runtime import SetupRuntime
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

lock = Lock()

def try_pytest_format_instance(instance: dict, lock) -> str | None:
    f2p_set = set(instance["FAIL_TO_PASS"])
    f2p_cmd = instance["test_cmds"][-1] + "  " + "  ".join([f'"{i}"' for i in f2p_set])
    container = SetupRuntime.from_launch_image(f"starryzhang/sweb.eval.x86_64.{instance['instance_id']}".replace("__", "_1776_").lower(), instance['instance_id'])
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["test_patch"]}\nNEW_PATCH""")
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["patch"]}\nNEW_PATCH""")
    res = container.send_command(f2p_cmd, timeout = 30*60)
    out = res.output.lower()
    if int(res.metadata.exit_code) == 0:
        container.cleanup()
        return f2p_cmd, list(f2p_set)
    if "not found" in out or "no match" in out or "syntax error" in out or "no tests" in out:
        f2p_set = set([i.split("[")[0].split("(")[0] for i in f2p_set])
        f2p_cmd = instance["test_cmds"][-1] + "  " + "  ".join([f'"{i}"' for i in f2p_set])
        res = container.send_command(f2p_cmd, timeout = 30*60)
        out = res.output.lower()
        if int(res.metadata.exit_code) == 0:
            container.cleanup()
            return f2p_cmd, list(f2p_set)
    if re.search(r'coverage.*not reached', out):
        f2p_cmd += "  --cov-fail-under=0 "
        res = container.send_command(f2p_cmd, timeout = 30*60)
        out = res.output.lower()
        if int(res.metadata.exit_code) == 0:
            container.cleanup()
            return f2p_cmd, list(f2p_set)
    container.cleanup()
    if "not found" in out or "no match" in out:
        with lock:
            with open(f"data/live/error/notfound/{instance['instance_id']}.txt", "w") as f:
                print(f"{f2p_cmd}\n{res.output}", file = f, flush = True)
        return None
    if "timeout" in out:
        with lock:
            with open(f"data/live/error/timeout/{instance['instance_id']}.txt", "w") as f:
                print(f"{f2p_cmd}\n{res.output}", file = f, flush = True)
        return None
    if "failed" in out:
        with lock:
            with open(f"data/live/error/fail/{instance['instance_id']}.txt", "w") as f:
                print(f"{f2p_cmd}\n{res.output}", file = f, flush = True)
        return None
    with lock:
        with open(f"data/live/error/other/{instance['instance_id']}.txt", "w") as f:
            print(f"{f2p_cmd}\n{res.output}", file = f, flush = True)
    return None

def process_instance(instance: dict, lock) -> dict | None:
    if os.path.exists(f"data/live/error/fail/{instance['instance_id']}.txt"):
        return None
    f2p_cmd = try_pytest_format_instance(instance, lock)
    if f2p_cmd is not None:
        instance["f2p_cmd"], instance["f2p_parsed"] = f2p_cmd
        instance["addtional_setup_cmd"] = []
        with lock:
            with open("data/live/1_verified_f2p_cmd.jsonl", "a") as f:
                json.dump(instance, f)
                f.write("\n")
        return instance
    return None

def test(instances: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_instance, instance, lock): instance for instance in instances}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result is not None:
                    print(f"Successfully processed: {result['instance_id']}", flush = True)
            except Exception as e:
                instance = futures[future]
                print(f"Error processing {instance['instance_id']}: {e} \n{traceback.format_exc()}", flush = True)

if __name__ == "__main__":
    done = []
    if os.path.exists("data/live/1_verified_f2p_cmd.jsonl"):
        with open("data/live/1_verified_f2p_cmd.jsonl") as f:
            done = [json.loads(i) for i in f]
    done_ids = set([i["instance_id"] for i in done])
    with open("data/live/0_verified_validated.jsonl") as f:
        l = [json.loads(i) for i in f]
    print(f"loading {len(l)} instances...")
    left = [i for i in l if i["instance_id"] not in done_ids]
    print(f"processing {len(left)} instances")
    test(left)
    
