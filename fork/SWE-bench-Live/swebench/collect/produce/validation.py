from __future__ import annotations
import json
import os

def main(tgt) -> None:
    reference = f"data/live/.bak/{tgt}"
    NUM_INVALID=0
    val1 = "fork/SWE-bench-Live/logs/run_evaluation/three_validation_1/gold"
    val2 = "fork/SWE-bench-Live/logs/run_evaluation/three_validation_2/gold"
    val3 = "fork/SWE-bench-Live/logs/run_evaluation/three_validation_3/gold"


    with open(reference) as f:
        ref = [json.loads(i) for i in f]
        ref = {i["instance_id"]: i for i in ref}
    
    for instance_id in ref.keys():
        for base_dir in [val1, val2, val3]:
            cur_dir = os.path.join(base_dir, instance_id)
            try:
                with open(os.path.join(cur_dir, "instance.json")) as f:
                    dct = json.load(f)
            except:
                #print("dir not found:", instance_id)
                continue

            F2P = set(dct.get("FAIL_TO_PASS", [])) 
            P2P = set(dct.get("PASS_TO_PASS", []))
            F2P_ref = set(ref[instance_id]["FAIL_TO_PASS"])
            P2P_ref = set(ref[instance_id]["PASS_TO_PASS"])
            if P2P != P2P_ref or F2P != F2P_ref:
                continue
            with open(os.path.join(cur_dir, "post_test_map.json")) as f:
                mapping = json.load(f)
            INVALID_TESTS = [i for i in mapping.keys() if mapping[i] != "PASSED"]
            ref[instance_id]["INVALID_TESTS"] = INVALID_TESTS
            #print(instance_id, len(INVALID_TESTS))
            break
        if ref[instance_id].get("INVALID_TESTS", None) is None:
            NUM_INVALID += 1
    print("Num invalid:", NUM_INVALID)
    print("All:", len(ref))

    with open(f"data/live/{tgt}", "w") as f:
        for l in ref.values():
            if l.get("INVALID_TESTS", None) is not None:
                f.write(json.dumps(l) + "\n")

if __name__ == "__main__":
    tgts = ["0_verified_validated.jsonl", "1_verified_f2p_cmd.jsonl", "2_verified_test_cmd_content.jsonl","3_verified_test_loc.jsonl"]
    for tgt in tgts:
        main(tgt)

