from typing import List, Dict, Any, Optional
import json
import asyncio
from openai import AsyncOpenAI
from loguru import logger

from app.core.config import settings
from app.services.vector_store import vector_store_service

class LLMService:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url
        )
    
    async def answer_question(self, question: str, document_id: str) -> Dict[str, Any]:
        """Answer a question using RAG with document context"""
        try:
            # Search for relevant context
            similar_chunks = await vector_store_service.search_similar(
                query=question,
                document_id=document_id,
                n_results=5
            )
            
            if not similar_chunks:
                return {
                    "answer": "I couldn't find relevant information in the document to answer your question.",
                    "sources": []
                }
            
            # Prepare context from chunks
            context = "\n\n".join([chunk['document'] for chunk in similar_chunks])
            
            # Create prompt
            prompt = self._create_qa_prompt(question, context)
            
            # Get response from LLM
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": "You are a helpful AI assistant that answers questions based on the provided context. Always base your answers on the given context and cite relevant parts when possible."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            
            answer = response.choices[0].message.content
            
            # Prepare sources
            sources = [
                {
                    "chunk_id": chunk['id'],
                    "content": chunk['document'][:200] + "..." if len(chunk['document']) > 200 else chunk['document'],
                    "relevance_score": 1 - chunk.get('distance', 0)
                }
                for chunk in similar_chunks
            ]
            
            return {
                "answer": answer,
                "sources": sources,
                "context_used": True
            }
            
        except Exception as e:
            logger.error(f"Error answering question: {e}")
            return {
                "answer": f"I encountered an error while processing your question: {str(e)}",
                "sources": [],
                "context_used": False
            }
    
    async def generate_summary(self, document_id: str, max_length: int = 500) -> Dict[str, Any]:
        """Generate a summary of the document"""
        try:
            # Get all document chunks
            chunks = await vector_store_service.get_document_chunks(document_id)
            
            if not chunks:
                logger.warning(f"No chunks found for document {document_id}, using search fallback")
                # Try using search as fallback
                search_results = await vector_store_service.search_similar(
                    query="summary content overview",
                    document_id=document_id,
                    n_results=10
                )
                if search_results:
                    chunks = search_results
                else:
                    return {
                        "summary": "No content found for this document. The document may still be processing or there was an error during processing.",
                        "word_count": 0,
                        "chunks_used": 0
                    }
            
            # Combine chunks to get full text (with reasonable limit)
            full_text = " ".join([chunk['document'] for chunk in chunks])
            
            # If text is too long, use chunks strategically
            if len(full_text) > 15000:  # Approximate token limit consideration
                # Use first, middle, and last chunks for summary
                selected_chunks = []
                chunk_count = len(chunks)
                
                # First few chunks
                selected_chunks.extend(chunks[:3])
                
                # Middle chunks
                if chunk_count > 6:
                    mid_start = chunk_count // 2 - 1
                    mid_end = mid_start + 2
                    selected_chunks.extend(chunks[mid_start:mid_end])
                
                # Last few chunks
                selected_chunks.extend(chunks[-3:])
                
                # Remove duplicates while preserving order
                seen_ids = set()
                unique_chunks = []
                for chunk in selected_chunks:
                    if chunk['id'] not in seen_ids:
                        unique_chunks.append(chunk)
                        seen_ids.add(chunk['id'])
                
                text_for_summary = " ".join([chunk['document'] for chunk in unique_chunks])
            else:
                text_for_summary = full_text
            
            # Create summary prompt
            prompt = self._create_summary_prompt(text_for_summary, max_length)
            
            # Get response from LLM
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": "You are an expert at creating concise, informative summaries. Focus on the main ideas, key points, and important details."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=max_length * 2  # Allow some buffer
            )
            
            summary = response.choices[0].message.content
            word_count = len(summary.split())
            
            return {
                "summary": summary,
                "word_count": word_count,
                "chunks_used": len(chunks)
            }
            
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return {
                "summary": f"Error generating summary: {str(e)}",
                "word_count": 0,
                "chunks_used": 0
            }
    
    async def generate_quiz(self, document_id: str, num_questions: int = 5, 
                          difficulty: str = "medium") -> Dict[str, Any]:
        """Generate a multiple-choice quiz from the document"""
        try:
            # Get document chunks
            chunks = await vector_store_service.get_document_chunks(document_id)
            
            if not chunks:
                logger.warning(f"No chunks found for document {document_id}, using search fallback")
                # Try using search as fallback
                search_results = await vector_store_service.search_similar(
                    query="quiz questions content overview",
                    document_id=document_id,
                    n_results=10
                )
                if search_results:
                    chunks = search_results
                else:
                    return {
                        "questions": [],
                        "error": "No content found for this document. The document may still be processing.",
                        "total_questions": 0
                    }
            
            # Select diverse chunks for quiz generation
            selected_chunks = self._select_diverse_chunks(chunks, num_questions)
            text_for_quiz = " ".join([chunk['document'] for chunk in selected_chunks])
            
            # Create quiz prompt
            prompt = self._create_quiz_prompt(text_for_quiz, num_questions, difficulty)
            
            # Get response from LLM
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": "You are an expert quiz generator. Create multiple-choice questions that test understanding of the provided content. Always respond with valid JSON format."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            # Parse the response
            quiz_content = response.choices[0].message.content
            
            # Try to extract JSON from the response
            quiz_json = self._extract_json_from_response(quiz_content)
            
            if not quiz_json or 'questions' not in quiz_json:
                raise ValueError("Invalid quiz format received from LLM")
            
            # Validate and format questions
            formatted_questions = []
            for q in quiz_json['questions'][:num_questions]:
                if self._validate_quiz_question(q):
                    formatted_questions.append({
                        "question": q['question'],
                        "options": q['options'],
                        "correct_answer": q['correct_answer']
                    })
            
            return {
                "questions": formatted_questions,
                "total_questions": len(formatted_questions),
                "difficulty": difficulty,
                "chunks_used": len(selected_chunks)
            }
            
        except Exception as e:
            logger.error(f"Error generating quiz: {e}")
            return {
                "questions": [],
                "error": f"Error generating quiz: {str(e)}",
                "total_questions": 0
            }
    
    def _select_diverse_chunks(self, chunks: List[Dict], num_questions: int) -> List[Dict]:
        """Select diverse chunks for quiz generation"""
        if len(chunks) <= num_questions:
            return chunks
        
        # Select chunks evenly distributed across the document
        step = len(chunks) // num_questions
        selected = []
        
        for i in range(0, len(chunks), step):
            if len(selected) < num_questions * 2:  # Get a bit more for better coverage
                selected.append(chunks[i])
        
        return selected[:num_questions * 2]  # Return up to 2x questions for better content
    
    def _extract_json_from_response(self, response: str) -> Optional[Dict]:
        """Extract JSON from LLM response"""
        try:
            # Try to find JSON in the response
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            
            if start_idx != -1 and end_idx != 0:
                json_str = response[start_idx:end_idx]
                return json.loads(json_str)
            
            # If direct JSON parsing fails, try the whole response
            return json.loads(response)
            
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON from LLM response")
            return None
    
    def _validate_quiz_question(self, question: Dict) -> bool:
        """Validate a quiz question structure"""
        required_fields = ['question', 'options', 'correct_answer']
        
        if not all(field in question for field in required_fields):
            return False
        
        if not isinstance(question['options'], list) or len(question['options']) < 2:
            return False
        
        if not isinstance(question['correct_answer'], int) or question['correct_answer'] < 0 or question['correct_answer'] >= len(question['options']):
            return False
        
        return True
    
    def _create_qa_prompt(self, question: str, context: str) -> str:
        """Create prompt for Q&A"""
        return f"""Based on the following context, please answer the question. If the answer is not in the context, say so clearly.

Context:
{context}

Question: {question}

Please provide a comprehensive answer based on the context provided."""
    
    def _create_summary_prompt(self, text: str, max_length: int) -> str:
        """Create prompt for summary generation"""
        return f"""Please create a comprehensive summary of the following text. The summary should be approximately {max_length} words and capture the main ideas, key points, and important details.

Text to summarize:
{text}

Summary:"""
    
    def _create_quiz_prompt(self, text: str, num_questions: int, difficulty: str) -> str:
        """Create prompt for quiz generation"""
        return f"""Based on the following text, create a multiple-choice quiz with {num_questions} questions at {difficulty} difficulty level.

Text:
{text}

Please generate exactly {num_questions} multiple-choice questions. For each question:
1. Create a clear, specific question
2. Provide 4 answer options (A, B, C, D)
3. Indicate the correct answer by its index (0, 1, 2, or 3)

Respond with a JSON format like this:
{{
    "questions": [
        {{
            "question": "What is the main topic discussed?",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0
        }}
    ]
}}"""

llm_service = LLMService()