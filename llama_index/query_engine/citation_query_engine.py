from typing import Any, Dict, List, Optional, Sequence
from langchain.text_splitter import TextSplitter

from llama_index.callbacks.schema import CBEventType
from llama_index.data_structs.node import NodeWithScore, Node
from llama_index.indices.base import BaseGPTIndex
from llama_index.indices.base_retriever import BaseRetriever
from llama_index.callbacks.base import CallbackManager
from llama_index.indices.postprocessor.types import BaseNodePostprocessor
from llama_index.indices.query.base import BaseQueryEngine
from llama_index.indices.query.response_synthesis import ResponseSynthesizer
from llama_index.indices.query.schema import QueryBundle
from llama_index.indices.response.type import ResponseMode
from llama_index.langchain_helpers.text_splitter import TokenTextSplitter
from llama_index.optimization.optimizer import BaseTokenUsageOptimizer
from llama_index.prompts.base import Prompt
from llama_index.prompts.prompts import (
    QuestionAnswerPrompt,
    RefinePrompt,
)
from llama_index.response.schema import RESPONSE_TYPE


CITATION_QA_TEMPLATE = Prompt(
    "Please provide an answer based only on the given sources. "
    "When referencing information from a source, "
    "cite the appropriate source(s) by their numbers. "
    "All answers should provide at least one source. "
    "Do not use a source if it does not help answer a question. "
    "If no sources are helpful, you should say that. "
    "For example:\n"
    "Source 1:\n"
    "The sky is red in the evening and blue in the morning.\n"
    "Source 2:\n"
    "Water is wet when the sky is red.\n"
    "Query: When is water wet?\n"
    "Answer: Water will be wet when the sky is red [2], "
    "which happens in the evening [1].\n"
    "Now it's your turn. Below are several numbered sources of information:"
    "\n------\n"
    "{context_str}"
    "\n------\n"
    "Query: {query_str}\n"
    "Answer: "
)

CITATION_REFINE_TEMPLATE = Prompt(
    "Please provide an answer based only on the given sources. "
    "When referencing information from a source, cite the appropriate source(s). "
    "All answers should provide at least one source. "
    "Do not use a source if it does not help answer a question. "
    "For example:\n"
    "Source 1:\n"
    "The sky is red in the evening and blue in the morning.\n"
    "Source 2:\n"
    "Water is wet when the sky is red.\n"
    "Query: When is water wet?\n"
    "Answer: Water will be wet when the sky is red [2], "
    "which happens in the evening [1].\n"
    "Now it's your turn. "
    "We have provided an existing answer: {existing_answer}"
    "Below are several numbered sources of information. "
    "Use them to refine the existing answer. "
    "If the provided sources are not helpful, you will repeat the existing answer."
    "\nBegin refining!"
    "\n------\n"
    "{context_msg}"
    "\n------\n"
    "Query: {query_str}\n"
    "Answer: "
)

DEFAULT_SOURCE_CHUNK_SIZE = 512
DEFAULT_SOURCE_CHUNK_OVERLAP = 20


