# SWE-bench Verified 真实切片 fixture

- 来源：HuggingFace `princeton-nlp/SWE-bench_Verified`（`data/test-00000-of-00001.parquet`）。
- 截取：按数据集顺序取前 6 条（`astropy__astropy-12907` … `astropy__astropy-13579`），
  13 个上游字段逐字保留（含 `hints_text`），未做任何改写。
- 用途：`tests/backend/test_dataset_adapters.py` 与 `tests/backend/test_datasets.py`
  的导入/映射测试，以及本地真实切片冒烟（见 `reports/` 对应日期目录）。
- CI 不出网：本文件为 vendored 快照。
- sha256：`34fda0cb623821742de3eccf3f4e66891ac0f1c6fa50464173f3cc1ecbae62fa`
- 抽取脚本（一次性，不在仓库运行）：

```sh
python - <<'EOF'
import json, hashlib
import pyarrow.parquet as pq
t = pq.read_table('test-00000-of-00001.parquet').slice(0, 6)
lines = []
for r in t.to_pylist():
    if hasattr(r['created_at'], 'isoformat'):
        r['created_at'] = r['created_at'].isoformat()
    lines.append(json.dumps(r, ensure_ascii=False, sort_keys=True))
payload = '\n'.join(lines) + '\n'
open('verified-slice.jsonl', 'w').write(payload)
print(hashlib.sha256(payload.encode()).hexdigest())
EOF
```
