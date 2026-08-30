# Architecture Notes

The system uses a multi-project data model from day one. Each chunk stored
in Chroma is tagged with a project_id so retrieval can be scoped to a
single project or run across all projects for cross-project questions.

Backend: FastAPI. Vector store: Chroma (local, persistent). Metadata: SQLite.
Embeddings: sentence-transformers (all-MiniLM-L6-v2), local and free.
Generation: Claude API.
