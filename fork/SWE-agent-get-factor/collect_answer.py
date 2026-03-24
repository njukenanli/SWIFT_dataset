import json

ds_dir = "dataset/pro_go_50.jsonl"

target_dir = {
    "reproduction": "trajectories/v-kenanli/ablation_rep_cl__claude-sonnet-4-6__t-0.00__p-1.00__c-0.00___swe_bench_dataset/pro_go_50.jsonl_dev/preds.json",
    "location": "trajectories/v-kenanli/ablation_loc_cl__claude-sonnet-4-6__t-0.00__p-1.00__c-0.00___swe_bench_dataset/pro_go_50.jsonl_dev/preds.json",
    "context": "trajectories/v-kenanli/ablation_con_cl__claude-sonnet-4-6__t-0.00__p-1.00__c-0.00___swe_bench_dataset/pro_go_50.jsonl_dev/preds.json",
    "api": "trajectories/v-kenanli/ablation_api_cl__claude-sonnet-4-6__t-0.00__p-1.00__c-0.00___swe_bench_dataset/pro_go_50.jsonl_dev/preds.json"
}

out_dir = "dataset/forward_pro_go_cl46.jsonl"

target = {}

for i in target_dir.keys():
    with open(target_dir[i]) as f:
        target[i] = json.load(f)

with open(ds_dir) as f:
    ds = [json.loads(i) for i in f]

for idx in range(len(ds)):
    instance = ds[idx]
    instance["addtional_setup_cmd"] = []
    instance_id = instance["instance_id"]
    loc_raw = target["location"][instance_id]["model_patch"]
    if len(loc_raw.strip())<5:
        print(instance_id, "empty loc", loc_raw)
        
    try:
        loc = json.loads(loc_raw)
        instance["location_content"] = {
            f"{k}: line number range {v['line_ranges']}": v["code_snippets"]
            for k, v in loc.items()
        }
        instance["location"] = {
            f"{k}": f"line number range {v['line_ranges']}"
            for k, v in loc.items()
        }
    except Exception as e:
        print(f"Location json decode error:{e}")
        instance["location_content"] = {"locations": loc}
        instance["location"] = {"locations": loc}
    test_patch = target["reproduction"][instance_id]["model_patch"]
    if not test_patch.strip() or len(test_patch.splitlines()) < 10:
        print(instance_id, "empty repro test")
    instance["before_repo_set_cmd"] = ""
    instance["test_patch"] = test_patch
    lang = instance.get("repo_language", "python").strip().lower()
    test_file = "/testbed/reproduction_test.go" if lang == "go" else "/testbed/reproduction.py"
    file_content = "\n".join([i.lstrip("+") for i in test_patch.splitlines() if (len(i) == 1 and i[0] == "+") or (len(i) > 1 and i[0] == "+" and i[1] != "+") ])
    if len(file_content.strip()) < 5:
        print(instance_id, "empty production")
    instance["F2P_content"] = {test_file: file_content}
    instance["f2p_cmd"] = "python reproduction.py" if lang == "python" else "go test -run TestReproduce -v"
    instance["FAIL_TO_PASS"] = "reproduction.py" if lang == "python" else "TestReproduce"

    context_raw = target["context"][instance_id]["model_patch"]
    if len(context_raw.strip()) < 5:
        print(instance_id, "empty context")
    try:
        instance["error_context"] = json.loads(context_raw)
    except:
        print(instance_id, "context json decode error")
        instance["error_context"] = {"context stack": context_raw}

    api_raw = target["api"][instance_id]["model_patch"]
    if len(api_raw.strip()) < 5:
        print(instance_id, "empty api")
    try:
        instance["api"] = json.loads(api_raw)
    except:
        print(instance_id, "api json decode error")
        instance["api"] = {"api utils": api_raw}
    
    ds[idx] = instance

with open(out_dir, "w") as f:
    for i in ds:
        f.write(json.dumps(i)+"\n")


