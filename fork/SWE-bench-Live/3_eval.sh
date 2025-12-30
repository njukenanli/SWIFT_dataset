python -m swebench.harness.run_validation \
    --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl \
    --predictions_path gold \
    --max_workers 10 \
    --run_id three_validation_1 \
    --namespace starryzhang


python -m swebench.harness.run_validation \
    --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl \
    --predictions_path gold \
    --max_workers 10 \
    --run_id three_validation_2 \
    --namespace starryzhang

python -m swebench.harness.run_validation \
    --dataset_name /home/v-kenanli/workspace/ablation/data/live/3_verified_test_loc.jsonl \
    --predictions_path gold \
    --max_workers 10 \
    --run_id three_validation_3 \
    --namespace starryzhang
