import json, os, sys
from src.utils.go.content_extract import Extractor
from tqdm import tqdm
import traceback

err = 0

with open("data/pro/go/1_test_cmd.jsonl") as f:
    verified_filtered = [json.loads(i) for i in f.readlines()]
print(f"all : {len(verified_filtered)}")

done = []
if os.path.exists("data/pro/go/2_test_cmd_content.jsonl"):
    with open("data/pro/go/2_test_cmd_content.jsonl") as f:
        done = [json.loads(i) for i in f.readlines()]
done_ids = set([i["instance_id"] for i in done])
print(f"done : {len(done)}")

todos = [i for i in verified_filtered if i["instance_id"] not in done_ids]
print(f"todo : {len(todos)}")

for idx in tqdm(range(len(todos))):
    row = todos[idx]
    try:
        test_case: dict[str, str] = Extractor.get_testcase(row["instance_id"], row["repo"], row["base_commit"], row["f2p_parsed"], row["test_patch"])
    except Exception as e:
        error_msg = str(e)
        error_trace = traceback.format_exc()
        print(f"Error processing {row['instance_id']}: {row['base_commit']}")
        print(error_msg)
        print(error_trace)
        print("=====================\n", flush = True)
        err += 1
        continue
    todos[idx]["F2P_content"] = test_case
    #print(row["instance_id"])
    #for key in test_case.keys():
    #    print("#",key)
    #    print(test_case[key])
    #print("=================================\n\n\n",flush=True)
    with open("data/pro/go/2_test_cmd_content.jsonl", "a") as f:
        json.dump(verified_filtered[idx], f)
        f.write("\n")

print(err)