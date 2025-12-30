import json
from src.utils.pyt.content_extract import Extractor
from tqdm import tqdm

with open("data/verified/2_verified_test_cmd_added.jsonl") as f:
    verified_filtered = [json.loads(i) for i in f.readlines()]

for idx in tqdm(range(len(verified_filtered))):
    row = verified_filtered[idx]
    instance_id = row["instance_id"]
    test_patch = row["test_patch"]
    test_case_path = row["f2p_parsed"]
    test_case: list[dict[str, str]] = Extractor.get_testcase(instance_id, row["repo"], row["base_commit"], test_case_path, row["test_patch"])
    verified_filtered[idx]["F2P_content"] = test_case

with open("data/verified/3_verified_test_cmd_content.jsonl", "w") as f:
    for i in verified_filtered:
        json.dump(i, f)
        f.write("\n")