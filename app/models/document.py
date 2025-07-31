from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class DocumentType(str, Enum):
    PDF = "pdf"
    VIDEO = "video"

class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class Document(BaseModel):
    id: str
    filename: str
    file_type: DocumentType
    file_path: str
    status: ProcessingStatus
    text_content: Optional[str] = None
    chunk_count: Optional[int] = None
    created_at: datetime
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None

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