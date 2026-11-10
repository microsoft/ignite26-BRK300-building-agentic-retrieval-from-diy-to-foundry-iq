import unittest

from app.naming import (
    KNOWLEDGE_BASE,
    KNOWLEDGE_SOURCES,
    KNOWLEDGE_SOURCES_BY_KEY,
    OBSOLETE_KNOWLEDGE_BASES,
)


class KnowledgeSourceNamingTests(unittest.TestCase):
    def test_defines_six_sources_in_slide_order(self) -> None:
        self.assertEqual(
            [(source.key, source.name) for source in KNOWLEDGE_SOURCES],
            [
                ("origins", "Farms & harvests"),
                ("quality", "Quality checks"),
                ("logistics-retail", "Shipping & sales"),
                ("policies", "Policies"),
                ("documents", "Origin Passport"),
                ("engineering-practices", "Engineering"),
            ],
        )
        self.assertEqual(set(KNOWLEDGE_SOURCES_BY_KEY), {source.key for source in KNOWLEDGE_SOURCES})

    def test_keeps_existing_index_container_and_source_names(self) -> None:
        self.assertEqual(
            [(source.index, source.container, source.knowledge_source) for source in KNOWLEDGE_SOURCES],
            [
                ("cocoarynth-origins-index", "cocoarynth-origins", "cocoarynth-origins-source"),
                ("cocoarynth-quality-index", "cocoarynth-quality", "cocoarynth-quality-source"),
                (
                    "cocoarynth-logistics-retail-index",
                    "cocoarynth-logistics-retail",
                    "cocoarynth-logistics-retail-source",
                ),
                ("cocoarynth-policies-index", "cocoarynth-policies", "cocoarynth-policies-source"),
                ("cocoarynth-documents-index", "knowledge", "cocoarynth-documents-source"),
                (
                    "cocoarynth-engineering-practices-index",
                    "engineering-practices",
                    "cocoarynth-engineering-practices-source",
                ),
            ],
        )

    def test_uses_one_knowledge_base(self) -> None:
        self.assertEqual(KNOWLEDGE_BASE, "cocoarynth-kb-all")
        self.assertEqual(OBSOLETE_KNOWLEDGE_BASES, ("cocoarynth-kb-docs", "cocoarynth-kb-engineering-practices"))


if __name__ == "__main__":
    unittest.main()
