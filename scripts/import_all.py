"""Build and validate one complete data generation before publishing it."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from file_lock import FinanceWriteLock
from serve import atomic_write_bytes


REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DATA_DIR = REPO_ROOT / "app" / "data"
LOCK_PATH = REPO_ROOT / "tmp" / ".finance-data.lock"
GENERATED_FILES = (
    "card_transactions.json",
    "account_transactions.json",
    "transactions.json",
)


def run(script, staging, strict=False):
    command = [sys.executable, str(REPO_ROOT / "scripts" / script)]
    if strict:
        command.append("--strict")
    environment = os.environ.copy()
    environment["FINANCE_DATA_DIR"] = str(staging)
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = "\n".join(
        value.strip() for value in (completed.stdout, completed.stderr) if value.strip()
    )
    if completed.returncode:
        raise RuntimeError(output or "%s exited with %d" % (script, completed.returncode))
    if output:
        print(output)


def stamp_generation(staging, generation_id):
    for name in GENERATED_FILES:
        path = staging / name
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["generationId"] = generation_id
        path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="publish only when all manual review queues are also empty",
    )
    args = parser.parse_args(argv)
    (REPO_ROOT / "tmp").mkdir(exist_ok=True)
    LIVE_DATA_DIR.mkdir(parents=True, exist_ok=True)

    with FinanceWriteLock(LOCK_PATH, timeout=5):
        with tempfile.TemporaryDirectory(
            prefix="finance-import-", dir=REPO_ROOT / "tmp"
        ) as temporary:
            staging = Path(temporary)
            run("parse_cc.py", staging)
            run("parse_one.py", staging)
            run("build_data.py", staging)
            run("validate_data.py", staging, strict=args.strict)

            generation_id = "generation_" + uuid.uuid4().hex
            stamp_generation(staging, generation_id)
            # Publish the main dashboard last. A browser that lands in the tiny
            # replacement window sees a generation mismatch and refuses to draw.
            for name in GENERATED_FILES:
                atomic_write_bytes(LIVE_DATA_DIR / name, (staging / name).read_bytes())

    print("Published validated generation %s." % generation_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
