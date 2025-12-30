import json, os

with open("data/live/0_verified_validated_1.jsonl") as f:
    ds = [json.loads(i) for i in f]

print(len(ds))

base_dir = "fork/SWE-bench-Live/logs/run_evaluation/437-validation/gold"

res = []

# iter base_dir/{instance_id}/report.json
for idx in range(len(ds)):
    instance_id = ds[idx]["instance_id"]
    if not os.path.exists(f"{base_dir}/{instance_id}/report.json"):
        print(f"Warning! {base_dir}/{instance_id}/report.json not exists!")
        continue
    with open(f"{base_dir}/{instance_id}/report.json") as f:
        report = json.load(f)
    if not report[instance_id]["resolved"]: 
        continue
    if len(report[instance_id]["tests_status"]["FAIL_TO_PASS"]["success"]) == 0:
        continue
    ds[idx]["FAIL_TO_PASS"] = report[instance_id]["tests_status"]["FAIL_TO_PASS"]["success"]
    ds[idx]["PASS_TO_PASS"] = report[instance_id]["tests_status"]["PASS_TO_PASS"]["success"]
    ds[idx]["test_cmds"] = [ds[idx]["test_cmds"][-1]]
    res.append(ds[idx])

print(len(res))

with open("data/live/0_verified_validated.jsonl", "w") as f:
    for i in res:
        json.dump(i, f)
        f.write("\n")
    
