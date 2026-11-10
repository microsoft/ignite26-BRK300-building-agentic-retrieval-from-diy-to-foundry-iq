from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeSourceSpec:
    key: str
    name: str
    index: str
    container: str
    knowledge_source: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "index": self.index,
            "knowledgeSource": self.knowledge_source,
        }


KNOWLEDGE_SOURCES = (
    KnowledgeSourceSpec(
        key="origins",
        name="Farms & harvests",
        index="cocoarynth-origins-index",
        container="cocoarynth-origins",
        knowledge_source="cocoarynth-origins-source",
        description=(
            "Farm and supplier records, partly in Spanish: origin profiles, farm inspections, collection "
            "tickets, fermentation logs, organic certification, harvest reports, and bean prices."
        ),
    ),
    KnowledgeSourceSpec(
        key="quality",
        name="Quality checks",
        index="cocoarynth-quality-index",
        container="cocoarynth-quality",
        knowledge_source="cocoarynth-quality-source",
        description=(
            "Factory records: bean receiving and moisture tests, holds and releases, lab certificates, "
            "tasting panels, nonconformance reports, batch records, and retention samples."
        ),
    ),
    KnowledgeSourceSpec(
        key="logistics-retail",
        name="Shipping & sales",
        index="cocoarynth-logistics-retail-index",
        container="cocoarynth-logistics-retail",
        knowledge_source="cocoarynth-logistics-retail-source",
        description=(
            "Shipping and sales records: bills of lading, depot handling logs, temperature loggers, carrier "
            "incidents, retailer emails and agreements, launch plans, price lists, and sales reviews."
        ),
    ),
    KnowledgeSourceSpec(
        key="policies",
        name="Policies",
        index="cocoarynth-policies-index",
        container="cocoarynth-policies",
        knowledge_source="cocoarynth-policies-source",
        description=(
            "Company policies and reference: label claims, data sharing, storage and handling, sourcing "
            "principles, bean quality specification, product launch process, glossary, and org chart."
        ),
    ),
    KnowledgeSourceSpec(
        key="documents",
        name="Origin Passport",
        index="cocoarynth-documents-index",
        container="knowledge",
        knowledge_source="cocoarynth-documents-source",
        description=(
            "The Origin Passport software project: product requirements, architecture decisions, its "
            "traceability data-sharing policy, and support runbooks."
        ),
    ),
    KnowledgeSourceSpec(
        key="engineering-practices",
        name="Engineering",
        index="cocoarynth-engineering-practices-index",
        container="engineering-practices",
        knowledge_source="cocoarynth-engineering-practices-source",
        description=(
            "Software engineering guidance for API design, React and TypeScript, frontend accessibility, "
            "and engineering culture."
        ),
    ),
)
KNOWLEDGE_SOURCES_BY_KEY = {source.key: source for source in KNOWLEDGE_SOURCES}
# All Cocoarynth company records in one index, for the strategies that search a single index.
COMBINED_INDEX = "cocoarynth-all-index"
COMBINED_INDEX_SOURCE_KEYS = ("origins", "quality", "logistics-retail", "policies")
KNOWLEDGE_BASE = "cocoarynth-kb-all"
OBSOLETE_KNOWLEDGE_BASES = ("cocoarynth-kb-docs", "cocoarynth-kb-engineering-practices")
