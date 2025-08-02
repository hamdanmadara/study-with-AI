import asyncio
from datetime import datetime
from typing import Dict, Optional
from loguru import logger

from app.models.document import Document, ProcessingStatus
from app.core.config import settings


class QueueService:
    def __init__(self, max_workers: int = 2):
        self.processing_queue = asyncio.Queue()
        self.max_workers = max_workers
        self.workers_started = False
        self.active_workers = {}
        self.queue_stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_failed': 0,
            'queue_size': 0,
            'active_workers': 0
        }
    
    async def start_workers(self):
        """Start worker tasks if not already started"""
        if not self.workers_started:
            logger.info(f"Starting {self.max_workers} queue workers")
            for i in range(self.max_workers):
                worker_name = f"worker-{i+1}"
                task = asyncio.create_task(self._worker(worker_name))
                self.active_workers[worker_name] = {
                    'task': task,
                    'current_document': None,
                    'started_at': datetime.now(),
                    'documents_processed': 0
                }
            self.workers_started = True
            logger.info(f"Queue workers started successfully")
    
    async def add_document_to_queue(self, document: Document, processor_callback):
        """Add document to processing queue"""
        await self.processing_queue.put((document, processor_callback))
        self.queue_stats['total_queued'] += 1
        self.queue_stats['queue_size'] = self.processing_queue.qsize()
        
        logger.info(f"Document {document.id} ({document.filename}) added to queue. Queue size: {self.queue_stats['queue_size']}")
        
        # Update document status to queued
        document.status = ProcessingStatus.PENDING
        document.queued_at = datetime.now()
    
    async def _worker(self, worker_name: str):
        """Worker that processes documents from the queue"""
        logger.info(f"Queue worker {worker_name} started")
        
        while True:
            try:
                # Get document from queue
                document, processor_callback = await self.processing_queue.get()
                
                # Update worker status
                self.active_workers[worker_name]['current_document'] = document.id
                self.queue_stats['queue_size'] = self.processing_queue.qsize()
                self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                
                logger.info(f"Worker {worker_name} starting processing of document {document.id} ({document.filename})")
                
                try:
                    # Process the document using the provided callback
                    await processor_callback(document)
                    
                    # Update stats on success
                    self.queue_stats['total_processed'] += 1
                    self.active_workers[worker_name]['documents_processed'] += 1
                    
                    logger.info(f"Worker {worker_name} completed processing document {document.id}")
                    
                except Exception as e:
                    # Update stats on failure
                    self.queue_stats['total_failed'] += 1
                    logger.error(f"Worker {worker_name} failed to process document {document.id}: {e}")
                
                finally:
                    # Clear current document and mark task as done
                    self.active_workers[worker_name]['current_document'] = None
                    self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                    self.processing_queue.task_done()
                    
            except Exception as e:
                logger.error(f"Queue worker {worker_name} encountered error: {e}")
                # Continue processing other documents
    
    def get_queue_status(self) -> Dict:
        """Get current queue status and statistics"""
        current_queue_size = self.processing_queue.qsize()
        active_count = len([w for w in self.active_workers.values() if w['current_document']])
        
        # Update real-time stats
        self.queue_stats['queue_size'] = current_queue_size
        self.queue_stats['active_workers'] = active_count
        
        status = {
            'queue_size': current_queue_size,
            'active_workers': active_count,
            'max_workers': self.max_workers,
            'total_queued': self.queue_stats['total_queued'],
            'total_processed': self.queue_stats['total_processed'],
            'total_failed': self.queue_stats['total_failed'],
            'workers_started': self.workers_started,
            'workers': {}
        }
        
        # Add individual worker status
        for worker_name, worker_info in self.active_workers.items():
            status['workers'][worker_name] = {
                'current_document': worker_info['current_document'],
                'started_at': worker_info['started_at'].isoformat(),
                'documents_processed': worker_info['documents_processed'],
                'is_busy': worker_info['current_document'] is not None
            }
        
        return status
    
    def get_estimated_wait_time(self) -> Optional[int]:
        """Estimate wait time for new documents in minutes"""
        if not self.workers_started:
            return None
        
        queue_size = self.processing_queue.qsize()
        active_workers = len([w for w in self.active_workers.values() if w['current_document']])
        available_workers = self.max_workers - active_workers
        
        if available_workers > 0:
            return 0  # Can start immediately
        
        if queue_size == 0:
            return 0
        
        # Estimate based on average processing time (assume 5-10 minutes per document)
        avg_processing_minutes = 7  # Conservative estimate
        estimated_minutes = (queue_size * avg_processing_minutes) / self.max_workers
        
        return int(estimated_minutes)
    
    async def stop_workers(self):
        """Stop all workers (for graceful shutdown)"""
        if self.workers_started:
            logger.info("Stopping queue workers...")
            for worker_name, worker_info in self.active_workers.items():
                worker_info['task'].cancel()
            self.workers_started = False
            logger.info("Queue workers stopped")


# Create global queue service instance
queue_service = QueueService(max_workers=getattr(settings, 'max_processing_workers', 2))