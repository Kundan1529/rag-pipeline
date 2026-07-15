from __future__ import annotations

from dataclasses import dataclass, field
from collections import Counter


@dataclass
class ConversationContext:
    """
    Lightweight conversation memory.

    Stores only the information required to improve
    retrieval for follow-up questions.

    No embeddings.
    No LLM.
    No vector database.
    """

    topic: str | None = None

    last_intent: str | None = None

    entities: set[str] = field(default_factory=set)

    concepts: set[str] = field(default_factory=set)

    documents: set[str] = field(default_factory=set)

    citations: list[dict] = field(default_factory=list)
    # ---------------------------------------------------------

    def update(
    self,
    evidence: list[dict],
    intent: str,
):
        """
        Update the conversation context after every
        successful retrieval.
        """

        self.last_intent = intent
        topic_score = Counter()

        for chunk in evidence:

            # Documents
            self.documents.add(chunk["doc_no"])

            # ---------- Entities ----------
            entities = chunk.get("entities", [])

            self.entities.update(entities)

            for entity in entities:
                topic_score[entity] += 3

            # ---------- Concepts ----------
            concepts = chunk.get("concepts", [])

            self.concepts.update(concepts)

            for concept in concepts:
                topic_score[concept] += 2

            # ---------- Keywords ----------
            keywords = chunk.get("keywords", [])

            for keyword in keywords:
                topic_score[keyword] += 1

        # Choose the strongest semantic topic
        if topic_score:
            self.topic = topic_score.most_common(1)[0][0]

    # ---------------------------------------------------------

    def rewrite(
        self,
        query: str,
    ) -> str:
        """
        Expand follow-up questions using
        recent conversation context.
        """

        lower = query.lower()

        followup = any(
            lower.startswith(prefix)
            for prefix in (
                "it",
                "its",
                "they",
                "them",
                "this",
                "that",
                "those",
                "these",
                "he",
                "she",
                "how",
                "why",
                "who",
                "when",
                "where",
                "compare",
                "difference",
                "advantages",
                "limitations",
                "examples",
                "implementation",
            )
        )

        if not followup:
            return query

        if not self.topic:
            return query

        # Don't prepend twice
        if self.topic.lower() in lower:
            return query

        return f"{self.topic} {query}"

    # ---------------------------------------------------------

    def clear(self):
        """
        Reset the conversation context.
        """

        self.topic = None

        self.entities.clear()

        self.concepts.clear()

        self.documents.clear()

        self.citations.clear()