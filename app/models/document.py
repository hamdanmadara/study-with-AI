from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class DocumentType(str, Enum):
    PDF = "pdf"
    VIDEO = "video"
    AUDIO = "audio"

class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class Document(BaseModel):
    id: str
    filename: str
    file_type: DocumentType
    file_path: str  # For backward compatibility, now stores R2 object key
    status: ProcessingStatus
    text_content: Optional[str] = None
    chunk_count: Optional[int] = None
    created_at: datetime
    queued_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    # R2 Storage fields
    r2_object_key: Optional[str] = None  # R2 object key for the file
    r2_bucket_name: Optional[str] = None  # R2 bucket name
    file_url: Optional[str] = None  # R2 file URL
    file_size: Optional[int] = None  # File size in bytes
    storage_type: str = "r2"  # Storage type: "local" or "r2"
    
    # Progress tracking fields
    total_duration: Optional[float] = None  # Total video duration in seconds
    processed_duration: Optional[float] = None  # Processed duration in seconds
    total_segments: Optional[int] = None  # Total number of segments
    processed_segments: Optional[int] = None  # Number of processed segments
    current_segment: Optional[int] = None  # Currently processing segment number
    estimated_completion: Optional[datetime] = None  # Estimated completion time
    processing_started_at: Optional[datetime] = None  # When processing actually started

class QuestionRequest(BaseModel):
    document_id: str
    question: str

class SummaryRequest(BaseModel):
    document_id: str
    max_length: Optional[int] = 500

class QuizRequest(BaseModel):
    document_id: str
    num_questions: int = 5
    difficulty: Optional[str] = "medium"

class QuizQuestion(BaseModel):
    question: str
    options: List[str]
    correct_answer: int

class QuizResponse(BaseModel):
    questions: List[QuizQuestion]
    document_title: str