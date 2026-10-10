"""Create a shared index client without silently adopting an unmigrated catalog."""

import os
from .embeddings import EmbeddingProfile, Ollama, model_digest
from .index_state import IndexState
from .qdrant import QdrantIndex


def load_index(settings, catalog, *, writer=False):
    saved = IndexState.saved(catalog)
    if saved:
        state = IndexState.from_saved(catalog)
        if state.collection != settings.workspace:
            raise ValueError("Configured collection differs from catalog binding")
        profile = state.profile
        if (
            profile.model != settings.embedding_model
            or profile.context != settings.embedding_context
            or profile.dimension != settings.embedding_dimension
        ):
            raise ValueError("Configured embedding model differs from catalog binding")
    else:
        with catalog.connect() as db:
            nonempty = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if nonempty or not writer:
            raise ValueError("Initialize a fresh index or migrate the existing catalog first")
        profile = EmbeddingProfile(
            settings.embedding_model,
            model_digest(settings.ollama_url, settings.embedding_model),
            dimension=settings.embedding_dimension,
            context=settings.embedding_context,
        )
        state = IndexState(catalog, settings.workspace, profile)
    embedder = Ollama(settings.ollama_url, profile, settings.embedding_timeout)
    embedder.check_profile()
    index = QdrantIndex(
        settings, catalog, state, embedder, key=os.environ.get("QDRANT_API_KEY", "")
    )
    index.bootstrap(create=writer)
    return index
