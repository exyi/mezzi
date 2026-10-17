import json
from collections import defaultdict

if __name__ == "__main__":
    with open("assets/serialized-ctcae-clean.json", "r", encoding="utf8") as f:
        records = json.load(f)

    mapping: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        mapping[record["soc"]].add(record["id"])

    _m = {key: list(value) for key, value in mapping.items()}

    with open("assets/grouped-ctcae.json", "w", encoding="utf8") as f:
        json.dump(_m, f, ensure_ascii=False, indent=4)
        
    print(f"Saved mapping of questions into groups, found {len(mapping)} in total")
