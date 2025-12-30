import json, os, sys
from src.utils.pyt.content_extract import Extractor
from tqdm import tqdm
import traceback

err = 0

def name_processor(instance: str, f2p: str) -> str:
    if "fonttools" in instance["instance_id"]:
        if "Tests/feaLib/builder_test.py::BuilderTest::" in f2p:
            test = "Tests/feaLib/builder_test.py::BuilderTest::check_feature_file"
            data_name = f2p.split("::")[-1].replace("test_Fea2feaFile_", "")
            inp = f"Tests/feaLib/data/{data_name}.fea"
            out = f"Tests/feaLib/data/{data_name}.ttx"
            content = Extractor.get_testcase(instance["instance_id"], instance["repo"], instance["base_commit"], [test, inp, out], row["test_patch"])
            formatted_content = ""
            for k, v in content.items():
                formatted_content += f"### Sub Module: {k}\n"
                formatted_content += f"{v}\n\n\n====================================\n"
            return formatted_content
    if "streamlink" in instance["instance_id"]:
        if "plugins" in f2p:
            comps = f2p.split("::")
            parent_func = f"tests/plugins/__init__.py::PluginCanHandleUrl::{comps[-1]}"
            test_class = "::".join(comps[:-1] + ["*"])
            content = Extractor.get_testcase(instance["instance_id"], instance["repo"], instance["base_commit"], [parent_func, test_class], row["test_patch"])
            formatted_content = ""
            for k, v in content.items():
                formatted_content += f"### Sub Module: {k}\n"
                formatted_content += f"{v}\n\n\n--------------------------------------\n"
            return formatted_content
    if "keras" in instance["instance_id"]:
        comps = f2p.split("::")
        real_test = None
        checklist = ["test_identity_basics",
                     "test_restored_multi_output_type",
                     "test_shape_mismatch_error",
                     "test_basic_flow",
                     "test_epoch_callbacks",
                     "test_fit_flow",
                     "test_steps_per_epoch",
                     "test_steps_per_execution_unrolled_steps_steps_count",
                     "test_steps_per_execution_steps_count_unknown_dataset",
                     "test_steps_per_execution_steps_per_epoch",
                     "test_steps_per_execution_steps_per_epoch_unknown_data_size",
                     "test_steps_per_execution_steps_count_without_training",
                     "test_steps_per_execution_steps_count",
                     "test_average_pooling1d_same_padding",
                     "test_average_pooling2d_same_padding",
                     "test_average_pooling3d_same_padding",
                     "test_average_pooling1d",
                     "test_average_pooling2d",
                     "test_average_pooling3d",
                     "test_global_average_pooling1d",
                     "test_global_average_pooling2d",
                     "test_global_average_pooling3d",
                     "test_global_max_pooling1d",
                     "test_global_max_pooling2d",
                     "test_global_max_pooling3d",
                     "test_max_pooling1d",
                     "test_max_pooling2d",
                     "test_max_pooling3d", ]
        for check in checklist:
            if check in comps[-1]:
                real_test = check
                break
        if real_test is not None:
            comps[-1] = real_test
            testcase = "::".join(comps)
            return list(Extractor.get_testcase(instance["instance_id"], instance["repo"], instance["base_commit"], [testcase], row["test_patch"]).values())[0]

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

with open("data/live/1_verified_f2p_cmd.jsonl") as f:
    verified_filtered = [json.loads(i) for i in f.readlines()]
print(f"all : {len(verified_filtered)}")

done = []
if os.path.exists("data/live/2_verified_test_cmd_content.jsonl"):
    with open("data/live/2_verified_test_cmd_content.jsonl") as f:
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
    with open("data/live/2_verified_test_cmd_content.jsonl", "a") as f:
        json.dump(verified_filtered[idx], f)
        f.write("\n")

print(err)