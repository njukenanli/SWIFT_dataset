```bash
pip install .

python -m swebench.harness.run_evaluation     --dataset_name /home/v-kenanli/workspace/ablation/data/verified/3_verified_test_cmd_content.jsonl     --split test     --namespace swebench     --predictions_path ...     --max_workers 8     --run_id  ... > log_.out 2>&1

```