"""
memory/vector_store.py — ChromaDB integration for semantic memory.

Stores past successful task descriptions and their outcomes to provide
context for future planning.
"""
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Module-level client initialized on first use
_chroma_client = None
_collection = None


def _get_collection() -> Any:
    """Initialize and return the ChromaDB collection lazily."""
    global _chroma_client, _collection
    
    if _collection is not None:
        return _collection

    persist_dir = Path(config.CHROMA_PERSIST_PATH)
    persist_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Initializing ChromaDB at %s", persist_dir)
    
    # Disable telemetry and use a more stable settings config
    settings = Settings(
        anonymized_telemetry=False,
        is_persistent=True,
    )
    
    _chroma_client = chromadb.PersistentClient(
        path=str(persist_dir),
        settings=settings
    )

    # Use NVIDIA NIM for embeddings to save local memory
    # We use the OpenAI-compatible endpoint for embeddings
    emb_fn = embedding_functions.OpenAIEmbeddingFunction(
        api_key=config.NVIDIA_NIM_API_KEY,
        api_base=config.NIM_BASE_URL,
        model_name="nvidia/nv-embedqa-e5-v5" # High-performance embedding model on NIM
    )

    _collection = _chroma_client.get_or_create_collection(
        name="task_memory",
        embedding_function=emb_fn,
        metadata={"hnsw:space": "cosine"}
    )
    
    return _collection


async def embed_task(task: str, summary: str, user_id: str = "") -> None:
    """
    Store a completed task in the vector database.
    
    Args:
        task: The original user request.
        summary: What the agent actually did to fulfill it.
        user_id: Optional user identifier for isolation.
    """
    try:
        import uuid
        collection = _get_collection()
        
        doc_id = str(uuid.uuid4())
        
        # We embed the task description so we can search by similar tasks later
        collection.add(
            documents=[task],
            metadatas=[{"summary": summary, "user_id": user_id}],
            ids=[doc_id]
        )
        logger.debug("Embedded task %s into vector store", doc_id)
    except Exception as exc:
        logger.error("Failed to embed task: %s", exc)


async def search_similar_tasks(query: str, user_id: str = "", k: int = 3) -> str:
    """
    Search for similar past tasks to provide context to the planner.
    
    Args:
        query: The new user request.
        user_id: Optional user identifier for isolation.
        k: Number of results to return.
        
    Returns:
        Formatted string of past similar tasks and their summaries.
    """
    try:
        collection = _get_collection()
        
        where_clause = {"user_id": user_id} if user_id else None
        
        results = collection.query(
            query_texts=[query],
            n_results=k,
            where=where_clause
        )
        
        if not results["documents"] or not results["documents"][0]:
            return ""
            
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        
        lines = []
        for doc, meta in zip(docs, metas):
            lines.append(f"Past Task: {doc}\nAction Taken: {meta.get('summary', '')}")
            
        return "\n\n".join(lines)
    except Exception as exc:
        logger.error("Failed to search similar tasks: %s", exc)
        return ""
