"""CWE, read from the official XML download (schema 8.0, namespace http://cwe.mitre.org/cwe-8) into KbDoc.

The XML comes from outside, so it is parsed with defusedxml: entity declarations and external references are
refused, never expanded. Any other namespace (an older or newer schema) stops the read with an error that
names it: a schema that changed is never loaded as if it had not. Structured text (XHTML inside some elements)
becomes plain text: paragraphs for prose, and whole blocks with their line breaks for code examples.

Only weaknesses are read; categories, groupings and views are not (yet) part of the knowledge base.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from prova.security.kb.doc import KbDoc, KbError, with_hash

CWE_NAMESPACE = "http://cwe.mitre.org/cwe-8"
XHTML = "http://www.w3.org/1999/xhtml"
LICENSE = (
    "CWE Terms of Use: Copyright The MITRE Corporation; free to use, a copy must reproduce MITRE's copyright "
    "designation and the license (https://cwe.mitre.org/about/termsofuse.html)"
)
_NS = f"{{{CWE_NAMESPACE}}}"
_BLOCKS = frozenset({"p", "div", "li", "ul", "ol", "table", "tr", "pre"})
_SPACES = re.compile(r"\s+")
_RELATIONS = {
    "ChildOf": "child_of",
    "ParentOf": "parent_of",
    "CanPrecede": "can_precede",
    "PeerOf": "peer_of",
}


@dataclass(frozen=True, slots=True)
class Catalog:
    version: str
    date: str
    docs: list[KbDoc]


def parse_catalog(data: bytes) -> Catalog:
    """The weaknesses of a CWE XML catalog. Raises KbError for anything it cannot read as it should."""
    try:
        root = fromstring(data)
    except DefusedXmlException as e:
        raise KbError(f"the XML was refused: {type(e).__name__} (entity declarations are not allowed)") from e
    except ParseError as e:
        raise KbError(f"the catalog is not XML that parses: {e}") from e
    namespace, _, name = root.tag[1:].partition("}") if root.tag.startswith("{") else ("", "", root.tag)
    if name != "Weakness_Catalog":
        raise KbError(f"the root is {name!r}, not Weakness_Catalog")
    if namespace != CWE_NAMESPACE:
        raise KbError(
            f"the catalog uses the namespace {namespace!r}, not {CWE_NAMESPACE!r}: the schema changed"
        )
    version = root.get("Version", "")
    docs = [with_hash(_weakness(w, version)) for w in root.iterfind(f"{_NS}Weaknesses/{_NS}Weakness")]
    return Catalog(version=version, date=root.get("Date", ""), docs=docs)


def _weakness(weakness: Element, version: str) -> KbDoc:
    number = weakness.get("ID")
    if not number or not number.isdigit():
        raise KbError(f"a weakness has no ID (name {weakness.get('Name')!r})")
    sections = {
        "description": _prose(weakness.find(f"{_NS}Description")),
        "extended_description": _prose(weakness.find(f"{_NS}Extended_Description")),
        "common_consequences": "\n\n".join(_consequences(weakness)),
        "potential_mitigations": "\n\n".join(_mitigations(weakness)),
        "demonstrative_examples": "\n\n".join(_examples(weakness)),
    }
    return KbDoc(
        id=f"CWE-{number}",
        source="cwe",
        version=version,
        license=LICENSE,
        trust="official",
        title=weakness.get("Name", ""),
        status=weakness.get("Status", ""),
        url=f"https://cwe.mitre.org/data/definitions/{number}.html",
        sections={key: text for key, text in sections.items() if text},
        relations=_relations(weakness),
        metadata=_metadata(weakness),
    )


def _consequences(weakness: Element) -> Iterator[str]:
    for consequence in weakness.iterfind(f"{_NS}Common_Consequences/{_NS}Consequence"):
        lines = [
            f"Scope: {_joined(consequence, 'Scope')}",
            f"Impact: {_joined(consequence, 'Impact')}",
            _prose(consequence.find(f"{_NS}Note")),
        ]
        yield "\n".join(line for line in lines if line and not line.endswith(": "))


def _mitigations(weakness: Element) -> Iterator[str]:
    for mitigation in weakness.iterfind(f"{_NS}Potential_Mitigations/{_NS}Mitigation"):
        lines = [
            f"Phase: {_joined(mitigation, 'Phase')}",
            f"Strategy: {_joined(mitigation, 'Strategy')}",
            _prose(mitigation.find(f"{_NS}Description")),
        ]
        yield "\n".join(line for line in lines if line and not line.endswith(": "))


def _examples(weakness: Element) -> Iterator[str]:
    for example in weakness.iterfind(f"{_NS}Demonstrative_Examples/{_NS}Demonstrative_Example"):
        parts: list[str] = []
        for child in example:
            if child.tag == f"{_NS}Example_Code":
                label = ", ".join(x for x in (child.get("Nature", ""), child.get("Language", "")) if x)
                parts.append(f"Code ({label}):\n{_code(child)}")
            else:
                parts.append(_prose(child))
        yield "\n\n".join(part for part in parts if part)


def _relations(weakness: Element) -> dict[str, list[str]]:
    relations: dict[str, list[str]] = {}
    for related in weakness.iterfind(f"{_NS}Related_Weaknesses/{_NS}Related_Weakness"):
        kind = _RELATIONS.get(related.get("Nature", ""), related.get("Nature", "").lower())
        target = f"CWE-{related.get('CWE_ID')}"
        if target not in relations.setdefault(kind, []):
            relations[kind].append(target)  # the same parent in two views is one parent
    patterns = [
        f"CAPEC-{p.get('CAPEC_ID')}"
        for p in weakness.iterfind(f"{_NS}Related_Attack_Patterns/{_NS}Related_Attack_Pattern")
    ]
    if patterns:
        relations["capec"] = patterns
    return relations


def _metadata(weakness: Element) -> dict[str, str]:
    metadata = {"abstraction": weakness.get("Abstraction", ""), "structure": weakness.get("Structure", "")}
    languages = [x.get("Name", "") for x in weakness.iterfind(f"{_NS}Applicable_Platforms/{_NS}Language")]
    metadata["languages"] = ", ".join(name for name in languages if name)  # a Class alone names no language
    return {key: value for key, value in metadata.items() if value}


def _joined(element: Element, child: str) -> str:
    return ", ".join((x.text or "").strip() for x in element.iterfind(f"{_NS}{child}"))


# -- structured text -----------------------------------------------------------------------------------


def _lines(element: Element) -> list[str]:
    """The text of `element`, with a line break at each XHTML block and <br/>."""
    out: list[str] = [element.text or ""]
    for child in element:
        local = child.tag.rpartition("}")[2]
        if local == "br":
            out.append("\n")
        elif local in _BLOCKS:
            out += ["\n", *_lines(child), "\n"]
        else:
            out += _lines(child)
        out.append(child.tail or "")
    return out


def _prose(element: Element | None) -> str:
    """Paragraphs, each on one line with its spaces collapsed, separated by a blank line."""
    if element is None:
        return ""
    paragraphs = [_SPACES.sub(" ", line).strip() for line in "".join(_lines(element)).split("\n")]
    return "\n\n".join(p for p in paragraphs if p)


def _code(element: Element) -> str:
    """The lines of a code example as they are, without the blank lines around them."""
    lines = [line.strip() for line in "".join(_lines(element)).split("\n")]
    return "\n".join(line for line in lines if line)
