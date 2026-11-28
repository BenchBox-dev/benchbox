# Capture for TPC-H Q21 on ClickHouse 26.8.46.6

Commit 62a0c9b0f8dc0eaad44f0f9bcf94ee97e68cbc27; harness output `/home/runner/work/_temp/measurement/clickhouse-tpch-q21/harness/harness.json`.

## Harness arms

| Arm | Passed | Failed | Peak memory (GiB) | Join setting | Plan join algorithms | Error |
|---|---|---|---|---|---|---|
| T | 10 | 0 | 0.08 | hash | SpillingHashJoin |  |
| N2 | 10 | 0 | 0.08 | hash | SpillingHashJoin |  |
| S | 10 | 0 | 0.07 | hash | SpillingHashJoin |  |
| N | 10 | 0 | 0.08 | hash | SpillingHashJoin |  |

## Setting sweep on the notuning arm

| Candidate | Settings | Result | Rows | Peak memory (GiB) | Seconds | Error |
|---|---|---|---|---|---|---|
| default | {} | pass | 100 | 0.07 | 0.1 |  |
| hash | {"join_algorithm": "hash"} | pass | 100 | 0.08 | 0.1 |  |
| parallel_hash | {"join_algorithm": "parallel_hash"} | pass | 100 | 0.07 | 0.1 |  |
| grace_hash_8 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8} | pass | 100 | 0.16 | 0.2 |  |
| grace_hash_32 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32} | pass | 100 | 0.63 | 0.2 |  |
| partial_merge | {"join_algorithm": "partial_merge"} | pass | 100 | 0.05 | 0.1 |  |
| full_sorting_merge | {"join_algorithm": "full_sorting_merge"} | pass | 100 | 0.08 | 0.2 |  |
| auto | {"join_algorithm": "auto"} | pass | 100 | 0.08 | 0.1 |  |
| hash_threads_2 | {"join_algorithm": "hash", "max_threads": 2} | pass | 100 | 0.07 | 0.2 |  |
| grace_hash_32_threads_2 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32, "max_threads": 2} | pass | 100 | 0.62 | 0.3 |  |
| grace_hash_32_spill | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32, "max_bytes_in_join": 1073741824, "max_bytes_before_external_group_by": 1073741824, "max_bytes_before_external_sort": 1073741824} | pass | 100 | 0.63 | 0.3 |  |
| partial_merge_spill | {"join_algorithm": "partial_merge", "max_bytes_in_join": 1073741824, "max_bytes_before_external_group_by": 1073741824, "max_bytes_before_external_sort": 1073741824} | pass | 100 | 0.05 | 0.1 |  |
