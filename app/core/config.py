from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    app_name: str = "Study AI Assistant"
    debug: bool = True
    
    # DeepSeek API
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    
    # File upload settings
    max_file_size: int = 500 * 1024 * 1024  # 500MB (increased limit)
    upload_path: str = "uploads"
    vector_store_path: str = "vector_store"
    supabase_max_file_size: int = 50 * 1024 * 1024  # 50MB Supabase limit
    
    # Embedding model
    embedding_model: str = "BAAI/bge-en-icl"
    
    # Chunking settings
    chunk_size: int = 1000
    chunk_overlap: int = 200
    
    # Vector store
    collection_name: str = "documents"
    
    # File storage settings - Only Supabase
    use_supabase_storage: bool = True
    temp_download_path: str = "temp_downloads"
    
    # Supabase Configuration
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_storage_bucket: str = "documents"
    
    # Authentication settings
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    
    @property
    def database_url(self) -> str:
        """Construct database URL for direct connections if needed"""
        return f"{self.supabase_url}/rest/v1/"
    
    @property
    def is_production(self) -> bool:
        return not self.debug
    
    class Config:
        env_file = ".env"

settings = Settings()