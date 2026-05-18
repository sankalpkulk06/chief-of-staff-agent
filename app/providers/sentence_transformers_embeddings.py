from typing import List


class SentenceTransformersEmbeddingsProvider:
    """Local embeddings using sentence-transformers. No API key needed."""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    def embed_query(self, text: str) -> List[float]:
        embedding = self._model.encode([text], normalize_embeddings=True)
        return embedding[0].tolist()
