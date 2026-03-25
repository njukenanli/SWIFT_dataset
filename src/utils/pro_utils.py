import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from threading import Lock
from src.utils.runtime import SetupRuntime

write_lock = Lock()


def _load_env_exports(instance_id: str) -> list[str]:
    exports: list[str] = []
    dockerfiles = [
        f"fork/SWE-bench-Pro/dockerfiles/base_dockerfile/{instance_id}/Dockerfile",
        f"fork/SWE-bench-Pro/dockerfiles/instance_dockerfile/{instance_id}/Dockerfile",
    ]
    for dockerfile in dockerfiles:
        if not os.path.exists(dockerfile):
            continue
        with open(dockerfile) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ENV "):
                    exports.append(line.replace("ENV", "export", 1))
    return exports


def _run_or_raise(container: SetupRuntime, cmd: str, logs: list[str], step: str):
    res = container.send_command(cmd)
    logs.append(f"$ {cmd}\n{res.output}\n")
    if res.metadata and res.metadata.exit_code != 0:
        raise RuntimeError(f"{step} failed (exit={res.metadata.exit_code}): {cmd}")
    return res


def _resolve_test_cmd(instance: dict) -> str:
    selected = instance.get("selected_test_files_to_run")
    if selected:
        try:
            tests = ast.literal_eval(selected) if isinstance(selected, str) else selected
            if tests:
                return "bash test_script.sh " + " ".join(f'"{t}"' for t in tests)
        except Exception:
            pass
    return instance["f2p_cmd"]

def proc_instance(instance):
    image = instance["image"]
    logs = [f">>>>>>>>>>>>>>>> {instance['instance_id']}\n"]
    container: SetupRuntime = SetupRuntime.from_launch_image(image, instance["instance_id"], "linux", None, "/app")
    try:
        for cmd in _load_env_exports(instance["instance_id"]):
            _run_or_raise(container, cmd, logs, "env export")

        _run_or_raise(container, f"git reset --hard {instance['base_commit']}", logs, "git reset")
        _run_or_raise(container, f"git checkout {instance['base_commit']}", logs, "git checkout")

        container.write_file(instance['patch'], "/tmp/gold_patch.diff")
        _run_or_raise(container, "git apply -v /tmp/gold_patch.diff", logs, "git apply")

        before_last = instance["before_repo_set_cmd"].strip().split("\n")[-1].strip()
        if before_last:
            _run_or_raise(container, before_last, logs, "before_repo_set_cmd")

        for idx, setup_cmd in enumerate(instance.get("addtional_setup_cmd", [])):
            _run_or_raise(container, setup_cmd, logs, f"additional_setup_cmd[{idx}]")

        test_cmd = _resolve_test_cmd(instance)
        test_res = container.send_command(test_cmd, timeout=30 * 60)
        logs.append(f"$ {test_cmd}\n{test_res.output}\n")

        if test_res.metadata and test_res.metadata.exit_code != 0:
            raise RuntimeError(f"test command failed (exit={test_res.metadata.exit_code})")

        return instance
    finally:
        os.makedirs("data/exec_logs", exist_ok=True)
        with open(f"data/exec_logs/{instance['instance_id']}.txt", "w") as f:
            f.write("\n\n=================\n\n".join(logs))
        container.cleanup()
    return instance


def main(instances):
    os.makedirs("data/exec_logs", exist_ok=True)
    
    with ThreadPoolExecutor(max_workers=15) as executor:
        future_to_instance = {executor.submit(proc_instance, instance): instance for instance in instances}
        
        for future in as_completed(future_to_instance):
            instance = future_to_instance[future]
            try:
                result = future.result(timeout=1800)
            except TimeoutError:
                print(f"Timeout after 30 minutes: {instance['instance_id']}")
            except Exception as e:
                print(f"Failed: {instance['instance_id']} - {str(e)}")
    return

if __name__ == "__main__":
    done=[]
    if os.path.exists("data/pro/go/5_test_loc_context_api.jsonl"):
        with open("data/pro/go/5_test_loc_context_api.jsonl") as f:
            done = [json.loads(i)["instance_id"] for i in f]
    with open("data/pro/go/4_test_loc_api.jsonl") as f:
        instances = [json.loads(i) for i in f]
    print(len(instances))
    instances = [i for i in instances if (i["instance_id"] not in done)]
    print(len(instances))
    main(instances[:15])
