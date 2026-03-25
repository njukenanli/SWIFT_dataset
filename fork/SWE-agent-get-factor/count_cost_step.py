import os, json
import argparse

base_dir = "/home/v-kenanli/workspace/ablation/fork/SWE-agent/trajectories/v-kenanli/ablation_all__gpt-5-20250807__t-0.00__p-1.00__c-0.00___swe_bench_/home/v-kenanli/workspace/ablation/data/pro/pyt/subset/overlap.jsonl_dev"
parser = argparse.ArgumentParser()
parser.add_argument("--base", type=str)
args = parser.parse_args()
if hasattr(args, "base") and args.base is not None:
    base_dir = args.base

count = 0
step = 0
cost = 0.0

for instance_id in os.listdir(base_dir):
    if os.path.exists(f"{base_dir}/{instance_id}/{instance_id}.traj"):
        with open(f"{base_dir}/{instance_id}/{instance_id}.traj") as f:
            info = json.load(f)["info"]["model_stats"]
        count += 1
        step += info["api_calls"]
        cost += info["instance_cost"]
print("Num instances:", count)
print("Avg cost:", cost/count)
print("Avg step:", step/count)
        