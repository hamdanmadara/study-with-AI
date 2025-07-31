import asyncio
import os
import time
from loguru import logger
from pathlib import Path

async def test_pdf_processing_auto():
    """Automatically test PDF processing when server starts"""
    try:
        # Look for any PDF file in the directory
        pdf_files = list(Path(".").glob("*.pdf"))
        if not pdf_files:
            logger.info("📄 No PDF files found for auto-testing")
            return True
        
        pdf_file = pdf_files[0]
        logger.info(f"🧪 Auto-testing with PDF: {pdf_file}")
        
        # Import services
        from app.services.text_extraction import text_extraction_service
        from app.services.embedding_service import embedding_service
        from app.services.vector_store import vector_store_service
        
        # Test PDF extraction
        logger.info("📄 Testing PDF text extraction...")
        text = await text_extraction_service.extract_text_from_pdf(str(pdf_file))
        logger.info(f"✅ Extracted {len(text)} characters from PDF")
        
        # Test embeddings
        logger.info("🤖 Testing embedding creation...")
        test_text = text[:500] if len(text) > 500 else text
        embedding = await embedding_service.create_single_embedding(test_text)
        logger.info(f"✅ Created embedding with {len(embedding)} dimensions")
        
        # Test vector store
        logger.info("🗄️ Testing vector store...")
        chunk_count = await vector_store_service.add_document(
            document_id="startup_test",
            text=text,
            metadata={"filename": str(pdf_file), "file_type": "pdf", "test": True}
        )
        logger.info(f"✅ Added {chunk_count} chunks to vector store")
        
        # Test search
        logger.info("🔍 Testing similarity search...")
        results = await vector_store_service.search_similar(
            query="What is this document about?",
            document_id="startup_test",
            n_results=3
        )
        logger.info(f"✅ Found {len(results)} relevant chunks")
        
        logger.info("🎉 Auto-test completed successfully!")
        logger.info("💻 Ready to accept uploads at: http://localhost:8000/app")
        return True
        
    except Exception as e:
        logger.error(f"❌ Auto-test failed: {e}")
        logger.info("⚠️ App will still work, but check the logs above")
        return False

def run_startup_test():
    """Run startup test in background"""
    try:
        asyncio.run(test_pdf_processing_auto())
    except Exception as e:
        logger.error(f"Error in startup test: {e}")