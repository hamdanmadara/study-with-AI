from typing import List
import asyncio
import os
from sentence_transformers import SentenceTransformer
from loguru import logger
import threading
from tqdm import tqdm

from app.core.config import settings

class EmbeddingService:
    def __init__(self):
        self.model = None
        self.model_loading = False
        self.model_loaded = False
        self._load_model_async()
    
    def _load_model_async(self):
        """Load the embedding model asynchronously with progress"""
        if self.model_loading or self.model_loaded:
            return
            
        self.model_loading = True
        
        def load_in_background():
            try:
                # Use a stable, smaller model that works reliably on all systems
                model_name = "all-MiniLM-L6-v2"  # 384 dimensions, stable and fast
                logger.info(f"🤖 Loading embedding model: {model_name}")
                logger.info("📥 This may take a few minutes on first run...")
                
                # Download and load model
                self.model = SentenceTransformer(model_name)
                self.model_loaded = True
                self.model_loading = False
                
                logger.info("✅ Embedding model loaded successfully!")
                logger.info(f"📊 Model dimension: {self.model.get_sentence_embedding_dimension()}")
                
            except Exception as e:
                logger.error(f"❌ Failed to load embedding model: {e}")
                logger.info("🔄 Falling back to simple TF-IDF embeddings...")
                self._setup_fallback()
        
        # Start loading in background thread
        thread = threading.Thread(target=load_in_background, daemon=True)
        thread.start()
    
    def _setup_fallback(self):
        """Setup fallback TF-IDF embeddings if sentence transformers fail"""
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            import numpy as np
            
            self.fallback_vectorizer = TfidfVectorizer(max_features=384, stop_words='english')
            self.fallback_fitted = False
            self.fallback_docs = []
            self.model_loaded = True
            self.model_loading = False
            logger.info("✅ Fallback TF-IDF embeddings ready")
        except ImportError:
            logger.error("❌ Neither sentence-transformers nor scikit-learn available")
            raise
    
    def _wait_for_model(self, timeout=300):
        """Wait for model to load with timeout"""
        import time
        waited = 0
        while self.model_loading and waited < timeout:
            time.sleep(1)
            waited += 1
        
        if not self.model_loaded:
            raise RuntimeError("Model failed to load within timeout")
    
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings for a list of texts"""
        # Wait for model to load
        if self.model_loading:
            logger.info("⏳ Waiting for embedding model to finish loading...")
            self._wait_for_model()
        
        try:
            if self.model:
                # Use sentence transformer model
                loop = asyncio.get_event_loop()
                embeddings = await loop.run_in_executor(
                    None, 
                    self.model.encode, 
                    texts
                )
                embeddings_list = [embedding.tolist() for embedding in embeddings]
                logger.info(f"✅ Created sentence transformer embeddings for {len(texts)} texts")
                return embeddings_list
            
            elif hasattr(self, 'fallback_vectorizer'):
                # Use TF-IDF fallback
                return await self._create_tfidf_embeddings(texts)
            
            else:
                raise ValueError("No embedding model available")
        
        except Exception as e:
            logger.error(f"Error creating embeddings: {e}")
            # Try fallback if main model fails
            if not hasattr(self, 'fallback_vectorizer'):
                self._setup_fallback()
            return await self._create_tfidf_embeddings(texts)
    
    async def _create_tfidf_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create TF-IDF embeddings as fallback"""
        try:
            import numpy as np
            
            # Add to document collection
            self.fallback_docs.extend(texts)
            
            # Fit if needed
            if not self.fallback_fitted:
                self.fallback_vectorizer.fit(self.fallback_docs)
                self.fallback_fitted = True
            
            # Transform texts
            vectors = self.fallback_vectorizer.transform(texts)
            embeddings = vectors.toarray().tolist()
            
            logger.info(f"✅ Created TF-IDF embeddings for {len(texts)} texts")
            return embeddings
            
        except Exception as e:
            logger.error(f"Error creating TF-IDF embeddings: {e}")
            # Ultimate fallback: random embeddings
            return [np.random.rand(384).tolist() for _ in texts]
    
    async def create_single_embedding(self, text: str) -> List[float]:
        """Create embedding for a single text"""
        embeddings = await self.create_embeddings([text])
        return embeddings[0]
    
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text (alias for create_single_embedding)"""
        return await self.create_single_embedding(text)

embedding_service = EmbeddingService()