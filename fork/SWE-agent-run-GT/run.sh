nohup sweagent run-batch     --config config/multi/ablation_loc_reg.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_loc_reg.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_rep.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_loc_rep.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_api.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_loc_api.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_reg_api.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_reg_api.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_rep_api.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/api.jsonl  > log_pro_pyt_rep_api.out 2>&1 &

sleep 2d

nohup sweagent run-batch     --config config/multi/ablation_loc_reg_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/api.jsonl  > log_pro_pyt_loc_reg_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_rep_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/api.jsonl  > log_pro_pyt_loc_rep_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/api.jsonl  > log_pro_pyt_loc_api_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_reg_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/api.jsonl  > log_pro_pyt_reg_api_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_rep_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/api.jsonl  > log_pro_pyt_rep_api_4o.out 2>&1 &