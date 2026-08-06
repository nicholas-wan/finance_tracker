"""One-shot: repoint every hand-entered record at the new content-only IDs.

Transaction IDs used to hash the source file name and the page/line the row was
extracted from, so renaming a statement - or a pypdf upgrade that shifted the
text layer by a line - re-minted every ID in that file. ``data_ids.py`` now
hashes only the content fields plus an occurrence counter. This script bridges
the two schemes for the data that only exists because the user typed it:

    manual/owner_tags.json            tagsById
    manual/transaction_overrides.json overridesById
    manual/transaction_remarks.json   remarksById
    manual/risk_reviews.json          recognizedIds
    manual/audit_history.json         transactionId / transactionIds

Run this BEFORE re-running the parsers, while app/data still holds rows carrying
the old IDs alongside their provenance. Each row's provenance records everything
the old hash consumed, so the old ID is recomputed and checked against the one
stored on the row; the new ID is computed from the same row through the new
scheme. A reference with no mapping is fatal - the point of the exercise is that
nothing hand-entered gets dropped.

    python scripts/migrate_ids_to_content_hash.py --dry-run
    python scripts/migrate_ids_to_content_hash.py
"""

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_ids import assign_provenance  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL_DIR = os.path.join(REPO_ROOT, "manual")
DATA_DIR = os.path.join(REPO_ROOT, "app", "data")
BACKUP_TAG = "pre_idhash"