class CitaitonQueryEngine(BaseQueryEngine):
    """Citation query engine.

    Args:
        retriever (BaseRetriever): A retriever object.
        response_synthesizer (Optional[ResponseSynthesizer]):
            A ResponseSynthesizer object.
        source_node_size (int):
            Size of source chunks, default=512. Useful for controlling
            granularity of sources.
        source_node_overlap (int): Overlap of source nodes, default=20.
        text_splitter (Optional[TextSplitter]):
            A text splitter for creating citation source nodes. Default is
            a TokenTextSplitter.
        callback_manager (Optional[CallbackManager]): A callback manager.
    """

    def __init__(
        self,
        retriever: BaseRetriever,
        response_synthesizer: Optional[ResponseSynthesizer] = None,
        source_chunk_size: int = DEFAULT_SOURCE_CHUNK_SIZE,
        source_chunk_overlap: int = DEFAULT_SOURCE_CHUNK_OVERLAP,
        text_splitter: Optional[TextSplitter] = None,
        callback_manager: Optional[CallbackManager] = None,
    ) -> None:
        self.text_splitter = text_splitter or TokenTextSplitter(
            chunk_size=source_chunk_size, chunk_overlap=source_chunk_overlap
        )
        self._retriever = retriever
        self._response_synthesizer = (
            response_synthesizer
            or ResponseSynthesizer.from_args(callback_manager=callback_manager)
        )

        super().__init__(callback_manager)

    @classmethod
    def from_args(
        cls,
        index: BaseGPTIndex,
        source_chunk_size: int = DEFAULT_SOURCE_CHUNK_SIZE,
        source_chunk_overlap: int = DEFAULT_SOURCE_CHUNK_OVERLAP,
        text_splitter: Optional[TextSplitter] = None,
        retriever: Optional[BaseRetriever] = None,
        node_postprocessors: Optional[List[BaseNodePostprocessor]] = None,
        verbose: bool = False,
        # response synthesizer args
        response_mode: ResponseMode = ResponseMode.COMPACT,
        text_qa_template: Optional[QuestionAnswerPrompt] = CITATION_QA_TEMPLATE,
        refine_template: Optional[RefinePrompt] = CITATION_REFINE_TEMPLATE,
        response_kwargs: Optional[Dict] = None,
        use_async: bool = False,
        streaming: bool = False,
        optimizer: Optional[BaseTokenUsageOptimizer] = None,
        # class-specific args
        **kwargs: Any,
    ) -> "CitaitonQueryEngine":
        """Initialize a CitaitonQueryEngine object."

        Args:
            retriever (BaseRetriever): A retriever object.
            service_context (Optional[ServiceContext]): A ServiceContext object.
            node_postprocessors (Optional[List[BaseNodePostprocessor]]): A list of
                node postprocessors.
            verbose (bool): Whether to print out debug info.
            response_mode (ResponseMode): A ResponseMode object.
            text_qa_template (Optional[QuestionAnswerPrompt]): A QuestionAnswerPrompt
                object.
            refine_template (Optional[RefinePrompt]): A RefinePrompt object.
            simple_template (Optional[SimpleInputPrompt]): A SimpleInputPrompt object.
            response_kwargs (Optional[Dict]): A dict of response kwargs.
            use_async (bool): Whether to use async.
            streaming (bool): Whether to use streaming.
            optimizer (Optional[BaseTokenUsageOptimizer]): A BaseTokenUsageOptimizer
                object.

        """
        retriever = retriever or index.as_retriever(**kwargs)

        response_synthesizer = ResponseSynthesizer.from_args(
            service_context=index.service_context,
            text_qa_template=text_qa_template,
            refine_template=refine_template,
            response_mode=response_mode,
            response_kwargs=response_kwargs,
            use_async=use_async,
            streaming=streaming,
            optimizer=optimizer,
            node_postprocessors=node_postprocessors,
            verbose=verbose,
        )

        return cls(
            retriever=retriever,
            response_synthesizer=response_synthesizer,
            callback_manager=index.service_context.callback_manager,
            source_chunk_size=source_chunk_size,
            source_chunk_overlap=source_chunk_overlap,
            text_splitter=text_splitter,
        )

    def _create_citation_nodes(self, nodes: List[NodeWithScore]) -> List[NodeWithScore]:
        """Modify retrieved nodes to be granular sources."""
        text_splitter = TokenTextSplitter(chunk_size=256, chunk_overlap=20)

        new_nodes: List[NodeWithScore] = []
        for node in nodes:
            splits = text_splitter.split_text_with_overlaps(node.node.get_text())

            start_offset = 0
            if node.node.node_info:
                start_offset = int(node.node.node_info.get("start", 0))

            for split in splits:
                text = f"Source {len(new_nodes)+1}:\n{split.text_chunk}\n"

                # NOTE currently this does not take into account escaped chars
                num_char_overlap = split.num_char_overlap or 0
                chunk_len = len(split.text_chunk)
                new_node_info = {
                    "start": start_offset - num_char_overlap,
                    "end": start_offset - num_char_overlap + chunk_len,
                }
                start_offset += chunk_len + 1

                new_nodes.append(
                    NodeWithScore(
                        node=Node(
                            text=text,
                            extra_info=node.node.extra_info or {},
                            relationships=node.node.relationships or {},
                            node_info=new_node_info,
                        ),
                        score=node.score,
                    )
                )
        return new_nodes

    def retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return self._retriever.retrieve(query_bundle)

    @property
    def retriever(self) -> BaseRetriever:
        """Get the retriever object."""
        return self._retriever

    def synthesize(
        self,
        query_bundle: QueryBundle,
        nodes: List[NodeWithScore],
        additional_source_nodes: Optional[Sequence[NodeWithScore]] = None,
    ) -> RESPONSE_TYPE:
        nodes = self._create_citation_nodes(nodes)
        response = self._response_synthesizer.synthesize(
            query_bundle=query_bundle,
            nodes=nodes,
            additional_source_nodes=additional_source_nodes,
        )
        return response

    async def asynthesize(
        self,
        query_bundle: QueryBundle,
        nodes: List[NodeWithScore],
        additional_source_nodes: Optional[Sequence[NodeWithScore]] = None,
    ) -> RESPONSE_TYPE:
        nodes = self._create_citation_nodes(nodes)
        return await self._response_synthesizer.asynthesize(
            query_bundle=query_bundle,
            nodes=nodes,
            additional_source_nodes=additional_source_nodes,
        )

    def _query(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
        """Answer a query."""
        query_id = self.callback_manager.on_event_start(
            CBEventType.QUERY, payload={"query_str": query_bundle.query_str}
        )

        retrieve_id = self.callback_manager.on_event_start(CBEventType.RETRIEVE)
        nodes = self._retriever.retrieve(query_bundle)
        nodes = self._create_citation_nodes(nodes)
        self.callback_manager.on_event_end(
            CBEventType.RETRIEVE, payload={"nodes": nodes}, event_id=retrieve_id
        )

        response = self._response_synthesizer.synthesize(
            query_bundle=query_bundle,
            nodes=nodes,
        )

        self.callback_manager.on_event_end(
            CBEventType.QUERY,
            payload={"response": response},
            event_id=query_id,
        )
        return response

    async def _aquery(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
        """Answer a query."""
        query_id = self.callback_manager.on_event_start(
            CBEventType.QUERY, payload={"query_str": query_bundle.query_str}
        )

        retrieve_id = self.callback_manager.on_event_start(CBEventType.RETRIEVE)
        nodes = self._retriever.retrieve(query_bundle)
        nodes = self._create_citation_nodes(nodes)
        self.callback_manager.on_event_end(
            CBEventType.RETRIEVE, payload={"nodes": nodes}, event_id=retrieve_id
        )

        response = await self._response_synthesizer.asynthesize(
            query_bundle=query_bundle,
            nodes=nodes,
        )

        self.callback_manager.on_event_end(
            CBEventType.QUERY,
            payload={"response": response},
            event_id=query_id,
        )
        return response
