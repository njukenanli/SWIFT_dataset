```bash

python -m pip install --upgrade pip && pip install --editable .

# remember that the swerex lib in SWE-agent needs to be replaced by our modified version venv/lib/python3.12/site-packages/swerex.

pip install openai azure-identity-broker --upgrade

nohup sweagent run-batch --config config/ablation_rep_cl.yaml --num_workers 1 --instances.type swe_bench --instances.subset dataset/live_50.jsonl > log_live_cl_rep.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_rep.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_loc_rep.out 2>&1 &


```