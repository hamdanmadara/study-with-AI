import os
import tempfile
from typing import Optional
from pathlib import Path
import asyncio

import pypdf
try:
    from moviepy.video.io.VideoFileClip import VideoFileClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

from loguru import logger

from app.core.config import settings

class TextExtractionService:
    def __init__(self):
        self.whisper_model = None
        self._load_whisper_model()
    
    def _load_whisper_model(self):
        """Load Whisper model on startup for better performance"""
        if not WHISPER_AVAILABLE:
            logger.warning("OpenAI Whisper not available. Video transcription disabled.")
            return
            
        try:
            # Use 'base' model for good balance of speed and accuracy
            logger.info("Loading Whisper 'base' model...")
            self.whisper_model = whisper.load_model("base")
            logger.info("Whisper model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            self.whisper_model = None
    
    async def extract_text_from_pdf(self, file_path: str) -> str:
        """Extract text from PDF file"""
        try:
            text = ""
            with open(file_path, 'rb') as file:
                pdf_reader = pypdf.PdfReader(file)
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            
            if not text.strip():
                raise ValueError("No text content found in PDF")
            
            logger.info(f"Extracted {len(text)} characters from PDF")
            return text.strip()
        
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")
    
    async def extract_text_from_video(self, file_path: str) -> str:
        """Extract text from video using MoviePy + OpenAI Whisper"""
        if not MOVIEPY_AVAILABLE:
            raise ValueError("MoviePy not available. Please install moviepy to extract text from videos.")
            
        if not WHISPER_AVAILABLE or not self.whisper_model:
            raise ValueError("OpenAI Whisper not available. Please install openai-whisper for video transcription.")
            
        try:
            logger.info(f"Starting optimized video processing for: {file_path}")
            
            # Create temporary audio file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio_path = temp_audio.name
            
            try:
                # Optimize video for transcription
                await self._optimize_for_transcription(file_path, temp_audio_path)
                
                # Transcribe audio using Whisper
                logger.info("Starting Whisper transcription...")
                text = await self._transcribe_with_whisper(temp_audio_path)
                
                if not text.strip():
                    text = "No clear speech detected in this video. The video may contain background music, noise, or speech that was not clearly audible."
                
                logger.info(f"Video processing completed. Extracted {len(text)} characters")
                return text.strip()
            
            finally:
                # Clean up temporary audio file
                if os.path.exists(temp_audio_path):
                    os.unlink(temp_audio_path)
        
        except Exception as e:
            logger.error(f"Error extracting text from video: {e}")
            raise ValueError(f"Failed to extract text from video: {str(e)}")
    
    async def _optimize_for_transcription(self, video_path: str, output_path: str):
        """Optimize video for transcription following best practices"""
        try:
            logger.info("Optimizing video for transcription...")
            
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._optimize_video_sync, video_path, output_path)
            
        except Exception as e:
            logger.error(f"Error optimizing video: {e}")
            raise
    
    def _optimize_video_sync(self, video_path: str, output_path: str):
        """Synchronous video optimization"""
        video = VideoFileClip(video_path)
        
        try:
            # Limit to first 10 minutes for faster processing (600 seconds)
            if video.duration > 600:
                video = video.subclip(0, 600)
                logger.info("Video longer than 10 minutes, processing first 10 minutes only")
            
            # Extract audio and optimize for speech recognition
            audio = video.audio
            
            # Write optimized audio file with 16kHz sample rate for speech
            audio.write_audiofile(
                output_path,
                verbose=False,
                logger=None,
                codec='pcm_s16le',  # Use PCM format for best quality
                ffmpeg_params=["-ar", "16000"]  # Set 16kHz sample rate via ffmpeg
            )
            
            logger.info(f"Audio optimized: duration={audio.duration:.1f}s, sample_rate=16000Hz")
            
        finally:
            video.close()
            if 'audio' in locals():
                audio.close()
    
    async def _transcribe_with_whisper(self, audio_path: str) -> str:
        """Transcribe audio using OpenAI Whisper with timeout"""
        try:
            # Run Whisper in executor with timeout to prevent hanging
            loop = asyncio.get_event_loop()
            
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._whisper_transcribe_sync, audio_path),
                timeout=300.0  # 5 minute timeout
            )
            
            return result
            
        except asyncio.TimeoutError:
            raise ValueError("Audio transcription timed out after 5 minutes")
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            raise ValueError(f"Failed to transcribe audio: {str(e)}")
    
    def _whisper_transcribe_sync(self, audio_path: str) -> str:
        """Synchronous Whisper transcription"""
        try:
            # Transcribe with Whisper
            result = self.whisper_model.transcribe(
                audio_path,
                language="en",  # Force English for better performance
                word_timestamps=False,  # Disable for faster processing
                fp16=False  # Use fp32 for better compatibility
            )
            
            # Extract text
            text = result["text"]
            
            # Add timestamps and segment info if available
            if "segments" in result and result["segments"]:
                logger.info(f"Transcribed {len(result['segments'])} segments")
                
                # Optionally, we could add segment-based chunking here
                # For now, just return the full text
                
            return text.strip()
            
        except Exception as e:
            logger.error(f"Whisper sync transcription error: {e}")
            raise
    
    async def batch_transcribe_videos(self, video_paths: list) -> dict:
        """Batch process multiple videos for better efficiency"""
        if not self.whisper_model:
            raise ValueError("Whisper model not available")
        
        results = {}
        logger.info(f"Starting batch transcription of {len(video_paths)} videos")
        
        for i, video_path in enumerate(video_paths, 1):
            try:
                logger.info(f"Processing video {i}/{len(video_paths)}: {video_path}")
                text = await self.extract_text_from_video(video_path)
                results[video_path] = text
                
            except Exception as e:
                logger.error(f"Failed to process {video_path}: {e}")
                results[video_path] = f"Error: {str(e)}"
        
        logger.info(f"Batch transcription completed: {len(results)} results")
        return results

# Create service instance
text_extraction_service = TextExtractionService()