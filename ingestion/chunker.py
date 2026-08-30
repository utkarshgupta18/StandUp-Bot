"""Simple character-based text chunker with overlap.

Good enough for markdown notes and commit diffs; can be swapped for a
token-aware or structure-aware (headings, functions) chunker later without
touching the rest of the pipeline.
"""
from app.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS


def chunk_text(text: str, size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks
