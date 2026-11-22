"""Cache syntax and line ownership for the pinned sources during image build."""
import pickle
from pathlib import Path

from impact import Source

if __name__ == "__main__":
    root = Path("/src")
    for folder in ("sqlglot", "tests"):
        for path in sorted((root / folder).rglob("*.py")):
            dest = Path("/baseline") / (path.relative_to(root).as_posix() + ".bin")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                pickle.dump(Source(path.read_text(encoding="utf-8")), f, protocol=5)
