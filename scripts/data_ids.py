"""Deterministic transaction IDs and compact source provenance.

The ID is a hash of what the bank actually charged - month, dates, card section,
description, amount, direction - plus an occurrence counter that separates
genuinely identical rows. It deliberately excludes the file name and the
page/line coordinates the row was extracted from.

Those coordinates used to be part of the hash. That made every ID a function of
the PDF's name and of its text layer's line numbering, so renaming a statement,
or a pypdf upgrade that shifted lines by one, re-minted every ID in the file -
and could hand a row the ID that used to belong to its neighbour. Every piece of
hand-entered data (owner tags, category overrides, remarks, recognized risk
reviews, audit history) is keyed by these IDs, so that silently detached or
misattributed the user's own decisions. File, page and line remain in the
provenance block, where they document where a row came from without deciding
what it is called.
"""

import hashlib
import json
import os
from collections import defaultdict


def source_name(path):
    """Keep deployed provenance useful without exposing a local absolute path."""
    return os.path.basename(path)


def content_identity(row, source_type, section):
    """The content-derived fields that decide a row's identity.

    Nothing here depends on the file the row was read from or where in that file
    it sat. ``postedDate`` and ``credit`` are absent on account rows and
    ``direction`` is absent on card rows; both resolve to a stable placeholder so
    the two row shapes hash consistently.
    """
    return [
        source_type,
        row.get("month"),
        row.get("date"),
        row.get("postedDate"),
        section,
        " ".join(str(row.get("description", "")).upper().split()),
        "%.2f" % abs(float(row.get("amount", 0))),
        "credit" if row.get("credit") else row.get("direction", "debit"),
    ]


def row_section(row):
    return row.get("card") or row.get("account") or "UOB ONE"


def assign_provenance(rows, source_type, source_file, verified=False):
    """Annotate rows in source order and return the same list.

    ``rows`` must arrive in statement order: the occurrence counter is what
    separates two identical charges on one day, and it is only stable because
    the parsers append rows page by page and line by line. Sorting happens after
    this call, never before it.
    """
    occurrences = defaultdict(int)
    for row in rows:
        page = row.pop("_sourcePage", None)
        line = row.pop("_sourceLine", None)
        section = row_section(row)
        identity = content_identity(row, source_type, section)
        occurrence_key = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
        occurrences[occurrence_key] += 1
        occurrence = occurrences[occurrence_key]
        digest_input = identity + [occurrence]
        digest = hashlib.sha256(
            json.dumps(digest_input, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:20]
        row["id"] = "tx_" + digest
        provenance = {
            "sourceType": source_type,
            "sourceFile": source_file,
            "statementMonth": row.get("month"),
            "section": section,
            "occurrence": occurrence,
            "verified": bool(verified),
        }
        if page is not None:
            provenance["page"] = page
        if line is not None:
            provenance["line"] = line
        row["provenance"] = provenance
    return rows
