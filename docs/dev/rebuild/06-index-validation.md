# 06 Index Validation

用途：验证 `06-feature-index.csv` 与 `06-feature-ledger.md` 的行映射、决策一致性和桥接计数。只读脚本，不修改仓库。

## 不变量

- CSV 518 行（不含表头）；ledger 518 行表项。
- 每个 domain 的 CSV 行数、顺序、feature 文本与 ledger 完全一致。
- CSV `ledger_line` 等于对应 ledger 表项的实际行号（1-based）。
- CSV `decision` 等于 ledger 最后一列“决定”。
- 无 `pending`。
- P1-P10 行数分别为 31/43/97/44/70/25/67/42/62/37，总 518。
- D1 冲突项：D05-016..018 drop、D05-019 port、D05-021 port、D05-036..037 port、D05-039 drop、D06-066 port；相关 note 必须出现 `active_requests` 或明确“无 lease”。
- 第二轮冲突项：D01-054..058 drop、D01-059 port、D01-060..062 keep、D03-019 drop、D04-027/028 drop、D05-026 port、D07-026 keep、D08-022 keep；D05-026 note 必须包含“同账号/429/503”，D05-039 note 必须区分“手动/后台”，D08-022 note 必须包含“唤醒/SLEEPING”。

## 校验脚本

从仓库根运行：

```python
import csv
import re
import sys
from collections import Counter, defaultdict

CSV = "docs/dev/rebuild/06-feature-index.csv"
LEDGER = "docs/dev/rebuild/06-feature-ledger.md"
EXPECTED_PACKAGES = {
    "P1": 31, "P2": 43, "P3": 97, "P4": 44, "P5": 70,
    "P6": 25, "P7": 67, "P8": 42, "P9": 62, "P10": 37,
}
EXPECTED_DECISIONS = {
    "D01-054": "drop", "D01-055": "drop", "D01-056": "drop",
    "D01-057": "drop", "D01-058": "drop", "D01-059": "port",
    "D01-060": "keep", "D01-061": "keep", "D01-062": "keep",
    "D03-034": "drop", "D04-031": "drop", "D04-057": "drop",
    "D03-019": "drop", "D04-027": "drop", "D04-028": "drop",
    "D05-016": "drop", "D05-017": "drop", "D05-018": "drop",
    "D05-019": "port", "D05-021": "port", "D05-036": "port",
    "D05-037": "port", "D05-039": "drop", "D06-066": "port",
    "D05-026": "port", "D07-026": "keep", "D08-022": "keep",
}
REQUIRED_NOTES = {
    "D05-026": ("同账号", "429", "503"),
    "D05-039": ("手动", "后台"),
    "D08-022": ("唤醒", "SLEEPING"),
}
errors = []

with open(CSV, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
with open(LEDGER, encoding="utf-8", newline="") as f:
    lines = f.read().replace("\r\n", "\n").splitlines()

ledger = defaultdict(list)
for lineno, line in enumerate(lines, 1):
    m = re.match(r"^\|\s*(D\d\d)(?:\s+[^|]*)?\s*\|", line)
    if not m:
        continue
    cells = line.split("|")
    ledger[m.group(1)].append((lineno, cells[2].strip(), cells[8].strip()))

if len(rows) != 518:
    errors.append(f"CSV rows={len(rows)}, expected 518")
if sum(len(v) for v in ledger.values()) != 518:
    errors.append(f"ledger rows={sum(len(v) for v in ledger.values())}, expected 518")

by_domain = defaultdict(list)
for row in rows:
    by_domain[row["domain"]].append(row)

for domain, domain_rows in by_domain.items():
    ledger_rows = ledger[domain]
    if len(domain_rows) != len(ledger_rows):
        errors.append(f"{domain}: csv={len(domain_rows)} ledger={len(ledger_rows)}")
        continue
    for row, (lineno, feature, decision) in zip(domain_rows, ledger_rows):
        if row["feature"] != feature:
            errors.append(f"{row['feature_id']}: feature text mismatch")
        if row["ledger_line"] != str(lineno):
            errors.append(f"{row['feature_id']}: ledger_line={row['ledger_line']} actual={lineno}")
        if row["decision"] != decision:
            errors.append(f"{row['feature_id']}: decision={row['decision']} ledger={decision}")

packages = Counter(row["decision_package"] for row in rows)
if dict(packages) != EXPECTED_PACKAGES:
    errors.append(f"packages={dict(packages)}")
if any(row["decision"] == "pending" for row in rows):
    errors.append("pending decision found")

for feature_id, expected in EXPECTED_DECISIONS.items():
    row = next((r for r in rows if r["feature_id"] == feature_id), None)
    if row is None:
        errors.append(f"{feature_id}: missing")
    elif row["decision"] != expected:
        errors.append(f"{feature_id}: decision={row['decision']} expected={expected}")

for feature_id in ("D05-021", "D05-036", "D05-037", "D06-066"):
    row = next((r for r in rows if r["feature_id"] == feature_id), None)
    if row and "active_requests" not in row["decision_note"]:
        errors.append(f"{feature_id}: note must mention active_requests")

for feature_id, keywords in REQUIRED_NOTES.items():
    row = next((r for r in rows if r["feature_id"] == feature_id), None)
    if row is None:
        errors.append(f"{feature_id}: missing")
    else:
        for keyword in keywords:
            if keyword not in row["decision_note"]:
                errors.append(f"{feature_id}: note must mention {keyword}")

LOG = "docs/dev/rebuild/06-decision-log.md"
with open(LOG, encoding="utf-8") as f:
    log_lines = f.read().replace("\r\n", "\n").splitlines()
p9q3 = [line for line in log_lines if "P9-Q3" in line]
forbidden_p9q3 = ("`D01-058`..`D01-062` → drop", "D01-058..D01-062 → drop")
if not p9q3 or any(x in p9q3[0] for x in forbidden_p9q3):
    errors.append("P9-Q3 must not drop logging features D01-059..D01-062")

if errors:
    print("FAIL")
    for error in errors:
        print("-", error)
    sys.exit(1)

print("OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0")
```

预期输出：

```text
OK rows=518 ledger=518 packages=31/43/97/44/70/25/67/42/62/37 total=518 errors=0
```

## 修复方法

- `ledger_line` 不按旧值手改；按 domain 顺序把 CSV 行与 ledger 表项配对，取 ledger 实际行号。
- feature 文本不一致时先修正文本，再重算行号；不要只改行号掩盖顺序错误。
- decision 冲突时以 `06-decision-log.md` 的 P2/P3 决策为准，同时更新 CSV、ledger 决定列和索引脚本的 `EXPECTED_DECISIONS`。
- 任何 06 变更后重跑本脚本；`FAIL` 时不得进入 Gate 0 之后步骤。
