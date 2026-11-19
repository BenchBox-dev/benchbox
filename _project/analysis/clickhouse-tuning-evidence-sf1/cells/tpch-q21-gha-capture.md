# Capture for TPC-H Q21 on ClickHouse 25.8.33.6

Commit 3847e49e8ce16ee30b0b2da43a17fc788708ba22; harness output `/home/runner/work/_temp/measurement/clickhouse-tpch-q21/harness/harness.json`.

## Harness arms

| Arm | Passed | Failed | Peak memory (GiB) | Join setting | Plan join algorithms | Error |
|---|---|---|---|---|---|---|
| T | 10 | 0 | 3.40 | hash |  |  |
| N2 | 10 | 0 | 3.40 | hash |  |  |
| S | 10 | 0 | 3.40 | hash |  |  |
| N | 10 | 0 | 3.40 | hash |  |  |

## Setting sweep on the notuning arm

| Candidate | Settings | Result | Rows | Peak memory (GiB) | Seconds | Error |
|---|---|---|---|---|---|---|
| default | {} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| hash | {"join_algorithm": "hash"} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| parallel_hash | {"join_algorithm": "parallel_hash"} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| grace_hash_8 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 8} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| grace_hash_32 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| partial_merge | {"join_algorithm": "partial_merge"} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| full_sorting_merge | {"join_algorithm": "full_sorting_merge"} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| auto | {"join_algorithm": "auto"} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| hash_threads_2 | {"join_algorithm": "hash", "max_threads": 2} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| grace_hash_32_threads_2 | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32, "max_threads": 2} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| grace_hash_32_spill | {"join_algorithm": "grace_hash", "grace_hash_join_initial_buckets": 32, "max_bytes_in_join": 1073741824, "max_bytes_before_external_group_by": 1073741824, "max_bytes_before_external_sort": 1073741824} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
| partial_merge_spill | {"join_algorithm": "partial_merge", "max_bytes_in_join": 1073741824, "max_bytes_before_external_group_by": 1073741824, "max_bytes_before_external_sort": 1073741824} | fail |  | 0.00 | 0.0 | ServerException: Code: 60.
DB::Exception: Unknown table expression identifier 'supplier' in scope SELECT s_name, COUNT(*) AS numwait FROM supplier CROSS JOIN lineitem AS l1 CROSS JOIN orders CROSS JOI |
