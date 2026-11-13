from typing import Any

from azure.core.credentials import TokenCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizerParameters,
    KnowledgeBase,
    KnowledgeBaseAzureOpenAIModel,
    KnowledgeSourceReference,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
)
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import (
    KnowledgeBaseMessage,
    KnowledgeBaseMessageTextContent,
    KnowledgeBaseRetrievalRequest,
    KnowledgeRetrievalAutoReasoningEffort,
    KnowledgeRetrievalOutputMode,
    SearchIndexKnowledgeSourceParams,
)
from azure.search.documents.models import QueryType, VectorizableTextQuery
from azure.storage.blob import BlobServiceClient

from .config import Settings
from .naming import KNOWLEDGE_BASE, KNOWLEDGE_SOURCE

# Keep only chunks the semantic ranker scores as relevant (0-4 scale). With fewer, cleaner chunks,
# Microsoft Retriever 1 can tell when evidence is missing and search again.
RERANKER_THRESHOLD = 2.0
CHUNK_FIELDS = [
    "chunk_id",
    "parent_id",
    "title",
    "blob_path",
    "chunk",
    "page_number_from",
    "page_number_to",
    "image_path",
]
SOURCE_DATA_FIELDS = [field_name for field_name in CHUNK_FIELDS if field_name != "chunk"]


