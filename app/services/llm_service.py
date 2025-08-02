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
        # Simple in-memory cache for repeated questions (helps with testing/demos)
        self.qa_cache = {}
        self.summary_cache = {}
        self.max_cache_size = 50
    
    async def answer_question(self, question: str, document_id: str) -> Dict[str, Any]:
        """Answer a question using RAG with document context"""
        try:
            # Check cache first
            cache_key = f"{document_id}:{question.lower().strip()}"
            if cache_key in self.qa_cache:
                logger.info("Returning cached answer")
                return self.qa_cache[cache_key]
            
            # Search for relevant context - reduced for faster response
            similar_chunks = await vector_store_service.search_similar(
                query=question,
                document_id=document_id,
                n_results=3  # Reduced from 5 to 3 for faster processing
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
            
            # Get response from LLM with intelligent formatting prompt
            system_prompt = self._create_intelligent_system_prompt()
            
            # Get response from LLM with timeout
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=500
                ),
                timeout=50.0  # 30 second timeout
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
            
            result = {
                "answer": answer,
                "sources": sources,
                "context_used": True
            }
            
            # Cache the result (with size limit)
            if len(self.qa_cache) >= self.max_cache_size:
                # Remove oldest entry
                oldest_key = next(iter(self.qa_cache))
                del self.qa_cache[oldest_key]
            self.qa_cache[cache_key] = result
            
            return result
            
        except asyncio.TimeoutError:
            logger.error("Question answering timed out")
            return {
                "answer": "The response took too long to generate. Please try asking a simpler question or try again.",
                "sources": [],
                "context_used": False
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
                # Try using search as fallback - reduced for speed
                search_results = await vector_store_service.search_similar(
                    query="summary content overview",
                    document_id=document_id,
                    n_results=6  # Reduced from 10 to 6
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
            
            # Optimize text length based on requested summary length
            max_chars = max_length * 8  # Roughly 8 chars per word as estimate
            
            if len(full_text) > max_chars:
                # Use strategic chunk selection based on summary requirements
                selected_chunks = []
                chunk_count = len(chunks)
                
                # For longer summaries, use more chunks
                if max_length > 600:  # Long summary
                    chunks_to_take = min(8, chunk_count)
                elif max_length > 300:  # Medium summary  
                    chunks_to_take = min(5, chunk_count)
                else:  # Short summary
                    chunks_to_take = min(3, chunk_count)
                
                # Distribute chunks across document
                if chunks_to_take >= chunk_count:
                    selected_chunks = chunks
                else:
                    step = max(1, chunk_count // chunks_to_take)
                    for i in range(0, chunk_count, step):
                        if len(selected_chunks) < chunks_to_take:
                            selected_chunks.append(chunks[i])
                
                text_for_summary = " ".join([chunk['document'] for chunk in selected_chunks])
                
                # Only truncate if still way too long
                if len(text_for_summary) > max_chars * 2:
                    text_for_summary = text_for_summary[:max_chars * 2] + "..."
            else:
                text_for_summary = full_text
            
            # Create summary prompt
            prompt = self._create_summary_prompt(text_for_summary, max_length)
            
            # Create intelligent summary system prompt
            summary_system_prompt = self._create_intelligent_summary_prompt()
            
            # Get response from LLM with longer timeout for summaries
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": summary_system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.2,
                    max_tokens=max_length * 2  # Use user's requested length
                ),
                timeout=60.0  # Increased timeout for summaries
            )
            
            summary = response.choices[0].message.content
            word_count = len(summary.split())
            
            return {
                "summary": summary,
                "word_count": word_count,
                "chunks_used": len(chunks)
            }
            
        except asyncio.TimeoutError:
            logger.error("Summary generation timed out")
            # Provide a basic fallback summary
            try:
                # Create a very basic summary from first chunk
                if chunks and len(chunks) > 0:
                    first_chunk = chunks[0]['document'][:500]
                    basic_summary = f"This document discusses {first_chunk}... (Note: Full summary generation timed out. Please try again with a shorter length.)"
                    return {
                        "summary": basic_summary,
                        "word_count": len(basic_summary.split()),
                        "chunks_used": 1
                    }
            except:
                pass
            
            return {
                "summary": f"Summary generation took too long for the requested {max_length} words. This may be due to document length or network issues. Please try again or consider a shorter length.",
                "word_count": 0,
                "chunks_used": 0
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
                # Try using search as fallback - reduced for speed
                search_results = await vector_store_service.search_similar(
                    query="quiz questions content overview",
                    document_id=document_id,
                    n_results=5  # Reduced from 10 to 5
                )
                if search_results:
                    chunks = search_results
                else:
                    return {
                        "questions": [],
                        "error": "No content found for this document. The document may still be processing.",
                        "total_questions": 0
                    }
            
            # Use entire document for quiz generation (optimized)
            full_text = " ".join([chunk['document'] for chunk in chunks])
            
            # Optimize for large documents but keep all content
            if len(full_text) > 15000:  # If very large, summarize content first
                # Create condensed version while keeping all key information
                text_for_quiz = self._optimize_text_for_quiz(full_text)
            else:
                text_for_quiz = full_text
            
            # Create quiz prompt
            prompt = self._create_quiz_prompt(text_for_quiz, num_questions, difficulty)
            
            # Create enhanced quiz system prompt
            quiz_system_prompt = self._create_quiz_system_prompt(num_questions, difficulty)
            
            # Get response from LLM
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=[
                        {"role": "system", "content": quiz_system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    max_tokens=num_questions * 200  # Dynamic based on question count
                ),
                timeout=60.0  # Increased timeout for quiz generation
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
                "chunks_used": len(chunks)
            }
            
        except asyncio.TimeoutError:
            logger.error("Quiz generation timed out")
            return {
                "questions": [],
                "error": f"Quiz generation took too long for {num_questions} questions. This may be due to document length or network issues. Please try again or consider fewer questions.",
                "total_questions": 0
            }
        except Exception as e:
            logger.error(f"Error generating quiz: {e}")
            return {
                "questions": [],
                "error": f"Error generating quiz: {str(e)}",
                "total_questions": 0
            }
    
    def _optimize_text_for_quiz(self, full_text: str) -> str:
        """
        Optimize large text for quiz generation by creating a condensed version
        that preserves all key information while reducing token usage
        """
        try:
            # If text is not too large, return as-is
            if len(full_text) <= 15000:
                return full_text
            
            # For very large texts, create a structured summary that preserves key information
            # Split into sections and extract key points from each
            section_size = 2000
            sections = []
            
            # Split text into manageable sections
            for i in range(0, len(full_text), section_size):
                section = full_text[i:i + section_size]
                sections.append(section)
            
            # Extract key information from each section
            condensed_sections = []
            for i, section in enumerate(sections):
                # Take first and last parts of each section to preserve context
                if len(section) > 1000:
                    # Keep beginning and end of section with key transitional content
                    beginning = section[:400]
                    end = section[-400:]
                    
                    # Find key sentences in the middle (look for important keywords)
                    middle_part = section[400:-400]
                    key_sentences = []
                    
                    # Look for sentences with important keywords
                    important_keywords = [
                        'definition', 'important', 'key', 'main', 'primary', 'essential',
                        'conclusion', 'result', 'findings', 'overview', 'summary',
                        'first', 'second', 'third', 'finally', 'therefore', 'however',
                        'because', 'since', 'due to', 'as a result', 'consequently'
                    ]
                    
                    sentences = middle_part.split('.')
                    for sentence in sentences[:10]:  # Limit to prevent excessive processing
                        if any(keyword in sentence.lower() for keyword in important_keywords):
                            key_sentences.append(sentence.strip())
                    
                    # Combine beginning, key sentences, and end
                    middle_summary = '. '.join(key_sentences[:3]) if key_sentences else ""
                    condensed_section = f"{beginning} {middle_summary} {end}"
                else:
                    condensed_section = section
                
                condensed_sections.append(condensed_section)
            
            # Combine all condensed sections
            optimized_text = " ".join(condensed_sections)
            
            # Final trim if still too long
            if len(optimized_text) > 12000:
                optimized_text = optimized_text[:12000] + "..."
            
            logger.info(f"Optimized text from {len(full_text)} to {len(optimized_text)} characters for quiz generation")
            return optimized_text
            
        except Exception as e:
            logger.error(f"Error optimizing text for quiz: {e}")
            # Fallback: simple truncation with preservation of beginning and end
            if len(full_text) > 12000:
                beginning = full_text[:6000]
                end = full_text[-6000:]
                return f"{beginning}... [content condensed for quiz generation] ...{end}"
            return full_text
    
    def _select_diverse_chunks(self, chunks: List[Dict], num_questions: int) -> List[Dict]:
        """Select diverse chunks for quiz generation based on question count"""
        if len(chunks) <= num_questions:
            return chunks
        
        # Scale chunk selection based on number of questions user requested
        if num_questions <= 3:
            max_chunks = min(num_questions + 2, len(chunks))
        elif num_questions <= 5:
            max_chunks = min(num_questions + 3, len(chunks))
        elif num_questions <= 10:
            max_chunks = min(num_questions + 5, len(chunks))
        else:  # More than 10 questions
            max_chunks = min(num_questions + 8, len(chunks))
        
        # Distribute evenly across document
        step = max(1, len(chunks) // max_chunks)
        selected = []
        
        for i in range(0, len(chunks), step):
            if len(selected) < max_chunks:
                selected.append(chunks[i])
        
        return selected
    
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
    
    def _create_intelligent_system_prompt(self) -> str:
        """Create intelligent system prompt that lets LLM decide formatting"""
        return """You are a helpful AI assistant that answers questions based on provided context. Always base your answers on the given context.

RESPONSE STYLE:
Write naturally and conversationally, like a knowledgeable human would explain things. Avoid technical formatting symbols.

INTELLIGENT FORMATTING:
Analyze each question and respond appropriately:

- For simple "what is", "who is", "when" questions: Give brief, direct answers (1-2 sentences)
- For "explain", "describe", "how does" questions: Provide detailed explanations with clear structure  
- For "list", "components", "types", "steps" questions: Present items in a clean, readable list format
- For "compare", "contrast" questions: Use natural comparison structure
- For "examples" questions: Provide clear examples in an organized way

FORMATTING EXAMPLES:

Question: "What are the components of LangGraph?"
Good Response:
The components of LangGraph are:
- Tool: A wrapper around specific functionalities (e.g., search or calculator)
- Node: Executes a tool or an LLM (Large Language Model)  
- Edge: Connects nodes, defining the flow of data between them
- State: Stores context and intermediate results across nodes during computation

Question: "What is machine learning?"
Good Response:
Machine learning is a subset of artificial intelligence that enables computers to learn and improve from data without being explicitly programmed.

Question: "Explain how neural networks work"
Good Response:
Neural networks work by mimicking the human brain's structure. They consist of interconnected nodes that process information in layers. During training, the network learns by adjusting connection strengths based on data patterns. Once trained, it can make predictions on new data by passing information through these learned pathways.

IMPORTANT RULES:
- Write like a human, not a robot
- Use simple dashes (-) for lists, not bullets or stars  
- No markdown symbols like ** or ###
- Keep formatting clean and readable
- Match response length to question complexity
- Organize information clearly without technical symbols"""
    
    def _create_qa_prompt(self, question: str, context: str) -> str:
        """Create prompt for Q&A"""
        return f"""Based on the following context, please answer the question. If the answer is not in the context, say so clearly.

Context:
{context}

Question: {question}

Answer the question following the style and format guidelines provided in the system message."""
    
    def _create_intelligent_summary_prompt(self) -> str:
        """Create intelligent summary system prompt"""
        return """You are an expert at creating well-structured, informative summaries. Write naturally like a human would summarize content for another person.

SUMMARY STYLE:
Write conversationally and organize information clearly. Avoid technical formatting symbols and write like you're explaining to a colleague.

INTELLIGENT STRUCTURE:
Choose the best organization based on content:

- For short summaries: Brief overview followed by main points
- For medium summaries: Overview, key points, and conclusion
- For long summaries: Break into natural sections with clear headings

FORMATTING EXAMPLES:

Question: "Summarize this technical document"
Good Summary:
This document covers the basics of machine learning and its applications. The main topics include:

- Supervised learning uses labeled data to train models for prediction tasks
- Unsupervised learning finds patterns in data without predefined outcomes  
- Deep learning employs neural networks to handle complex data like images and text
- Common applications range from recommendation systems to autonomous vehicles

The document emphasizes that choosing the right approach depends on your data type and business goals.

WRITING GUIDELINES:
- Write naturally, like explaining to a friend
- Use simple dashes (-) for lists, not special symbols
- Break content into readable paragraphs  
- Use clear section breaks when needed
- No markdown formatting (**, ###, etc.)
- Focus on what matters most
- Make it easy to scan and understand"""
    
    def _create_summary_prompt(self, text: str, max_length: int) -> str:
        """Create prompt for summary generation"""
        return f"""Analyze and summarize the following text following the structure and formatting guidelines provided:

Text to summarize:
{text}

Create a well-structured summary that captures the essential information in approximately {max_length} words."""
    
    def _create_quiz_system_prompt(self, num_questions: int, difficulty: str) -> str:
        """Create enhanced system prompt for quiz generation"""
        difficulty_guide = {
            "easy": """
DIFFICULTY - EASY:
- Test basic recall and understanding
- Use straightforward language
- Focus on main concepts and facts
- Avoid tricky wording or complex analysis
            """,
            "medium": """
DIFFICULTY - MEDIUM:
- Test understanding and application
- Include some analytical thinking
- Mix factual recall with comprehension
- Use clear but slightly complex scenarios
            """,
            "hard": """
DIFFICULTY - HARD:
- Test analysis, synthesis, and evaluation
- Require critical thinking and inference
- Include complex scenarios and comparisons
- Challenge deeper understanding of concepts
            """
        }
        
        return f"""You are an expert quiz generator that creates well-structured, educational multiple-choice questions.

{difficulty_guide.get(difficulty, difficulty_guide["medium"])}

QUESTION QUALITY:
- Make questions clear and unambiguous
- Ensure only one correct answer
- Create plausible but clearly incorrect distractors
- Avoid "all of the above" or "none of the above" options
- Test different aspects of the content

FORMAT REQUIREMENTS:
- Always respond with valid JSON format
- Generate exactly {num_questions} questions
- Each question must have exactly 4 options
- Correct answer must be indicated by index (0, 1, 2, or 3)

JSON STRUCTURE:
{{
    "questions": [
        {{
            "question": "Clear, specific question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 0
        }}
    ]
}}"""
    
    def _create_quiz_prompt(self, text: str, num_questions: int, difficulty: str) -> str:
        """Create prompt for quiz generation"""
        return f"""Create a {difficulty} difficulty quiz with {num_questions} multiple-choice questions based on the following content:

Content:
{text}

Generate questions that test different aspects of this content. Ensure questions are well-distributed across the material and follow the difficulty and formatting guidelines provided."""

llm_service = LLMService()