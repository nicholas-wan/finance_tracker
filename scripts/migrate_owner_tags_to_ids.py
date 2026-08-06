"""Bind legacy positional owner tags to stable transaction IDs.

Run after parse_cc.py and build_data.py. Existing ``tags`` are retained as a
recovery fallback; ``tagsById`` becomes authoritative on the next build.
"""

import json
import os
import tempfile


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER_PATH = os.path.join(REPO_ROOT, "manual", "owner_tags.json")
TRANSACTIONS_PATH = os.path.join(REPO_ROOT, "app", "data", "transactions.json")


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main():
    owners = load(OWNER_PATH)
    built = load(TRANSACTIONS_PATH)
    tags_by_id = owners.setdefault("tagsById", {})
    added = unchanged = 0
    conflicts = []
    for row in built.get("transactions", []):
        if row.get("ownerSource") not in ("exact-id", "exact-legacy"):
            continue
        tx_id = row["id"]
        owner = row["owner"]
        existing = tags_by_id.get(tx_id)
        if existing and existing != owner:
            conflicts.append((tx_id, existing, owner))
        elif existing == owner:
            unchanged += 1
        else:
            tags_by_id[tx_id] = owner
            added += 1
    if conflicts:
        for tx_id, existing, built_owner in conflicts[:20]:
            print("%s: tagsById=%s, current build=%s" % (tx_id, existing, built_owner))
        raise SystemExit("Refusing to overwrite %d conflicting stable owner tag(s)" % len(conflicts))

    owners["tagsById"] = dict(sorted(tags_by_id.items()))
    directory = os.path.dirname(OWNER_PATH)
    fd, temporary = tempfile.mkstemp(prefix="owner_tags.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(owners, handle, indent=1)
            handle.write("\n")
        os.replace(temporary, OWNER_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(
        "Bound %d exact owner tag(s) to stable IDs; %d already matched. "
        "Legacy positional tags were retained."
        % (added, unchanged)
    )


if __name__ == "__main__":
    main()
