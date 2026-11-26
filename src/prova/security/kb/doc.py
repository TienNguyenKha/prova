"""KbDoc: the one shape every source of the knowledge base is read into, whatever its format.

A doc keeps the structure of its source (sections by name, relations by kind), the URL of the official page
to cite, and what it may be filtered by, apart from its text. `content_hash` covers what the doc says, not the
version of the catalog it came in: a new release that leaves an entry alone leaves its hash alone, so it is
not embedded again.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KbError(Exception):
    """A source cannot be read as it should be: a schema that changed, XML that does not parse or that tries
    entity tricks, an entry without what it must have. Never an empty or half-read result."""


class KbDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)  # "CWE-918", "GHSA-xxxx", "CVE-2026-…", "owasp:SSRF_Prevention#…"
    source: str
    version: str  # the version of the catalog or snapshot it came from
    license: str
    trust: Literal["official", "community"]  # community-written text is data, never instructions
    title: str
    status: str = ""
    url: str  # the official page to cite
    sections: dict[str, str]  # description, mitigations, examples… with the names of the source
    relations: dict[str, list[str]] = {}  # child_of, capec, … to ids of other docs
    metadata: dict[str, str] = {}  # what it is filtered by (abstraction, languages, …), not embedded
    content_hash: str = ""


def with_hash(doc: KbDoc) -> KbDoc:
    """`doc` with the sha256 of what it says: title, status, sections, relations and metadata."""
    said = {
        "title": doc.title,
        "status": doc.status,
        "sections": doc.sections,
        "relations": doc.relations,
        "metadata": doc.metadata,
    }
    digest = hashlib.sha256(json.dumps(said, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return doc.model_copy(update={"content_hash": digest})
