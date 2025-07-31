from typing import List
import re
from app.core.config import settings

class TextChunker:
    def __init__(self, chunk_size: int = None, chunk_overlap: int = None):
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap or settings.chunk_overlap
    
    def chunk_text(self, text: str, content_type: str = "general") -> List[str]:
        """
        Split text into chunks using semantic chunking approach.
        Prioritizes sentence boundaries and paragraph breaks.
        Optimized for RAG applications with different strategies for different content types.
        """
        if not text.strip():
            return []
        
        # Apply content-specific preprocessing
        if content_type == "video":
            text = self._preprocess_video_text(text)
        elif content_type == "pdf":
            text = self._preprocess_pdf_text(text)
        
        # First, try to split by paragraphs
        paragraphs = self._split_by_paragraphs(text)
        chunks = []
        current_chunk = ""
        
        for paragraph in paragraphs:
            # If paragraph is small enough, add to current chunk
            if len(current_chunk) + len(paragraph) <= self.chunk_size:
                current_chunk += paragraph + "\n\n"
            else:
                # Save current chunk if it has content
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                
                # If paragraph is too large, split it by sentences
                if len(paragraph) > self.chunk_size:
                    sentence_chunks = self._chunk_by_sentences(paragraph)
                    chunks.extend(sentence_chunks[:-1])  # Add all but last
                    current_chunk = sentence_chunks[-1] if sentence_chunks else ""
                else:
                    current_chunk = paragraph + "\n\n"
        
        # Add the last chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        # Apply overlap for better RAG context
        if self.chunk_overlap > 0:
            chunks = self._apply_overlap(chunks)
        
        # Filter out chunks that are too small to be meaningful for RAG
        min_chunk_size = 50  # Minimum characters for meaningful context
        filtered_chunks = [chunk for chunk in chunks if len(chunk.strip()) >= min_chunk_size]
        
        return filtered_chunks
    
    def _preprocess_video_text(self, text: str) -> str:
        """Preprocess video transcription text for better chunking"""
        # Remove common transcription artifacts
        text = re.sub(r'\b(um|uh|ah|er|hmm)\b', '', text, flags=re.IGNORECASE)
        
        # Normalize repeated words (common in speech)
        text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
        
        # Add periods at natural speech breaks (helps with chunking)
        text = re.sub(r'(\w+)\s+(and|so|but|then|now|okay|well)\s+', r'\1. \2 ', text, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()
    
    def _preprocess_pdf_text(self, text: str) -> str:
        """Preprocess PDF text for better chunking"""
        # Remove page breaks and headers/footers patterns
        text = re.sub(r'\n\s*\d+\s*\n', '\n', text)  # Page numbers
        text = re.sub(r'\n\s*(Page|Chapter|Section)\s+\d+.*?\n', '\n', text, flags=re.IGNORECASE)
        
        # Fix hyphenated words split across lines
        text = re.sub(r'-\n\s*', '', text)
        
        # Normalize whitespace but preserve paragraph breaks
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        
        return text.strip()
    
    def _split_by_paragraphs(self, text: str) -> List[str]:
        """Split text by paragraphs"""
        # Split by double newlines or more
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]
    
    def _chunk_by_sentences(self, text: str) -> List[str]:
        """Split text by sentences when paragraph is too large"""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= self.chunk_size:
                current_chunk += sentence + " "
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                
                # If single sentence is too long, split by character limit
                if len(sentence) > self.chunk_size:
                    char_chunks = self._chunk_by_characters(sentence)
                    chunks.extend(char_chunks[:-1])
                    current_chunk = char_chunks[-1] if char_chunks else ""
                else:
                    current_chunk = sentence + " "
        
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def _chunk_by_characters(self, text: str) -> List[str]:
        """Split text by character limit as last resort"""
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i:i + self.chunk_size]
            chunks.append(chunk)
        return chunks
    
    def _apply_overlap(self, chunks: List[str]) -> List[str]:
        """Apply overlap between chunks"""
        if len(chunks) <= 1:
            return chunks
        
        overlapped_chunks = [chunks[0]]
        
        for i in range(1, len(chunks)):
            prev_chunk = chunks[i-1]
            current_chunk = chunks[i]
            
            # Get overlap from previous chunk
            overlap_text = prev_chunk[-self.chunk_overlap:] if len(prev_chunk) > self.chunk_overlap else prev_chunk
            
            # Add overlap to current chunk
            overlapped_chunk = overlap_text + " " + current_chunk
            overlapped_chunks.append(overlapped_chunk)
        
        return overlapped_chunks

text_chunker = TextChunker()