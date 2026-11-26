"""CWE, read from the official XML download (schema 8.0) into KbDoc, the one shape every source of the
knowledge base ends in.

The XML is data from outside: it is parsed with defusedxml (entity tricks are refused, not expanded), and a
namespace other than the one of schema 8.0 stops the read with an error that names it: a schema that
changed must never be loaded as if it had not. Text is kept as text: XHTML paragraphs become paragraphs, code
examples stay whole blocks with their line breaks. The sample is small and hand-written, with the shape of the
real XSD.
"""

from pathlib import Path

import pytest

from prova.security.kb.cwe import CWE_NAMESPACE, parse_catalog
from prova.security.kb.doc import KbError

SAMPLE = (Path(__file__).resolve().parents[2] / "data" / "cwe" / "cwec_sample.xml").read_bytes()


def docs() -> dict[str, object]:
    return {doc.id: doc for doc in parse_catalog(SAMPLE).docs}


def test_the_catalog_gives_its_version_and_one_doc_for_each_weakness() -> None:
    catalog = parse_catalog(SAMPLE)
    assert (catalog.version, catalog.date) == ("5.0", "2026-10-28")
    assert [d.id for d in catalog.docs] == ["CWE-918", "CWE-79", "CWE-1"]
    assert CWE_NAMESPACE == "http://cwe.mitre.org/cwe-8"


def test_a_doc_has_its_title_url_source_license_and_trust() -> None:
    ssrf = parse_catalog(SAMPLE).docs[0]
    assert ssrf.title == "Server-Side Request Forgery (SSRF)"
    assert ssrf.url == "https://cwe.mitre.org/data/definitions/918.html"
    assert (ssrf.source, ssrf.version, ssrf.trust, ssrf.status) == ("cwe", "5.0", "official", "Incomplete")
    assert "MITRE" in ssrf.license


def test_structured_text_becomes_plain_paragraphs_with_no_tags() -> None:
    sections = parse_catalog(SAMPLE).docs[0].sections
    assert sections["description"].startswith("The web server receives a URL")
    assert sections["extended_description"] == (
        "By providing URLs to unexpected hosts or ports, attackers can make it appear that the server is "
        "sending the request.\n\nThis can be used to bypass access controls."
    )
    assert "<" not in sections["extended_description"] and "xhtml" not in sections["extended_description"]


def test_consequences_and_mitigations_keep_their_scope_impact_phase_and_strategy() -> None:
    sections = parse_catalog(SAMPLE).docs[0].sections
    assert "Scope: Confidentiality, Integrity" in sections["common_consequences"]
    assert "Impact: Read Application Data" in sections["common_consequences"]
    assert "The server can reach internal hosts." in sections["common_consequences"]
    assert "Phase: Implementation" in sections["potential_mitigations"]
    assert "Strategy: Input Validation" in sections["potential_mitigations"]
    assert "refuse private, loopback and link-local" in sections["potential_mitigations"]


def test_a_code_example_stays_one_block_with_its_lines_and_its_characters() -> None:
    examples = parse_catalog(SAMPLE).docs[0].sections["demonstrative_examples"]
    assert 'url = request.args["url"]\nif len(url) < 2048:\nrequests.get(url)' in examples
    assert "Code (Bad, Python):" in examples and "169.254.169.254" in examples


def test_relations_name_the_parents_once_and_the_attack_patterns() -> None:
    relations = parse_catalog(SAMPLE).docs[0].relations
    assert relations["child_of"] == ["CWE-441"]  # in two views, named once
    assert relations["can_precede"] == ["CWE-610"]
    assert relations["capec"] == ["CAPEC-664"]


def test_what_a_doc_is_filtered_by_is_kept_apart_from_its_text() -> None:
    by_id = {d.id: d for d in parse_catalog(SAMPLE).docs}
    assert by_id["CWE-79"].metadata["languages"] == "JavaScript, PHP"
    assert by_id["CWE-918"].metadata["abstraction"] == "Base"
    assert "languages" not in by_id["CWE-918"].metadata  # no language by name: nothing invented


def test_a_deprecated_weakness_is_kept_with_its_status() -> None:
    deprecated = parse_catalog(SAMPLE).docs[2]
    assert deprecated.status == "Deprecated"


def test_the_content_hash_changes_with_the_text_and_only_with_it() -> None:
    first, again = parse_catalog(SAMPLE).docs[0], parse_catalog(SAMPLE).docs[0]
    assert first.content_hash == again.content_hash and len(first.content_hash) == 64
    changed = parse_catalog(SAMPLE.replace(b"bypass   access", b"get around")).docs[0]
    assert changed.content_hash != first.content_hash
    relabelled = parse_catalog(SAMPLE.replace(b'Version="5.0"', b'Version="5.0.1"')).docs[0]
    assert relabelled.content_hash == first.content_hash  # the version of the catalog is not the text


# --- what is refused ---------------------------------------------------------------------------------


def test_a_catalog_of_another_schema_is_refused_with_its_namespace_named() -> None:
    old = SAMPLE.replace(b"http://cwe.mitre.org/cwe-8", b"http://cwe.mitre.org/cwe-7")
    with pytest.raises(KbError, match="cwe-7"):
        parse_catalog(old)


def test_a_document_that_is_not_a_weakness_catalog_is_refused() -> None:
    with pytest.raises(KbError, match="Weakness_Catalog"):
        parse_catalog(b'<Something xmlns="http://cwe.mitre.org/cwe-8"/>')


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xx"><!ENTITY y "&x;&x;">]><a>&y;</a>',  # expansion
        b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>',  # external
    ],
)
def test_entity_tricks_are_refused_not_expanded(xml: bytes) -> None:
    with pytest.raises(KbError, match="refused"):
        parse_catalog(xml)


def test_xml_that_does_not_parse_is_an_error_not_an_empty_catalog() -> None:
    with pytest.raises(KbError, match="not XML"):
        parse_catalog(b"<Weakness_Catalog")


def test_a_weakness_without_an_id_is_refused() -> None:
    with pytest.raises(KbError, match="ID"):
        parse_catalog(SAMPLE.replace(b'ID="79" ', b"", 1))


def test_a_weakness_whose_id_is_not_a_number_is_refused() -> None:
    with pytest.raises(KbError, match="ID"):
        parse_catalog(SAMPLE.replace(b'ID="79"', b'ID="abc"', 1))


def test_paragraphs_written_one_after_the_other_stay_apart() -> None:
    end = b"before it is placed in a web page.</Description>"
    tight = SAMPLE.replace(
        end,
        end
        + b"<Extended_Description><xhtml:p>First.</xhtml:p><xhtml:p>Second.</xhtml:p></Extended_Description>",
    )
    xss = next(d for d in parse_catalog(tight).docs if d.id == "CWE-79")
    assert xss.sections["extended_description"] == "First.\n\nSecond."


def test_a_section_the_weakness_does_not_have_is_left_out_not_kept_empty() -> None:
    xss = next(d for d in parse_catalog(SAMPLE).docs if d.id == "CWE-79")
    assert set(xss.sections) == {"description"}


def test_the_content_hash_changes_with_the_relations_too() -> None:
    first = parse_catalog(SAMPLE).docs[0]
    other = parse_catalog(SAMPLE.replace(b'CAPEC_ID="664"', b'CAPEC_ID="665"')).docs[0]
    assert other.content_hash != first.content_hash
