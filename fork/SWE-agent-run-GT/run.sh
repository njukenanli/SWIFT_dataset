nohup sweagent run-batch     --config config/multi/ablation_con_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_con_api_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_loc_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_con_loc_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_reg_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified_101_con_reg_4o.out 2>&1 &

echo "hit1"
sleep 10

nohup sweagent run-batch     --config config/multi/ablation_con_rep_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_con_rep_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_loc_api_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_reg_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_loc_reg_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_loc_rep_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_loc_rep_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_reg_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_reg_api_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_rep_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_rep_api_4o.out 2>&1 &

echo "hit2"
sleep 10

nohup sweagent run-batch     --config config/ablation_non_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_non_4o.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_loc_api_4o.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/verified/subset/overlap.jsonl  > log_verified101_con_loc_api_4o.out 2>&1 &

echo "hit3"
sleep 10

#===========

nohup sweagent run-batch     --config config/multi/ablation_con_api.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/overlap.jsonl  > log_propy51_con_api_gpt5.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_loc.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/overlap.jsonl  > log_propy51_con_loc_gpt5.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_rep.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/overlap.jsonl  > log_propy51_con_rep_gpt5.out 2>&1 &

nohup sweagent run-batch     --config config/multi/ablation_con_loc_api.yaml     --num_workers 1     --instances.type swe_bench     --instances.subset /home/v-kenanli/workspace/ablation/data/pro/pyt/subset/overlap.jsonl  > log_propy51_con_loc_api_gpt5.out 2>&1 &

echo "hit4"
sleep 10