
from tqdm import tqdm
import json
from src.utils.go.content_extract import Extractor
from src.utils.go.diff import extract_changed_symbols, extract_file_line

err = 0

with open("data/pro/go/2_test_cmd_content.jsonl") as f:
    verified_filtered = [ json.loads(i) for i in f.readlines() ]
    print(len(verified_filtered))

for idx in tqdm(range(len(verified_filtered))):
    row = verified_filtered[idx]
    loc_hint = extract_changed_symbols(row["patch"])
    loc_line = extract_file_line(row["patch"])
    assert ".rst" not in str(loc_line.keys()), loc_line.keys()
    assert "/doc/" not in str(loc_line.keys()), loc_line.keys()
    try:
        loc_content = Extractor.get_content(row["repo"], row["base_commit"], loc_line)
    except Exception as e:
        print(row["instance_id"], row["base_commit"])
        print(e)
        err+=1
    verified_filtered[idx]["location"] = loc_hint
    verified_filtered[idx]["location_content"] = loc_content
    #print(row["instance_id"])
    #print(json.dumps(loc_hint,indent=True))
    #for key in loc_content.keys():
    #    print("#", key)
    #    print(loc_content[key])
    #    print("==========================\n")

with open("data/pro/go/3_test_loc.jsonl", "w") as f:
    for i in verified_filtered:
        json.dump(i, f)
        f.write("\n")



print(err)