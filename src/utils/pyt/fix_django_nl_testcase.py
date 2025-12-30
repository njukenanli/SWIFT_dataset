import json
import pathlib
import shlex
import subprocess
from src.utils.runtime import SetupRuntime

repo_path = "repos/django"
test_dir = "repos/django/tests"

def traverse(instance: dict, target: str) -> str | None:
    subprocess.run(["git", "checkout", "--quiet", instance["base_commit"]],
                    cwd=repo_path, check=True)
    tmp_name = ".tmp_swebench.patch"
    patch_file = pathlib.Path(repo_path) / ".tmp_swebench.patch"
    patch_file.write_text(instance["test_patch"], encoding="utf-8")
    subprocess.run(
        shlex.split(f"git apply -v {tmp_name}"),
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    patch_file.unlink(missing_ok=True)

    # target is part of docstring of a python function (target in function.docstring)
    # traverse folders and files folder recursively under test_dir to find the function 
    
    import ast
    import os
    
    def find_function_with_docstring(file_path: str, target: str) -> str | None:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                # Check functions
                if isinstance(node, ast.FunctionDef):
                    docstring = ast.get_docstring(node)
                    if docstring and target in docstring:
                        # Check if function is inside a class
                        for parent in ast.walk(tree):
                            if isinstance(parent, ast.ClassDef):
                                for child in ast.walk(parent):
                                    if child is node:
                                        rel_path = os.path.relpath(file_path, test_dir)
                                        return f"{rel_path}::{parent.name}::{node.name}"
                        # Function not in class
                        rel_path = os.path.relpath(file_path, test_dir)
                        return f"{rel_path}::{node.name}"
                        
                # Check class methods
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            docstring = ast.get_docstring(item)
                            if docstring and target in docstring:
                                rel_path = os.path.relpath(file_path, test_dir)
                                return f"{rel_path}::{node.name}::{item.name}"
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            pass
        return None
    
    # Traverse the test directory recursively
    for root, dirs, files in os.walk(test_dir):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                result = find_function_with_docstring(file_path, target)
                if result is not None:
                    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_path,
                                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["git", "clean", "-fd"], cwd=repo_path,
                                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return result

    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "clean", "-fd"], cwd=repo_path,
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # return the result in format: admin_custom_urls/models.py::class_name::function_name or  proxy_model_inheritance/app1/models.py::function_name
    # If not found, return None
    return None

def try_command(instance: dict, cmd: str):
    instance_id = instance["instance_id"]
    container = SetupRuntime.from_launch_image(f"swebench/sweb.eval.x86_64.{instance_id}".replace("__", "_1776_"), instance_id)
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["test_patch"]}\nNEW_PATCH""")
    container.send_command(f"""git apply - <<'NEW_PATCH'\n{instance["patch"]}\nNEW_PATCH""")
    res = container.send_command(cmd, timeout = 10*60)
    container.cleanup()
    if int(res.metadata.exit_code) != 0:
        print(res.output)
        return False
    return True

def find_nl_testcase(instance: dict, target: str, lock = None):
    '''return: full testcase name'''
    if lock is not None:
        with lock:
            pytest_format : str | None = traverse(instance, target)
    else:
        pytest_format : str | None = traverse(instance, target)
    if pytest_format is not None:
        django_format = pytest_format.replace("/", ".").replace(".py", "").replace("::", ".")
        if try_command(instance, f"./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1  {django_format}"):
            return django_format, pytest_format
        else:
            raise RuntimeError(f"{django_format} cannot be executed!")
    raise RuntimeError(f"{target} testcase not Found!")

if __name__ == "__main__":
    with open("data/swe_bench_verified.jsonl") as f:
        l = [json.loads(i) for i in f.read().splitlines()]

    with open("data/real_testcase.json") as f:
        d = json.load(f) 

    res = {}

    total = 0

    for idx in range(len(l)):
        instance_id = l[idx]["instance_id"]
        if instance_id not in d.keys():
            continue
        if "django" in instance_id:
            total += 1
            try:
                new_f2ps = []
                for f2p in d[instance_id]["FAIL_TO_PASS"]:
                    if len(f2p.strip().split()) > 2:
                            testcase = find_nl_testcase(l[idx], f2p)
                            new_f2ps.append(testcase)
                    else:
                        new_f2ps.append(f2p)
                res[instance_id] = new_f2ps
            except Exception as e:
                print(e)
