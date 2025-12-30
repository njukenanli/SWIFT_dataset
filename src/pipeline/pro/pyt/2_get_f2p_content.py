import json, os, sys
from src.utils.pyt.content_extract import Extractor
from tqdm import tqdm
import traceback

err = 0

def name_processor(instance: str, f2p: str) -> str:
    return list(Extractor.get_testcase(instance["instance_id"], instance["repo"], instance["base_commit"], [f2p], row["test_patch"]).values())[0]

def retry(instance) -> dict[str, str] | None:
    res = {}
    for f2p in instance["f2p_parsed"]:
        res[f2p] = name_processor(instance, f2p)
    #print()
    #print(instance["instance_id"])
    #for k, v in res.items():
    #    print(f"# {k}")
    #    print(v)
    return res

with open("data/pro/pyt/1_test_cmd.jsonl") as f:
    verified_filtered = [json.loads(i) for i in f.readlines()]
print(f"all : {len(verified_filtered)}")

done = []
if os.path.exists("data/pro/pyt/2_test_cmd_content.jsonl"):
    with open("data/pro/pyt/2_test_cmd_content.jsonl") as f:
        done = [json.loads(i) for i in f.readlines()]
done_ids = set([i["instance_id"] for i in done])
print(f"done : {len(done)}")

todos = [i for i in verified_filtered if i["instance_id"] not in done_ids]
print(f"todo : {len(todos)}")

for idx in tqdm(range(len(todos))):
    row = todos[idx]
    test_case_path = row["f2p_parsed"]
    try:
        test_case: dict[str, str] = Extractor.get_testcase(row["instance_id"], row["repo"], row["base_commit"], test_case_path, row["test_patch"])
    except Exception as e:
        try:
            # clear traceback cached here!
            import sys
            sys.exc_clear() if hasattr(sys, 'exc_clear') else None
            e.__traceback__ = None
            test_case: dict[str, str] = retry(row)
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
    with open("data/pro/pyt/2_test_cmd_content.jsonl", "a") as f:
        json.dump(verified_filtered[idx], f)
        f.write("\n")

print(err)