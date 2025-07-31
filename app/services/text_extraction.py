import os
import tempfile
import subprocess
import shutil
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
        self.ffmpeg_available = self._check_ffmpeg()
        self._load_whisper_model()
    
    def _check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available on the system"""
        try:
            # Check if ffmpeg is available in PATH
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=10)
            if result.returncode == 0:
                logger.info("FFmpeg is available in PATH")
                return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        # Get current directory for local FFmpeg search
        current_dir = Path(__file__).parent.parent.parent
        
        # Try to find ffmpeg in various locations
        search_paths = [
            # Local project directory
            current_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
            current_dir / "ffmpeg.exe",
            # Common Windows locations
            Path('C:/ffmpeg/bin/ffmpeg.exe'),
            Path('C:/Program Files/ffmpeg/bin/ffmpeg.exe'),
            Path('C:/Program Files (x86)/ffmpeg/bin/ffmpeg.exe'),
            # User directory
            Path.home() / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        
        for path in search_paths:
            if path.exists():
                logger.info(f"Found FFmpeg at: {path}")
                # Add to PATH for MoviePy
                ffmpeg_dir = str(path.parent)
                if ffmpeg_dir not in os.environ.get('PATH', ''):
                    os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')
                return True
        
        # Log detailed instructions for user
        logger.warning("FFmpeg not found. To enable video processing:")
        logger.warning("1. Download FFmpeg Windows binaries from: https://www.gyan.dev/ffmpeg/builds/")
        logger.warning("2. Extract to C:/ffmpeg/ or your project directory")
        logger.warning("3. Make sure ffmpeg.exe is in the bin/ folder")
        logger.warning(f"4. Or place ffmpeg.exe directly in: {current_dir}")
        return False
    
    def _load_whisper_model(self):
        """Load Whisper model on startup for better performance"""
        if not WHISPER_AVAILABLE:
            logger.warning("OpenAI Whisper not available. Video transcription disabled.")
            return
            
        try:
            # Use 'tiny' model for faster processing with reasonable accuracy
            logger.info("Loading Whisper 'tiny' model for faster processing...")
            self.whisper_model = whisper.load_model("tiny")
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
        """Extract text from video using MoviePy + OpenAI Whisper with FFmpeg fallback"""
        
        # Check dependencies
        if not WHISPER_AVAILABLE or not self.whisper_model:
            raise ValueError("OpenAI Whisper not available. Please install openai-whisper for video transcription.")
        
        if not self.ffmpeg_available:
            raise ValueError("FFmpeg not found. Please install FFmpeg to process video files. Download from https://ffmpeg.org/download.html")
            
        try:
            logger.info(f"Starting video processing for: {file_path}")
            
            # Create temporary audio file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio_path = temp_audio.name
            
            try:
                # Try MoviePy first, fallback to direct FFmpeg if needed
                if MOVIEPY_AVAILABLE:
                    try:
                        await self._optimize_for_transcription_moviepy(file_path, temp_audio_path)
                    except Exception as e:
                        logger.warning(f"MoviePy failed, trying direct FFmpeg: {e}")
                        await self._extract_audio_with_ffmpeg(file_path, temp_audio_path)
                else:
                    await self._extract_audio_with_ffmpeg(file_path, temp_audio_path)
                
                # Transcribe audio using Whisper
                logger.info("Starting Whisper transcription...")
                text = await self._transcribe_with_whisper(temp_audio_path)
                
                if not text.strip():
                    text = "No clear speech detected in this video. The video may contain background music, noise, or speech that was not clearly audible."
                elif len(text.strip()) < 50:
                    # For very short transcriptions, add context
                    logger.info(f"Short transcription detected: '{text.strip()}'")
                    video_name = Path(file_path).stem
                    
                    # Check if it's likely just noise/unclear audio
                    if len(text.strip()) < 20:
                        text = f"Video '{video_name}' contains minimal or unclear audio content. Transcription: {text.strip()}"
                    else:
                        text = f"Video '{video_name}' contains brief speech: {text.strip()}"
                
                logger.info(f"Video processing completed. Extracted {len(text)} characters")
                return text.strip()
            
            finally:
                # Clean up temporary audio file
                if os.path.exists(temp_audio_path):
                    try:
                        os.unlink(temp_audio_path)
                    except OSError:
                        pass
        
        except Exception as e:
            logger.error(f"Error extracting text from video: {e}")
            raise ValueError(f"Failed to extract text from video: {str(e)}")
    
    async def _extract_audio_with_ffmpeg(self, video_path: str, output_path: str):
        """Extract audio using direct FFmpeg command as fallback"""
        try:
            logger.info("Extracting audio using FFmpeg...")
            
            # Run FFmpeg command to extract audio
            cmd = [
                'ffmpeg',
                '-y',  # Overwrite output file
                '-i', video_path,
                '-t', '300',  # Limit to 5 minutes
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # PCM audio codec
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono channel
                output_path
            ]
            
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._run_ffmpeg_command, cmd)
            
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise ValueError("FFmpeg failed to extract audio")
                
            logger.info("Audio extracted successfully with FFmpeg")
            
        except Exception as e:
            logger.error(f"Error extracting audio with FFmpeg: {e}")
            raise
    
    def _run_ffmpeg_command(self, cmd: list):
        """Run FFmpeg command synchronously"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                check=True
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg command failed: {e.stderr}")
            raise ValueError(f"FFmpeg error: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise ValueError("FFmpeg command timed out")
    
    async def _optimize_for_transcription_moviepy(self, video_path: str, output_path: str):
        """Optimize video for transcription using MoviePy"""
        try:
            logger.info("Optimizing video for transcription with MoviePy...")
            
            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._optimize_video_sync, video_path, output_path)
            
        except Exception as e:
            logger.error(f"Error optimizing video with MoviePy: {e}")
            raise
    
    def _optimize_video_sync(self, video_path: str, output_path: str):
        """Synchronous video optimization with better error handling"""
        video = None
        audio = None
        
        try:
            video = VideoFileClip(video_path)
            
            # Limit to first 5 minutes for faster processing (300 seconds)
            if video.duration > 300:
                video = video.subclip(0, 300)
                logger.info("Video longer than 5 minutes, processing first 5 minutes only")
            
            # Extract audio and optimize for speech recognition
            audio = video.audio
            if audio is None:
                raise ValueError("No audio track found in video - this video contains only visual content")
            
            # Write optimized audio file with 16kHz sample rate for speech
            audio.write_audiofile(
                output_path,
                logger=None,
                verbose=False,
                codec='pcm_s16le',  # Use PCM format for best quality
                ffmpeg_params=["-ar", "16000", "-ac", "1"]  # 16kHz, mono
            )
            
            # Verify output file was created
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise ValueError("Failed to create audio file")
            
            logger.info(f"Audio optimized: duration={audio.duration:.1f}s, sample_rate=16000Hz")
            
        except Exception as e:
            logger.error(f"MoviePy video processing error: {e}")
            # Clean up any partial files
            if os.path.exists(output_path):
                try:
                    os.unlink(output_path)
                except OSError:
                    pass
            raise
            
        finally:
            # Clean up resources
            if audio is not None:
                try:
                    audio.close()
                except:
                    pass
            if video is not None:
                try:
                    video.close()
                except:
                    pass
    
    async def _transcribe_with_whisper(self, audio_path: str) -> str:
        """Transcribe audio using OpenAI Whisper with timeout"""
        try:
            # Run Whisper in executor with timeout to prevent hanging
            loop = asyncio.get_event_loop()
            
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._whisper_transcribe_sync, audio_path),
                timeout=120.0  # 2 minute timeout for faster processing
            )
            
            return result
            
        except asyncio.TimeoutError:
            raise ValueError("Audio transcription timed out after 2 minutes")
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            raise ValueError(f"Failed to transcribe audio: {str(e)}")
    
    def _whisper_transcribe_sync(self, audio_path: str) -> str:
        """Synchronous Whisper transcription"""
        try:
            # Transcribe with Whisper with optimized settings for speed
            result = self.whisper_model.transcribe(
                audio_path,
                language="en",  # Force English for better performance
                word_timestamps=False,  # Disable for faster processing
                fp16=False,  # Use fp32 for better compatibility
                condition_on_previous_text=False,  # Disable for faster processing
                temperature=0.0,  # Use greedy decoding for faster processing
                no_speech_threshold=0.6,  # Higher threshold to skip silence faster
                logprob_threshold=-1.0  # Lower threshold for faster processing
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