def load(path, default=None):
    if not os.path.exists(path):
        if default is None:
            raise SystemExit("Missing %s" % path)
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def legacy_id(row, provenance):
    """Reproduce the retired hash: content fields plus file, page and line."""
    identity = [
        provenance["sourceType"],
        provenance["sourceFile"],
        row.get("month"),
        row.get("date"),
        row.get("postedDate"),
        provenance["section"],
        " ".join(str(row.get("description", "")).upper().split()),
        "%.2f" % abs(float(row.get("amount", 0))),
        "credit" if row.get("credit") else row.get("direction", "debit"),
        provenance.get("page"),
        provenance.get("line"),
    ]
    digest_input = identity + [provenance["occurrence"]]
    return "tx_" + hashlib.sha256(
        json.dumps(digest_input, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]


def source_order(rows):
    """Recover statement order from provenance.

    The parsers walk pages in order and lines within a page in order, so sorting
    a file's rows by (page, line) reproduces exactly the sequence the occurrence
    counter was handed. CSV and legacy rows carry only a line number.
    """
    return sorted(
        rows,
        key=lambda row: (
            row["provenance"].get("page") or 0,
            row["provenance"].get("line") or 0,
        ),
    )


def mapping_from_published(path, problems):
    """old id -> new id for one published app/data file."""
    rows = load(path).get("transactions", [])
    by_file = {}
    for row in rows:
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            problems.append("%s: row %r has no provenance" % (os.path.basename(path), row.get("id")))
            continue
        by_file.setdefault((provenance["sourceType"], provenance["sourceFile"]), []).append(row)

    pairs = []
    for (source_type, source_file), file_rows in sorted(by_file.items()):
        ordered = source_order(file_rows)
        seen = set()
        for row in ordered:
            coordinate = (row["provenance"].get("page"), row["provenance"].get("line"))
            if coordinate in seen:
                problems.append(
                    "%s: two rows share page/line %r - source order is ambiguous"
                    % (source_file, coordinate)
                )
            seen.add(coordinate)
            recomputed = legacy_id(row, row["provenance"])
            if recomputed != row.get("id"):
                problems.append(
                    "%s: stored id %s does not match the retired scheme (got %s)"
                    % (source_file, row.get("id"), recomputed)
                )
        fresh = copy.deepcopy(ordered)
        for row in fresh:
            provenance = row.pop("provenance")
            if provenance.get("page") is not None:
                row["_sourcePage"] = provenance["page"]
            if provenance.get("line") is not None:
                row["_sourceLine"] = provenance["line"]
            row.pop("id", None)
        assign_provenance(fresh, source_type, source_file)
        pairs.extend((old["id"], new["id"]) for old, new in zip(ordered, fresh))
    return pairs


def mapping_from_legacy(problems):
    """old id -> new id for manual/legacy_transactions.json.

    build_data.py numbers these rows by their position in the file and hands the
    whole list to assign_provenance before dropping the months a statement now
    covers, so the mapping is computed over every row for the same reason.
    """
    rows = load(os.path.join(MANUAL_DIR, "legacy_transactions.json"), {}).get("transactions", [])
    source_type, source_file = "legacy-manual", "legacy_transactions.json"
    old_ids = []
    for line_no, row in enumerate(rows, 1):
        provenance = {
            "sourceType": source_type,
            "sourceFile": source_file,
            "section": row.get("card") or row.get("account") or "UOB ONE",
            "occurrence": 1,
            "page": None,
            "line": line_no,
        }
        old_ids.append(legacy_id(row, provenance))
    if len(set(old_ids)) != len(old_ids):
        problems.append("legacy_transactions.json produced duplicate retired IDs")

    fresh = copy.deepcopy(rows)
    for line_no, row in enumerate(fresh, 1):
        row["_sourceLine"] = line_no
    assign_provenance(fresh, source_type, source_file, verified=False)
    return list(zip(old_ids, [row["id"] for row in fresh]))


def build_mapping():
    problems = []
    pairs = []
    pairs.extend(mapping_from_published(os.path.join(DATA_DIR, "card_transactions.json"), problems))
    pairs.extend(mapping_from_published(os.path.join(DATA_DIR, "account_transactions.json"), problems))
    pairs.extend(mapping_from_legacy(problems))

    mapping = {}
    reverse = {}
    for old, new in pairs:
        if old in mapping and mapping[old] != new:
            problems.append("old id %s maps to both %s and %s" % (old, mapping[old], new))
        mapping[old] = new
        if new in reverse and reverse[new] != old:
            problems.append(
                "new id %s would be shared by old ids %s and %s - manual data would merge"
                % (new, reverse[new], old)
            )
        reverse[new] = old
    return mapping, problems


def backup(path, created):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = "%s.%s_%s.bak" % (path, BACKUP_TAG, stamp)
    with open(path, "rb") as source, open(target, "wb") as sink:
        sink.write(source.read())
    created.append(os.path.relpath(target, REPO_ROOT))


def write_json(path, payload):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    os.replace(tmp_path, path)


def remap_id(tx_id, mapping, missing, where):
    new = mapping.get(tx_id)
    if new is None:
        missing.append("%s references unmapped id %s" % (where, tx_id))
        return tx_id
    return new


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching any file")
    args = parser.parse_args()

    mapping, problems = build_mapping()
    if problems:
        print("REFUSING TO MIGRATE:")
        for problem in problems[:20]:
            print("  -", problem)
        raise SystemExit(1)
    unchanged = sum(1 for old, new in mapping.items() if old == new)
    print("Mapped %d transaction id(s); %d were already identical." % (len(mapping), unchanged))

    missing = []
    audit_missing = []
    rewrites = {}
    counts = {}

    owner_path = os.path.join(MANUAL_DIR, "owner_tags.json")
    owners = load(owner_path)
    tags_by_id = owners.get("tagsById", {})
    new_tags = {}
    for tx_id, owner in tags_by_id.items():
        new_tags[remap_id(tx_id, mapping, missing, "owner_tags.tagsById")] = owner
    if len(new_tags) != len(tags_by_id):
        missing.append("owner_tags.tagsById collapsed from %d to %d entries"
                       % (len(tags_by_id), len(new_tags)))
    owners["tagsById"] = dict(sorted(new_tags.items()))
    rewrites[owner_path] = owners
    counts["owner_tags.json tagsById"] = len(new_tags)

    override_path = os.path.join(MANUAL_DIR, "transaction_overrides.json")
    override_data = load(override_path, {})
    overrides = override_data.get("overridesById", {})
    new_overrides = {}
    for tx_id, value in overrides.items():
        new_overrides[remap_id(tx_id, mapping, missing, "transaction_overrides")] = value
    if len(new_overrides) != len(overrides):
        missing.append("transaction_overrides collapsed from %d to %d entries"
                       % (len(overrides), len(new_overrides)))
    override_data["overridesById"] = dict(sorted(new_overrides.items()))
    rewrites[override_path] = override_data
    counts["transaction_overrides.json overridesById"] = len(new_overrides)

    remark_path = os.path.join(MANUAL_DIR, "transaction_remarks.json")
    remark_data = load(remark_path, {})
    remarks = remark_data.get("remarksById", {})
    new_remarks = {}
    for tx_id, value in remarks.items():
        new_remarks[remap_id(tx_id, mapping, missing, "transaction_remarks")] = value
    if len(new_remarks) != len(remarks):
        missing.append("transaction_remarks collapsed from %d to %d entries"
                       % (len(remarks), len(new_remarks)))
    remark_data["remarksById"] = dict(sorted(new_remarks.items()))
    rewrites[remark_path] = remark_data
    counts["transaction_remarks.json remarksById"] = len(new_remarks)

    risk_path = os.path.join(MANUAL_DIR, "risk_reviews.json")
    risk_data = load(risk_path, {})
    recognized = risk_data.get("recognizedIds", [])
    new_recognized = [remap_id(tx_id, mapping, missing, "risk_reviews.recognizedIds")
                      for tx_id in recognized]
    if len(set(new_recognized)) != len(set(recognized)):
        missing.append("risk_reviews.recognizedIds collapsed from %d to %d distinct ids"
                       % (len(set(recognized)), len(set(new_recognized))))
    risk_data["recognizedIds"] = sorted(set(new_recognized))
    # Group id lists are recomputed by risk_checks.detect_risks on every build,
    # so nothing stored here holds one; remap any that a future schema adds.
    group_references = 0
    for key in ("groupIds", "recognizedGroups", "groups"):
        stored = risk_data.get(key)
        if isinstance(stored, list):
            remapped = []
            for entry in stored:
                if isinstance(entry, str):
                    remapped.append(remap_id(entry, mapping, missing, "risk_reviews.%s" % key))
                    group_references += 1
                elif isinstance(entry, list):
                    remapped.append([
                        remap_id(item, mapping, missing, "risk_reviews.%s" % key) for item in entry
                    ])
                    group_references += len(entry)
                else:
                    remapped.append(entry)
            risk_data[key] = remapped
    rewrites[risk_path] = risk_data
    counts["risk_reviews.json recognizedIds"] = len(risk_data["recognizedIds"])
    counts["risk_reviews.json group ids"] = group_references

    audit_path = os.path.join(MANUAL_DIR, "audit_history.json")
    audit_data = load(audit_path, {"entries": []})
    audit_references = 0
    for entry in audit_data.get("entries", []):
        # History legitimately outlives the rows it describes, so an unresolved
        # id here is reported rather than fatal: it is left exactly as written.
        if isinstance(entry.get("transactionId"), str):
            audit_references += 1
            entry["transactionId"] = remap_id(
                entry["transactionId"], mapping, audit_missing, "audit_history.transactionId")
        if isinstance(entry.get("transactionIds"), list):
            audit_references += len(entry["transactionIds"])
            entry["transactionIds"] = sorted({
                remap_id(tx_id, mapping, audit_missing, "audit_history.transactionIds")
                for tx_id in entry["transactionIds"] if isinstance(tx_id, str)
            })
    rewrites[audit_path] = audit_data
    counts["audit_history.json entries"] = len(audit_data.get("entries", []))
    counts["audit_history.json id references"] = audit_references

    if missing:
        print("\nREFUSING TO MIGRATE - %d unmapped reference(s):" % len(missing))
        for message in missing[:20]:
            print("  -", message)
        raise SystemExit(1)

    for label, count in counts.items():
        print("  %-46s %d" % (label, count))
    if audit_missing:
        print("\n%d audit history reference(s) point at rows that no longer exist; "
              "left unchanged:" % len(audit_missing))
        for message in audit_missing[:10]:
            print("  -", message)

    if args.dry_run:
        print("\nDry run - nothing was written.")
        return

    created = []
    for path in rewrites:
        backup(path, created)
    for path, payload in rewrites.items():
        write_json(path, payload)
    print("\nRewrote %d manual file(s). Backups:" % len(rewrites))
    for name in created:
        print("  -", name)
    print("Now re-run parse_cc.py, parse_one.py, build_data.py and validate_data.py.")


if __name__ == "__main__":
    main()
