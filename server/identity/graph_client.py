import logging
from datetime import datetime, timezone

from server import config
from server.identity.entity_types import ENTITY_TYPES

logger = logging.getLogger(__name__)


class IdentityGraph:
    """Wrapper around Graphiti for building a temporal user-identity
    knowledge graph from LifeOS session conversations.

    Uses Kuzu as an embedded graph database (like SQLite — no external
    server required).  Gracefully degrades: if Graphiti or Ollama is
    unavailable the system continues with flat-summary memory only.
    """

    def __init__(self):
        self.graphiti = None
        self.available: bool = False
        self._has_episodes: bool = False  # True after first successful ingestion

    async def initialize(self):
        """Set up Kuzu embedded DB + Graphiti with Ollama as LLM backend.
        Sets ``self.available = True`` on success."""
        try:
            from graphiti_core import Graphiti
            from graphiti_core.driver.kuzu_driver import KuzuDriver
            from graphiti_core.llm_client.config import LLMConfig
            from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
            from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
            from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

            ollama_v1 = config.OLLAMA_BASE_URL.rstrip("/") + "/v1"

            llm_config = LLMConfig(
                api_key="ollama",
                model=config.OLLAMA_MODEL,
                small_model=config.OLLAMA_MODEL,
                base_url=ollama_v1,
            )

            llm_client = OpenAIGenericClient(config=llm_config)

            embedder = OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    api_key="ollama",
                    embedding_model="nomic-embed-text",
                    embedding_dim=768,
                    base_url=ollama_v1,
                )
            )

            reranker = OpenAIRerankerClient(
                client=llm_client,
                config=llm_config,
            )

            # Kuzu is embedded — just a file path, no server needed
            kuzu_driver = KuzuDriver(db=str(config.KUZU_DB_PATH))

            self.graphiti = Graphiti(
                graph_driver=kuzu_driver,
                llm_client=llm_client,
                embedder=embedder,
                cross_encoder=reranker,
            )

            await self.graphiti.build_indices_and_constraints()
            self.available = True

            # Check if graph already has data (e.g. from a previous run)
            try:
                edges = await self.graphiti.search(
                    query="test",
                    group_ids=[config.GRAPHITI_GROUP_ID],
                    num_results=1,
                )
                self._has_episodes = True
            except Exception:
                # FTS index doesn't exist yet — graph is empty
                self._has_episodes = False

            logger.info("Identity graph: available (Kuzu @ %s, has_data=%s)",
                        config.KUZU_DB_PATH, self._has_episodes)

        except Exception as e:
            logger.warning("Identity graph unavailable: %s", e)
            self.available = False

    async def ingest_session(
        self,
        session_id: str,
        interactions: list[dict],
        summary: str | None,
    ):
        """Ingest a completed session as a Graphiti episode.

        Builds an episode body from the conversation interactions and
        the generated summary, then feeds it to Graphiti for entity /
        relationship extraction.
        """
        if not self.available or not self.graphiti:
            return

        from graphiti_core.nodes import EpisodeType

        lines = []
        for interaction in interactions:
            transcript = interaction.get("transcript", "")
            if transcript:
                lines.append(f"User: {transcript}")
            response = interaction.get("response", "")
            if response:
                lines.append(f"Assistant: {response}")

        if summary:
            lines.append(f"\nSession summary: {summary}")

        episode_body = "\n".join(lines)
        if not episode_body.strip():
            return

        try:
            await self.graphiti.add_episode(
                name=f"session_{session_id}",
                episode_body=episode_body,
                source=EpisodeType.text,
                source_description="LifeOS voice session conversation",
                reference_time=datetime.now(timezone.utc),
                group_ids=[config.GRAPHITI_GROUP_ID],
                entity_types=ENTITY_TYPES,
            )
            self._has_episodes = True
            logger.info("Identity graph: ingested session %s", session_id[:8])
        except Exception as e:
            logger.warning("Identity graph ingestion failed for %s: %s",
                           session_id[:8], e)

    async def query_user_context(
        self,
        transcript: str,
        limit: int = 10,
    ) -> dict:
        """Search the identity graph for facts relevant to the current
        conversation.  Returns a dict suitable for injection into
        ``ctx.memory["identity"]``.
        """
        if not self.available or not self.graphiti or not self._has_episodes:
            return {"identity_facts": []}

        try:
            edges = await self.graphiti.search(
                query=transcript,
                group_ids=[config.GRAPHITI_GROUP_ID],
                num_results=limit,
            )

            facts = [edge.fact for edge in edges if getattr(edge, "fact", None)]
            logger.debug("Identity graph: %d facts for %r", len(facts),
                         transcript[:60])
            return {"identity_facts": facts}

        except Exception as e:
            logger.warning("Identity graph query failed: %s", e)
            return {"identity_facts": []}

    async def close(self):
        """Shut down the Graphiti connection."""
        if self.graphiti:
            try:
                await self.graphiti.close()
            except Exception as e:
                logger.warning("Identity graph close error: %s", e)
        self.graphiti = None
        self.available = False
