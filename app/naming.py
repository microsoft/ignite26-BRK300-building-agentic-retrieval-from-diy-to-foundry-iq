from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeSourceSpec:
    name: str
    index: str
    container: str
    knowledge_source: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "index": self.index,
            "knowledgeSource": self.knowledge_source,
        }


# Every Cocoarynth company record, in one Blob container and one search index. The DIY agents search this index
# directly; the knowledge base reaches it through one knowledge source.
KNOWLEDGE_SOURCE = KnowledgeSourceSpec(
    name="Cocoarynth records",
    index="cocoarynth-all-index",
    container="cocoarynth",
    knowledge_source="cocoarynth-all-source",
    description=(
        "Every Cocoarynth company record: farm and supplier records (partly in Spanish), factory quality "
        "records, shipping and sales records, and company policies."
    ),
)
KNOWLEDGE_BASE = "cocoarynth-kb-all"
