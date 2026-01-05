python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_con_4o.json     --max_workers 8     --run_id  verified_con_4o > log_verified_con_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_api_4o.json     --max_workers 8     --run_id  verified_api_4o > log_verified_api_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_loc_4o.json     --max_workers 8     --run_id  verified_loc_4o > log_verified_loc_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_reg_4o.json     --max_workers 8     --run_id  verified_reg_4o > log_verified_reg_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_rep_4o.json     --max_workers 8     --run_id  verified_rep_4o > log_verified_rep_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_reg_rep_4o.json     --max_workers 8     --run_id  verified_reg_rep_4o > log_verified_reg_rep_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_all_4o.json     --max_workers 8     --run_id  verified_all_4o > log_verified_all_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path logs/preds/old_verified_loc_test_4o.json     --max_workers 8     --run_id  verified_loc_test_4o > log_verified_loc_test_4o.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl     --split test     --namespace starryzhang     --predictions_path logs/preds/live_reg_rep_5.json      --max_workers 4     --run_id live_reg_rep_5 > log_live_reg_rep.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl     --split test     --namespace starryzhang     --predictions_path logs/preds/live_base_50_claude.json      --max_workers 4     --run_id live_base_50_claude > log_live_base_50_claude.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl     --split test     --namespace starryzhang     --predictions_path logs/preds/live_api_46_claude.json      --max_workers 4     --run_id live_api_46_claude > log_live_api_46_claude.out 2>&1

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl     --split test     --namespace starryzhang     --predictions_path logs/preds/live_con_50_claude.json      --max_workers 4     --run_id live_con_50_claude > log_live_con_50_claude.out 2>&1