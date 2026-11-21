"""Install just the pinned Python sources used to interpret diff coordinates."""

import argparse
import hashlib
import tarfile
import tempfile
import shutil
from urllib.request import urlopen
from pathlib import Path, PurePosixPath

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
SOURCE_DIGEST = "d42a8ccf5ab63b759023a795014f454b08254a898c71944df2db6bebbfc94759"


def install(archive, destination):
    prefix = f"sqlglot-{PIN}/"
    digest = hashlib.sha256()
    # Read once in archive order; hash names and contents in canonical order
    # so re-compressing the same revision cannot invalidate a final rebuild.
    blobs = {}
    with tarfile.open(archive, mode="r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix) :]
            if not (relative.endswith(".py") or relative == "LICENSE"):
                continue
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or relative in blobs:
                raise ValueError("invalid archive member")
            blobs[relative] = tar.extractfile(member).read()
    for name, content in sorted(blobs.items()):
        digest.update(name.encode() + b"\x00" + hashlib.sha256(content).digest())
    if digest.hexdigest() != SOURCE_DIGEST:
        raise ValueError("SQLGlot source content does not match the pinned revision")
    for name, content in blobs.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="use an existing copy of the exact upstream archive"
    )
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.archive:
        install(args.archive, args.destination)
    else:
        url = f"https://codeload.github.com/tobymao/sqlglot/tar.gz/{PIN}"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "sqlglot.tar.gz"
            with urlopen(url, timeout=75) as source, archive.open("wb") as target:
                shutil.copyfileobj(source, target)
            install(archive, args.destination)
