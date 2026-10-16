from pathlib import Path


def load_mapping(file: Path | str) -> dict[str, str]:
    file = Path(file)
    output: dict[str, str] = {}
    with open(file, "r", encoding="utf8") as f:
        for line in f:
            line = line.strip()
            if len(line) > 0:
                ctcae_, names_ = line.split(";")
                output[names_.lower()] = ctcae_.lower()

    return output
