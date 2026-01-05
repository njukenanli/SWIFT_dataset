#python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/gpt5-reg-rep-pyt.json   --output_dir=logs/eval/gpt5-reg-rep-pyt    --scripts_dir=run_scripts    --num_workers=4   --use_local_docker   --dockerhub_username=jefzda > log-gpt5-reg-rep-pyt.out 2>&1

#python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/gpt5-api-pyt.json   --output_dir=logs/eval/gpt5-api-pyt    --scripts_dir=run_scripts    --num_workers=4    --use_local_docker  --dockerhub_username=jefzda > log-gpt5-api-pyt.out 2>&1

python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/claude-base-pyt.json   --output_dir=logs/eval/claude-base-pyt    --scripts_dir=run_scripts    --num_workers=4    --use_local_docker  --dockerhub_username=jefzda > log-claude-base-pyt.out 2>&1

python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/claude-reg-pyt.json   --output_dir=logs/eval/claude-reg-pyt    --scripts_dir=run_scripts    --num_workers=4    --use_local_docker  --dockerhub_username=jefzda > log-claude-reg-pyt.out 2>&1

python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/claude-rep-pyt.json   --output_dir=logs/eval/claude-rep-pyt    --scripts_dir=run_scripts    --num_workers=4    --use_local_docker  --dockerhub_username=jefzda > log-claude-rep-pyt.out 2>&1

python swe_bench_pro_eval.py  --raw_sample_path=/home/v-kenanli/workspace/ablation/data/pro/pyt/original_pro.jsonl  --patch_path=logs/preds/claude-loc-pyt.json   --output_dir=logs/eval/claude-loc-pyt    --scripts_dir=run_scripts    --num_workers=4    --use_local_docker  --dockerhub_username=jefzda > log-claude-loc-pyt.out 2>&1