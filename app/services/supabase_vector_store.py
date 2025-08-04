"""
Supabase Vector Store Service
Replaces ChromaDB with Supabase pgvector for vector similarity search
"""

from typing import List, Dict, Any, Optional
from loguru import logger
import asyncio

from app.services.supabase_service import supabase_service
from app.services.embedding_service import embedding_service


class SupabaseVectorStore:
    def __init__(self):
        """Initialize Supabase vector store"""
        pass
    
    async def search_similar_documents(
        self, 
        query: str, 
        user_id: str, 
        document_ids: Optional[List[str]] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for similar document chunks using vector similarity
        
        Args:
            query: The search query
            user_id: User ID for scoped search
            document_ids: Optional list of specific document IDs to search within
            limit: Maximum number of results to return
        
        Returns:
            List of similar chunks with metadata
        """
        try:
            # Generate embedding for the query
            query_embedding = await embedding_service.generate_embedding(query)
            
            if document_ids:
                # Search within specific documents
                results = supabase_service.supabase.rpc('get_document_context', {
                    'query_embedding': query_embedding,
                    'document_ids': document_ids,
                    'user_id': user_id,
                    'match_count': limit
                }).execute()
            else:
                # Search across all user's documents
                results = await supabase_service.search_similar_chunks(
                    query_embedding, 
                    user_id, 
                    limit
                )
            
            if results and hasattr(results, 'data'):
                return results.data
            else:
                return results or []
                
        except Exception as e:
            logger.error(f"Vector search failed for user {user_id}: {e}")
            return []
    
    async def get_document_chunks(
        self, 
        document_id: str, 
        user_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all chunks for a specific document
        
        Args:
            document_id: Document ID
            user_id: User ID for scoped access
        
        Returns:
            List of document chunks
        """
        try:
            chunks = await supabase_service.get_document_chunks(document_id, user_id)
            return chunks
            
        except Exception as e:
            logger.error(f"Failed to get chunks for document {document_id}: {e}")
            return []
    
    async def add_document(
        self, 
        document_id: str,
        user_id: str, 
        chunks: List[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add document chunks to vector store
        
        Args:
            document_id: Document ID  
            user_id: User ID
            chunks: List of text chunks
            metadata: Optional metadata
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # This is handled by the document processor now
            # But we keep this method for compatibility
            logger.info(f"Document {document_id} chunks handled by document processor")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add document {document_id} to vector store: {e}")
            return False
    
    async def delete_document(self, document_id: str, user_id: str) -> bool:
        """
        Delete document from vector store
        
        Args:
            document_id: Document ID
            user_id: User ID for scoped access
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Delete chunks from Supabase
            success = await supabase_service.delete_document(document_id, user_id)
            
            if success:
                logger.info(f"Document {document_id} deleted from vector store")
            else:
                logger.warning(f"Document {document_id} deletion may have failed")
                
            return success
            
        except Exception as e:
            logger.error(f"Failed to delete document {document_id} from vector store: {e}")
            return False
    
    # Synchronous wrappers for backward compatibility
    
    def search_similar_documents_sync(
        self, 
        query: str, 
        user_id: str, 
        document_ids: Optional[List[str]] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Synchronous wrapper for search_similar_documents"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.search_similar_documents(query, user_id, document_ids, limit)
        )
    
    def get_document_chunks_sync(
        self, 
        document_id: str, 
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Synchronous wrapper for get_document_chunks"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.get_document_chunks(document_id, user_id)
        )
    
    def add_document_sync(
        self, 
        document_id: str,
        user_id: str, 
        chunks: List[str],
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Synchronous wrapper for add_document"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.add_document(document_id, user_id, chunks, metadata)
        )
    
    def delete_document_sync(self, document_id: str, user_id: str) -> bool:
        """Synchronous wrapper for delete_document"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.delete_document(document_id, user_id)
        )


# Create global Supabase vector store instance
supabase_vector_store = SupabaseVectorStore()