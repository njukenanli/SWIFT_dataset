
import json
from tqdm import tqdm
from src.utils.pyt.content_extract import Extractor
from src.utils.pyt.diff import extract_changed_symbols, extract_file_line

with open("data/verified/3_verified_test_cmd_content.jsonl") as f:
    verified_filtered = [ json.loads(i) for i in f.readlines() ]
    print(len(verified_filtered))

for idx in tqdm(range(len(verified_filtered))):
    row = verified_filtered[idx]
    loc_hint = extract_changed_symbols(row["patch"])
    loc_line = extract_file_line(row["patch"])
    assert ".rst" not in str(loc_line.keys())
    loc_content = Extractor.get_content(row["repo"], row["base_commit"], loc_line)
    verified_filtered[idx]["location"] = loc_hint
    verified_filtered[idx]["location_content"] = loc_content

with open("data/verified/4_verified_test_loc.jsonl", "w") as f:
    for i in verified_filtered:
        json.dump(i, f)
        f.write("\n")



