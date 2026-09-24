"""Manual test script for tools.py — run to verify search and fetch work."""
import sys
import io

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from tools import web_search, fetch_page
from memory import EntityMemory
from models import Fact
import json


def test_web_search():
    print("=" * 60)
    print("TEST: web_search")
    print("=" * 60)

    resp = web_search("Tanishq jewellery store expansion India 2024")
    if resp.error:
        print(f"ERROR: {resp.error}")
        return False

    print(f"Query: {resp.query}")
    print(f"Results: {len(resp.results)}")
    for i, r in enumerate(resp.results, 1):
        print(f"\n  [{i}] {r.title}")
        print(f"      URL: {r.url}")
        print(f"      Snippet: {r.snippet[:150]}...")
        has_raw = "yes" if r.raw_content else "no"
        print(f"      Raw content: {has_raw}")
        if r.score is not None:
            print(f"      Score: {r.score:.3f}")

    print("\nPASSED" if resp.results else "\nFAILED — no results")
    return bool(resp.results)


def test_web_search_error():
    print("\n" + "=" * 60)
    print("TEST: web_search with empty query")
    print("=" * 60)

    resp = web_search("")
    print(f"Error: {resp.error}")
    print(f"Results: {len(resp.results)}")
    print("PASSED — handled gracefully")
    return True


def test_fetch_page():
    print("\n" + "=" * 60)
    print("TEST: fetch_page")
    print("=" * 60)

    page = fetch_page("https://en.wikipedia.org/wiki/Titan_Company")
    if page.error:
        print(f"ERROR: {page.error}")
        return False

    print(f"URL: {page.url}")
    print(f"Title: {page.title}")
    print(f"Text length: {len(page.text)} chars")
    print(f"First 300 chars:\n{page.text[:300]}...")

    print("\nPASSED" if page.text else "\nFAILED — no text extracted")
    return bool(page.text)


def test_fetch_page_bad_url():
    print("\n" + "=" * 60)
    print("TEST: fetch_page with invalid URL")
    print("=" * 60)

    page = fetch_page("https://thisdomaindoesnotexist999.com/page")
    print(f"Error: {page.error}")
    print("PASSED — handled gracefully" if page.error else "FAILED — no error raised")
    return bool(page.error)


def test_fetch_page_timeout():
    print("\n" + "=" * 60)
    print("TEST: fetch_page with very short timeout")
    print("=" * 60)

    page = fetch_page("https://en.wikipedia.org/wiki/India", timeout=0.001)
    print(f"Error: {page.error}")
    print("PASSED — handled gracefully" if page.error else "FAILED — no error raised")
    return bool(page.error)


def test_memory():
    print("\n" + "=" * 60)
    print("TEST: EntityMemory")
    print("=" * 60)

    mem = EntityMemory("test_knowledge.json")

    mem.add_facts(
        "Tanishq",
        [
            Fact(text="Part of Titan Company (Tata Group)", source="https://example.com/1"),
            Fact(text="Opened ~85 new stores in 2024-2025", source="https://example.com/2"),
        ],
        entity_type="company",
        related=["Titan Company"],
    )

    mem.add_facts(
        "Titan Company",
        [Fact(text="Parent company of Tanishq, part of Tata Group", source="https://example.com/3")],
        entity_type="company",
        related=["Tanishq"],
    )

    record = mem.get("tanishq")
    assert record is not None, "Entity not found"
    assert len(record.facts) == 2, f"Expected 2 facts, got {len(record.facts)}"
    print(f"Entity: {record.name}, facts: {len(record.facts)}, related: {record.related_entities}")

    results = mem.search("titan")
    assert len(results) >= 1, "Search failed"
    print(f"Search 'titan': found {len(results)} entities")

    context = mem.get_context_for_entities(["Tanishq", "Titan Company"])
    print(f"Context string length: {len(context)} chars")
    print(f"Context preview:\n{context[:400]}")

    # duplicate fact should not be added again
    mem.add_facts("Tanishq", [Fact(text="Part of Titan Company (Tata Group)", source="https://example.com/1")])
    record = mem.get("tanishq")
    assert len(record.facts) == 2, f"Duplicate added — got {len(record.facts)} facts"
    print("Duplicate prevention: PASSED")

    import os
    os.remove("test_knowledge.json")
    print("\nPASSED")
    return True


if __name__ == "__main__":
    results = {}
    results["web_search"] = test_web_search()
    results["web_search_error"] = test_web_search_error()
    results["fetch_page"] = test_fetch_page()
    results["fetch_page_bad_url"] = test_fetch_page_bad_url()
    results["fetch_page_timeout"] = test_fetch_page_timeout()
    results["memory"] = test_memory()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")

    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n{passed}/{total} passed")
