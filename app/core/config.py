from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    app_name: str = "Study AI Assistant"
    debug: bool = True
    
    # DeepSeek API
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    
    # File upload settings
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    upload_path: str = "uploads"
    vector_store_path: str = "vector_store"
    
    # Embedding model
    embedding_model: str = "BAAI/bge-en-icl"
    
    # Chunking settings
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Vector store
    collection_name: str = "documents"
    
    class Config:
        env_file = ".env"

settings = Settings()