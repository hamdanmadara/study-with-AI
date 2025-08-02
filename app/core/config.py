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
    
    # Cloudflare R2 Configuration (support both naming conventions)
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket_name: Optional[str] = None
    r2_endpoint_url: Optional[str] = None
    r2_region: str = "auto"
    
    # Alternative R2 naming (for existing setup)
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    endpoint_url: Optional[str] = None
    account_id: Optional[str] = None
    
    # File storage settings
    use_r2_storage: bool = True
    temp_download_path: str = "temp_downloads"
    
    @property
    def effective_r2_access_key_id(self) -> str:
        return self.r2_access_key_id or self.access_key_id or ""
    
    @property
    def effective_r2_secret_access_key(self) -> str:
        return self.r2_secret_access_key or self.secret_access_key or ""
    
    @property
    def effective_r2_endpoint_url(self) -> str:
        return self.r2_endpoint_url or self.endpoint_url or ""
    
    @property
    def effective_r2_bucket_name(self) -> str:
        # Use the existing bucket name
        return self.r2_bucket_name or "study-content"
    
    class Config:
        env_file = ".env"

settings = Settings()