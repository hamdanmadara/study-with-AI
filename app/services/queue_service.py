import asyncio
from datetime import datetime
from typing import Dict, Optional
from loguru import logger

from app.models.document import Document, ProcessingStatus
from app.core.config import settings


class QueueService:
    def __init__(self, max_workers: int = 2):
        # Separate queues for different file types
        self.pdf_queue = asyncio.Queue()  # PDFs can process concurrently
        self.media_queue = asyncio.Queue()  # Audio/Video must process sequentially
        
        self.max_workers = max_workers
        self.workers_started = False
        self.active_workers = {}
        self.queue_stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_failed': 0,
            'pdf_queue_size': 0,
            'media_queue_size': 0,
            'active_workers': 0
        }
        
        # Media processing lock to ensure one audio/video at a time
        self.media_lock = asyncio.Lock()
    
    async def start_workers(self):
        """Start worker tasks if not already started"""
        if not self.workers_started:
            logger.info(f"Starting specialized queue workers")
            
            # Start PDF workers (can process multiple PDFs concurrently)
            pdf_workers = max(1, self.max_workers - 1)  # Reserve 1 worker for media
            for i in range(pdf_workers):
                worker_name = f"pdf-worker-{i+1}"
                task = asyncio.create_task(self._pdf_worker(worker_name))
                self.active_workers[worker_name] = {
                    'task': task,
                    'current_document': None,
                    'started_at': datetime.now(),
                    'documents_processed': 0,
                    'type': 'pdf'
                }
            
            # Start one dedicated media worker (sequential processing)
            worker_name = "media-worker"
            task = asyncio.create_task(self._media_worker(worker_name))
            self.active_workers[worker_name] = {
                'task': task,
                'current_document': None,
                'started_at': datetime.now(),
                'documents_processed': 0,
                'type': 'media'
            }
            
            self.workers_started = True
            logger.info(f"Queue workers started: {pdf_workers} PDF workers + 1 media worker")
    
    async def add_document_to_queue(self, document: Document, processor_callback):
        """Add document to appropriate processing queue based on file type"""
        # Ensure workers are started
        await self.start_workers()
        
        # Determine if this is a media file (audio/video) or PDF
        file_extension = document.filename.lower().split('.')[-1] if '.' in document.filename else ''
        is_media_file = file_extension in ['mp4', 'avi', 'mov', 'mkv', 'mp3', 'wav', 'flac', 'aac', 'm4a']
        
        if is_media_file:
            await self.media_queue.put((document, processor_callback))
            self.queue_stats['media_queue_size'] = self.media_queue.qsize()
            queue_type = "media"
            queue_size = self.queue_stats['media_queue_size']
        else:
            await self.pdf_queue.put((document, processor_callback))
            self.queue_stats['pdf_queue_size'] = self.pdf_queue.qsize()
            queue_type = "PDF"
            queue_size = self.queue_stats['pdf_queue_size']
        
        self.queue_stats['total_queued'] += 1
        
        logger.info(f"Document {document.id} ({document.filename}) added to {queue_type} queue. Queue size: {queue_size}")
        
        # Update document status to queued
        document.status = ProcessingStatus.PENDING
        document.queued_at = datetime.now()
    
    async def _pdf_worker(self, worker_name: str):
        """Worker that processes PDF documents from the PDF queue"""
        logger.info(f"PDF worker {worker_name} started")
        
        while True:
            try:
                # Get document from PDF queue
                document, processor_callback = await self.pdf_queue.get()
                
                # Update worker status
                self.active_workers[worker_name]['current_document'] = document.id
                self.queue_stats['pdf_queue_size'] = self.pdf_queue.qsize()
                self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                
                logger.info(f"PDF worker {worker_name} starting processing of document {document.id} ({document.filename})")
                
                try:
                    # Process the document using the provided callback
                    await processor_callback(document)
                    
                    # Update stats on success
                    self.queue_stats['total_processed'] += 1
                    self.active_workers[worker_name]['documents_processed'] += 1
                    
                    logger.info(f"PDF worker {worker_name} completed processing document {document.id}")
                    
                except Exception as e:
                    # Update stats on failure
                    self.queue_stats['total_failed'] += 1
                    logger.error(f"PDF worker {worker_name} failed to process document {document.id}: {e}")
                
                finally:
                    # Clear current document and mark task as done
                    self.active_workers[worker_name]['current_document'] = None
                    self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                    self.pdf_queue.task_done()
                    
            except Exception as e:
                logger.error(f"PDF worker {worker_name} encountered error: {e}")
                # Continue processing other documents
    
    async def _media_worker(self, worker_name: str):
        """Worker that processes media files (audio/video) sequentially"""
        logger.info(f"Media worker {worker_name} started - processes ONE media file at a time")
        
        while True:
            try:
                # Get document from media queue
                document, processor_callback = await self.media_queue.get()
                
                # Use lock to ensure only one media file processes at a time
                async with self.media_lock:
                    # Update worker status
                    self.active_workers[worker_name]['current_document'] = document.id
                    self.queue_stats['media_queue_size'] = self.media_queue.qsize()
                    self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                    
                    logger.info(f"Media worker {worker_name} starting EXCLUSIVE processing of {document.id} ({document.filename})")
                    
                    try:
                        # Process the document using the provided callback
                        await processor_callback(document)
                        
                        # Update stats on success
                        self.queue_stats['total_processed'] += 1
                        self.active_workers[worker_name]['documents_processed'] += 1
                        
                        logger.info(f"Media worker {worker_name} completed processing document {document.id}")
                        
                    except Exception as e:
                        # Update stats on failure
                        self.queue_stats['total_failed'] += 1
                        logger.error(f"Media worker {worker_name} failed to process document {document.id}: {e}")
                    
                    finally:
                        # Clear current document and mark task as done
                        self.active_workers[worker_name]['current_document'] = None
                        self.queue_stats['active_workers'] = len([w for w in self.active_workers.values() if w['current_document']])
                        self.media_queue.task_done()
                        
            except Exception as e:
                logger.error(f"Media worker {worker_name} encountered error: {e}")
                # Continue processing other documents
    
    def get_queue_status(self) -> Dict:
        """Get current queue status and statistics"""
        pdf_queue_size = self.pdf_queue.qsize()
        media_queue_size = self.media_queue.qsize()
        total_queue_size = pdf_queue_size + media_queue_size
        active_count = len([w for w in self.active_workers.values() if w['current_document']])
        
        # Update real-time stats
        self.queue_stats['pdf_queue_size'] = pdf_queue_size
        self.queue_stats['media_queue_size'] = media_queue_size
        self.queue_stats['active_workers'] = active_count
        
        status = {
            'total_queue_size': total_queue_size,
            'pdf_queue_size': pdf_queue_size,
            'media_queue_size': media_queue_size,
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
                'is_busy': worker_info['current_document'] is not None,
                'type': worker_info.get('type', 'unknown')
            }
        
        return status
    
    def get_estimated_wait_time(self, file_type: str = 'pdf') -> Optional[int]:
        """Estimate wait time for new documents in minutes"""
        if not self.workers_started:
            return None
        
        # Check file type and estimate accordingly
        if file_type.lower() in ['mp4', 'avi', 'mov', 'mkv', 'mp3', 'wav', 'flac', 'aac', 'm4a']:
            # Media file - uses sequential processing
            queue_size = self.media_queue.qsize()
            media_worker_busy = any(w['current_document'] and w.get('type') == 'media' 
                                  for w in self.active_workers.values())
            
            if not media_worker_busy and queue_size == 0:
                return 0  # Can start immediately
            
            # Media files take longer (10-15 minutes average)
            avg_processing_minutes = 12
            wait_time = queue_size * avg_processing_minutes
            if media_worker_busy:
                wait_time += avg_processing_minutes  # Add time for current processing
                
        else:
            # PDF file - can use concurrent processing
            queue_size = self.pdf_queue.qsize()
            pdf_workers = [w for w in self.active_workers.values() if w.get('type') == 'pdf']
            active_pdf_workers = len([w for w in pdf_workers if w['current_document']])
            available_pdf_workers = len(pdf_workers) - active_pdf_workers
            
            if available_pdf_workers > 0:
                return 0  # Can start immediately
            
            if queue_size == 0:
                return 0
            
            # PDFs process faster (3-5 minutes average)
            avg_processing_minutes = 4
            wait_time = (queue_size * avg_processing_minutes) / len(pdf_workers)
        
        return int(wait_time)
    
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