class FoundryIqService:
    def __init__(self, settings: Settings, credential: TokenCredential) -> None:
        self.settings = settings
        self.credential = credential
        self.index_client = SearchIndexClient(settings.search_endpoint, credential)
        self.blob_service = BlobServiceClient(
            account_url=f"https://{settings.storage_account_name}.blob.core.windows.net",
            credential=credential,
        )

    def _model_parameters(self, deployment: str, model: str) -> AzureOpenAIVectorizerParameters:
        return AzureOpenAIVectorizerParameters(
            resource_url=self.settings.openai_endpoint,
            deployment_name=deployment,
            model_name=model,
        )

    def list_documents(self, container_name: str, content_path: str) -> dict[str, Any]:
        container = self.blob_service.get_container_client(container_name)
        try:
            documents = [
                {
                    "name": blob.name,
                    "size": blob.size,
                    "lastModified": blob.last_modified.isoformat() if blob.last_modified else None,
                    "url": f"{content_path}/{blob.name}",
                }
                for blob in container.list_blobs()
            ]
        finally:
            container.close()
        return {"container": container_name, "documents": sorted(documents, key=lambda item: item["name"])}

    def download_document(self, blob_name: str, container_name: str) -> tuple[bytes, str]:
        blob = self.blob_service.get_blob_client(container_name, blob_name)
        try:
            content = blob.download_blob().readall()
            properties = blob.get_blob_properties()
            content_type = properties.content_settings.content_type or "application/octet-stream"
            return content, content_type
        finally:
            blob.close()

    def list_chunks(self, index_name: str, document: str, limit: int = 100) -> dict[str, Any]:
        client = SearchClient(self.settings.search_endpoint, index_name, self.credential)
        try:
            escaped_document = document.replace("'", "''")
            results = client.search(
                "*",
                filter=f"title eq '{escaped_document}'",
                order_by=["page_number_from asc", "page_number_to asc", "chunk_id asc"],
                top=limit,
                select=CHUNK_FIELDS,
            )
            documents = [dict(result) for result in results]
        finally:
            client.close()
        return {"index": index_name, "document": document, "chunks": documents}

    def search_documents(self, index_name: str, query: str, limit: int = 10) -> dict[str, Any]:
        client = SearchClient(self.settings.search_endpoint, index_name, self.credential)
        try:
            results = client.search(
                search_text=query,
                vector_queries=[
                    VectorizableTextQuery(
                        text=query,
                        k_nearest_neighbors=50,
                        fields="text_vector",
                    )
                ],
                query_type=QueryType.SEMANTIC,
                semantic_configuration_name="semantic-configuration",
                query_caption="extractive",
                top=limit,
                select=CHUNK_FIELDS,
            )
            matches = [self._serialize_search_value(dict(result)) for result in results]
        finally:
            client.close()
        return {"index": index_name, "query": query, "matches": matches}

    def create_knowledge_base(self) -> dict[str, Any]:
        spec = KNOWLEDGE_SOURCE
        self.index_client.create_or_update_knowledge_source(
            SearchIndexKnowledgeSource(
                name=spec.knowledge_source,
                description=f"{spec.name}: {spec.description}",
                search_index_parameters=SearchIndexKnowledgeSourceParameters(
                    search_index_name=spec.index,
                    source_data_fields=[
                        SearchIndexFieldReference(name=field_name) for field_name in SOURCE_DATA_FIELDS
                    ],
                    search_fields=[SearchIndexFieldReference(name="chunk")],
                    semantic_configuration_name="semantic-configuration",
                ),
            )
        )

        knowledge_base = KnowledgeBase(
            name=KNOWLEDGE_BASE,
            description=(
                "Cocoarynth chocolate company knowledge: farms and harvests, quality checks, shipping and sales, "
                "and company policies"
            ),
            models=[
                KnowledgeBaseAzureOpenAIModel(
                    azure_open_ai_parameters=self._model_parameters(
                        self.settings.chat_deployment,
                        self.settings.chat_model,
                    )
                )
            ],
            knowledge_sources=[KnowledgeSourceReference(name=spec.knowledge_source)],
            retrieval_reasoning_effort=KnowledgeRetrievalAutoReasoningEffort(),
            output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
        )
        self.index_client.create_or_update_knowledge_base(knowledge_base)
        return {
            "knowledgeBase": KNOWLEDGE_BASE,
            "knowledgeSources": [spec.knowledge_source],
        }

    def get_knowledge_base_configuration(self) -> dict[str, Any]:
        knowledge_base = self.index_client.get_knowledge_base(KNOWLEDGE_BASE).as_dict()
        knowledge_base["rerankerThreshold"] = RERANKER_THRESHOLD
        knowledge_source = self.index_client.get_knowledge_source(KNOWLEDGE_SOURCE.knowledge_source).as_dict()
        return {
            "knowledgeBase": self._redact_secrets(knowledge_base),
            "knowledgeSources": [self._redact_secrets(knowledge_source)],
        }

    def retrieve(self, question: str) -> dict[str, Any]:
        request = KnowledgeBaseRetrievalRequest(
            messages=[
                KnowledgeBaseMessage(
                    role="user",
                    content=[KnowledgeBaseMessageTextContent(text=question)],
                )
            ],
            knowledge_source_params=[
                SearchIndexKnowledgeSourceParams(
                    knowledge_source_name=KNOWLEDGE_SOURCE.knowledge_source,
                    include_references=True,
                    include_reference_source_data=True,
                    reranker_threshold=RERANKER_THRESHOLD,
                )
            ],
            include_activity=True,
            output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
            max_runtime_in_seconds=120,
        )
        client = KnowledgeBaseRetrievalClient(
            endpoint=self.settings.search_endpoint,
            knowledge_base_name=KNOWLEDGE_BASE,
            credential=self.credential,
        )
        try:
            return client.retrieve(retrieval_request=request).as_dict()
        finally:
            client.close()

    @classmethod
    def _serialize_search_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._serialize_search_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serialize_search_value(item) for item in value]
        if hasattr(value, "as_dict"):
            return cls._serialize_search_value(value.as_dict())
        return value

    @classmethod
    def _redact_secrets(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: cls._redact_secrets(item)
                for key, item in value.items()
                if key.lower() not in {"authentication", "headers", "stored_headers", "api_key", "key"}
            }
        if isinstance(value, list):
            return [cls._redact_secrets(item) for item in value]
        return value
