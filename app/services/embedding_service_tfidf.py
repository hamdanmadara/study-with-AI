from typing import List
import asyncio
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np
from loguru import logger

class TFIDFEmbeddingService:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=1000, stop_words='english')
        self.is_fitted = False
        self.documents = []
    
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings using TF-IDF"""
        try:
            # Store documents for fitting
            self.documents.extend(texts)
            
            # Fit vectorizer if not done yet
            if not self.is_fitted:
                self.vectorizer.fit(self.documents)
                self.is_fitted = True
            
            # Transform texts to vectors
            vectors = self.vectorizer.transform(texts)
            embeddings = vectors.toarray().tolist()
            
            logger.info(f"Created TF-IDF embeddings for {len(texts)} texts")
            return embeddings
        
        except Exception as e:
            logger.error(f"Error creating TF-IDF embeddings: {e}")
            # Fallback to random embeddings
            return [np.random.rand(1000).tolist() for _ in texts]
    
    async def create_single_embedding(self, text: str) -> List[float]:
        """Create embedding for a single text"""
        embeddings = await self.create_embeddings([text])
        return embeddings[0]

embedding_service = TFIDFEmbeddingService()