from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.models.document import QuestionRequest, SummaryRequest, QuizRequest
from app.services.llm_service import llm_service
from app.services.document_processor import document_processor
from app.models.document import ProcessingStatus

router = APIRouter(prefix="/features", tags=["features"])

@router.post("/question")
async def ask_question(request: QuestionRequest):
    """Ask a question about a document"""
    try:
        # Check if document exists and is processed
        document = document_processor.get_document_status(request.document_id)
        
        if document.status != ProcessingStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail=f"Document is not ready. Current status: {document.status.value}"
            )
        
        # Additional check for error messages in document
        if hasattr(document, 'error_message') and document.error_message:
            raise HTTPException(
                status_code=400,
                detail=f"Document processing failed: {document.error_message}"
            )
        
        # Get answer from LLM service
        result = await llm_service.answer_question(
            question=request.question,
            document_id=request.document_id
        )
        
        return {
            "document_id": request.document_id,
            "question": request.question,
            "answer": result["answer"],
            "sources": result["sources"],
            "context_used": result["context_used"]
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to answer question: {str(e)}")

@router.post("/summary")
async def generate_summary(request: SummaryRequest):
    """Generate a summary of a document"""
    try:
        # Check if document exists and is processed
        document = document_processor.get_document_status(request.document_id)
        
        if document.status != ProcessingStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail=f"Document is not ready. Current status: {document.status.value}"
            )
        
        # Additional check for error messages in document
        if hasattr(document, 'error_message') and document.error_message:
            raise HTTPException(
                status_code=400,
                detail=f"Document processing failed: {document.error_message}"
            )
        
        # Generate summary
        result = await llm_service.generate_summary(
            document_id=request.document_id,
            max_length=request.max_length
        )
        
        return {
            "document_id": request.document_id,
            "document_name": document.filename,
            "summary": result["summary"],
            "word_count": result["word_count"],
            "chunks_used": result.get("chunks_used", 0),
            "max_length_requested": request.max_length
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")

@router.post("/quiz")
async def generate_quiz(request: QuizRequest):
    """Generate a quiz from a document"""
    try:
        # Check if document exists and is processed
        document = document_processor.get_document_status(request.document_id)
        
        if document.status != ProcessingStatus.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail=f"Document is not ready. Current status: {document.status.value}"
            )
        
        # Additional check for error messages in document
        if hasattr(document, 'error_message') and document.error_message:
            raise HTTPException(
                status_code=400,
                detail=f"Document processing failed: {document.error_message}"
            )
        
        # Generate quiz
        result = await llm_service.generate_quiz(
            document_id=request.document_id,
            num_questions=request.num_questions,
            difficulty=request.difficulty
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return {
            "document_id": request.document_id,
            "document_name": document.filename,
            "questions": result["questions"],
            "total_questions": result["total_questions"],
            "difficulty": result["difficulty"],
            "chunks_used": result.get("chunks_used", 0)
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate quiz: {str(e)}")

@router.get("/available/{document_id}")
async def get_available_features(document_id: str):
    """Get available features for a document based on its processing status"""
    try:
        document = document_processor.get_document_status(document_id)
        
        features = {
            "question_answer": document.status == ProcessingStatus.COMPLETED,
            "summary": document.status == ProcessingStatus.COMPLETED,
            "quiz": document.status == ProcessingStatus.COMPLETED
        }
        
        return {
            "document_id": document_id,
            "document_name": document.filename,
            "status": document.status.value,
            "available_features": features,
            "chunk_count": document.chunk_count,
            "processed_at": document.processed_at.isoformat() if document.processed_at else None
        }
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get features: {str(e)}")