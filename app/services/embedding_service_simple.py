from typing import List
import asyncio
from openai import AsyncOpenAI
from loguru import logger

from app.core.config import settings

class SimpleEmbeddingService:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
    
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings using DeepSeek/OpenAI API"""
        try:
            # Use OpenAI-compatible embedding API
            embeddings = []
            for text in texts:
                # For now, create simple mock embeddings
                # This is just for testing - replace with actual API call
                mock_embedding = [0.1] * 1536  # Standard OpenAI embedding size
                embeddings.append(mock_embedding)
            
            logger.info(f"Created mock embeddings for {len(texts)} texts")
            return embeddings
        
        except Exception as e:
            logger.error(f"Error creating embeddings: {e}")
            # Fallback to simple mock embeddings
            return [[0.1] * 1536 for _ in texts]
    
    async def create_single_embedding(self, text: str) -> List[float]:
        """Create embedding for a single text"""
        embeddings = await self.create_embeddings([text])
        return embeddings[0]

embedding_service = SimpleEmbeddingService()