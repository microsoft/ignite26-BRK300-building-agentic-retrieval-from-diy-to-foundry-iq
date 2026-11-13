import unittest
from unittest.mock import Mock, patch

from azure.search.documents.indexes.models import SearchIndexKnowledgeSource
from azure.search.documents.knowledgebases.models import KnowledgeRetrievalOutputMode

from app.config import Settings
from app.foundry_iq import FoundryIqService


class FoundryIqServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FoundryIqService.__new__(FoundryIqService)
        self.service.settings = Settings(
            search_endpoint="https://example.search.windows.net",
            openai_endpoint="https://example.openai.azure.com",
            embedding_deployment="text-embedding-3-large",
            embedding_model="text-embedding-3-large",
            chat_deployment="gpt-4.1-mini",
            chat_model="gpt-4.1-mini",
            managed_identity_client_id=None,
        )
        self.service.credential = Mock()
        self.service.index_client = Mock()

    def test_creates_one_source_and_one_knowledge_base(self) -> None:
        result = self.service.create_knowledge_base()

        sources = [
            call.args[0] for call in self.service.index_client.create_or_update_knowledge_source.call_args_list
        ]
        self.assertEqual(len(sources), 1)
        source = sources[0]
        self.assertIsInstance(source, SearchIndexKnowledgeSource)
        self.assertEqual(source.name, "cocoarynth-all-source")
        self.assertEqual(source.search_index_parameters.search_index_name, "cocoarynth-all-index")
        self.assertEqual(source.search_index_parameters.semantic_configuration_name, "semantic-configuration")
        self.assertNotIn("chunk", [field.name for field in source.search_index_parameters.source_data_fields])
        self.assertEqual([field.name for field in source.search_index_parameters.search_fields], ["chunk"])
        self.service.index_client.create_or_update_knowledge_base.assert_called_once()
        knowledge_base = self.service.index_client.create_or_update_knowledge_base.call_args.args[0]
        self.assertEqual(knowledge_base.name, "cocoarynth-kb-all")
        self.assertEqual(knowledge_base.output_mode, KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA)
        self.assertEqual(knowledge_base.retrieval_reasoning_effort.kind, "auto")
        self.assertEqual(
            [reference.name for reference in knowledge_base.knowledge_sources],
            ["cocoarynth-all-source"],
        )
        self.assertIsNone(knowledge_base.retrieval_instructions)
        self.assertEqual(result, {"knowledgeBase": "cocoarynth-kb-all", "knowledgeSources": ["cocoarynth-all-source"]})

    @patch("app.foundry_iq.SearchClient")
    def test_lists_ordered_chunks_for_one_document(self, search_client_type: Mock) -> None:
        search_client_type.return_value.search.return_value = [{"chunk_id": "chunk-1"}]

        result = self.service.list_chunks("cocoarynth-all-index", "pilot.pdf")

        self.assertEqual(result["chunks"], [{"chunk_id": "chunk-1"}])
        arguments = search_client_type.return_value.search.call_args.kwargs
        self.assertEqual(arguments["filter"], "title eq 'pilot.pdf'")
        self.assertEqual(
            arguments["order_by"],
            ["page_number_from asc", "page_number_to asc", "chunk_id asc"],
        )

    @patch("app.foundry_iq.SearchClient")
    def test_lists_chunks_from_the_requested_index(self, search_client_type: Mock) -> None:
        search_client_type.return_value.search.return_value = [{"chunk_id": "hold-chunk-1"}]

        result = self.service.list_chunks("cocoarynth-all-index", "quality-hold.pdf")

        self.assertEqual(result["index"], "cocoarynth-all-index")
        search_client_type.assert_called_once_with(
            self.service.settings.search_endpoint,
            "cocoarynth-all-index",
            self.service.credential,
        )

    @patch("app.foundry_iq.SearchClient")
    def test_runs_hybrid_semantic_search(self, search_client_type: Mock) -> None:
        caption = Mock()
        caption.as_dict.return_value = {"text": "CSV is required.", "highlights": "<em>CSV</em> is required."}
        search_client_type.return_value.search.return_value = [
            {"chunk_id": "chunk-1", "@search.captions": [caption]}
        ]

        result = self.service.search_documents("cocoarynth-all-index", "CSV export")

        self.assertEqual(
            result["matches"],
            [
                {
                    "chunk_id": "chunk-1",
                    "@search.captions": [{"text": "CSV is required.", "highlights": "<em>CSV</em> is required."}],
                }
            ],
        )
        arguments = search_client_type.return_value.search.call_args.kwargs
        self.assertEqual(result["index"], "cocoarynth-all-index")
        self.assertEqual(arguments["search_text"], "CSV export")
        self.assertEqual(arguments["vector_queries"][0].text, "CSV export")
        self.assertEqual(arguments["semantic_configuration_name"], "semantic-configuration")

    @patch("app.foundry_iq.KnowledgeBaseRetrievalClient")
    def test_retrieval_requests_extractive_data_and_activity(self, retrieval_client_type: Mock) -> None:
        retrieval_client_type.return_value.retrieve.return_value.as_dict.return_value = {"activity": []}

        self.service.retrieve("What changed?")

        self.assertEqual(retrieval_client_type.call_args.kwargs["knowledge_base_name"], "cocoarynth-kb-all")
        request = retrieval_client_type.return_value.retrieve.call_args.kwargs["retrieval_request"]
        self.assertEqual(request.output_mode, KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA)
        self.assertTrue(request.include_activity)
        self.assertEqual(
            [source.knowledge_source_name for source in request.knowledge_source_params],
            ["cocoarynth-all-source"],
        )
        for source in request.knowledge_source_params:
            self.assertEqual(source.reranker_threshold, 2.0)
            self.assertTrue(source.include_references)
            self.assertTrue(source.include_reference_source_data)

    def test_redacts_stored_authentication_from_configuration(self) -> None:
        self.service.index_client.get_knowledge_base.return_value.as_dict.return_value = {
            "name": "kb",
            "knowledge_sources": [{"name": "source"}],
        }
        self.service.index_client.get_knowledge_source.return_value.as_dict.return_value = {
            "name": "source",
            "authentication": {"headers": {"Authorization": "Bearer secret"}},
        }

        result = self.service.get_knowledge_base_configuration()

        serialized = str(result)
        self.service.index_client.get_knowledge_base.assert_called_once_with("cocoarynth-kb-all")
        self.assertEqual(
            [call.args[0] for call in self.service.index_client.get_knowledge_source.call_args_list],
            ["cocoarynth-all-source"],
        )
        self.assertEqual(result["knowledgeBase"]["rerankerThreshold"], 2.0)
        self.assertEqual(len(result["knowledgeSources"]), 1)
        self.assertNotIn("mcp", serialized.lower())
        self.assertNotIn("Bearer secret", serialized)
        self.assertNotIn("authentication", serialized)

if __name__ == "__main__":
    unittest.main()