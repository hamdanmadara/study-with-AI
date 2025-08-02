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
from typing import Callable

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
    
    async def extract_text_from_video(self, file_path: str, progress_callback: Optional[Callable] = None) -> str:
        """Extract text from video using segmentation for long videos"""
        
        # Check dependencies
        if not WHISPER_AVAILABLE or not self.whisper_model:
            raise ValueError("OpenAI Whisper not available. Please install openai-whisper for video transcription.")
        
        if not self.ffmpeg_available:
            raise ValueError("FFmpeg not found. Please install FFmpeg to process video files. Download from https://ffmpeg.org/download.html")
            
        try:
            logger.info(f"Starting video processing for: {file_path}")
            
            # Pre-validate video file
            await self._validate_video_file(file_path)
            
            # Get video duration first - this is critical for segmentation
            try:
                video_duration = await self._get_video_duration(file_path)
                logger.info(f"✅ Successfully detected video duration: {video_duration:.1f} seconds ({video_duration/60:.1f} minutes)")
            except Exception as duration_error:
                logger.error(f"❌ Failed to detect video duration: {duration_error}")
                raise ValueError(f"Cannot process video without knowing its duration: {duration_error}")
            
            # If video is longer than 5 minutes, use segmentation
            if video_duration > 300:  # 5 minutes = 300 seconds
                return await self._process_video_segments(file_path, video_duration, progress_callback)
            else:
                # Process normally for short videos
                if progress_callback:
                    progress_callback({
                        'total_duration': video_duration,
                        'processed_duration': 0,
                        'total_segments': 1,
                        'processed_segments': 0,
                        'current_segment': 1
                    })
                
                result = await self._process_single_video_segment(file_path, 0, video_duration)
                
                if progress_callback:
                    progress_callback({
                        'total_duration': video_duration,
                        'processed_duration': video_duration,
                        'total_segments': 1,
                        'processed_segments': 1,
                        'current_segment': 1
                    })
                
                return result
        
        except Exception as e:
            logger.error(f"Error extracting text from video: {e}")
            raise ValueError(f"Failed to extract text from video: {str(e)}")
    
    async def extract_text_from_audio(self, file_path: str, progress_callback: Optional[Callable] = None) -> str:
        """Extract text from audio files using segmentation for long audio"""
        
        # Check dependencies
        if not WHISPER_AVAILABLE or not self.whisper_model:
            raise ValueError("OpenAI Whisper not available. Please install openai-whisper for audio transcription.")
        
        if not self.ffmpeg_available:
            raise ValueError("FFmpeg not found. Please install FFmpeg to process audio files. Download from https://ffmpeg.org/download.html")
            
        try:
            logger.info(f"Starting audio processing for: {file_path}")
            
            # Get audio duration first
            try:
                audio_duration = await self._get_audio_duration(file_path)
                logger.info(f"✅ Successfully detected audio duration: {audio_duration:.1f} seconds ({audio_duration/60:.1f} minutes)")
            except Exception as duration_error:
                logger.error(f"❌ Failed to detect audio duration: {duration_error}")
                raise ValueError(f"Cannot process audio without knowing its duration: {duration_error}")
            
            # If audio is longer than 5 minutes, use segmentation
            if audio_duration > 300:  # 5 minutes = 300 seconds
                return await self._process_audio_segments(file_path, audio_duration, progress_callback)
            else:
                # Process normally for short audio
                if progress_callback:
                    progress_callback({
                        'total_duration': audio_duration,
                        'processed_duration': 0,
                        'total_segments': 1,
                        'processed_segments': 0,
                        'current_segment': 1
                    })
                
                result = await self._process_single_audio_segment(file_path, 0, audio_duration)
                
                if progress_callback:
                    progress_callback({
                        'total_duration': audio_duration,
                        'processed_duration': audio_duration,
                        'total_segments': 1,
                        'processed_segments': 1,
                        'current_segment': 1
                    })
                
                return result
        
        except Exception as e:
            logger.error(f"Error extracting text from audio: {e}")
            raise ValueError(f"Failed to extract text from audio: {str(e)}")
    
    async def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using multiple methods"""
        logger.info(f"Attempting to get duration for audio: {audio_path}")
        
        # Method 1: Try FFprobe (most reliable for audio)
        try:
            logger.info("Trying FFprobe for audio duration detection...")
            duration = await self._get_duration_ffprobe(audio_path)
            if duration and duration > 0:
                logger.info(f"FFprobe detected audio duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                return duration
        except Exception as e:
            logger.warning(f"FFprobe failed for audio: {e}")
        
        # Method 2: Try FFmpeg directly for audio
        try:
            logger.info("Trying direct FFmpeg for audio duration detection...")
            duration = await self._get_duration_ffmpeg_direct(audio_path)
            if duration and duration > 0:
                logger.info(f"FFmpeg detected audio duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                return duration
        except Exception as e:
            logger.warning(f"Direct FFmpeg audio duration detection failed: {e}")
        
        # If all methods fail, log error and raise exception
        logger.error(f"All duration detection methods failed for audio {audio_path}")
        raise ValueError("Could not determine audio duration. Please ensure the audio file is valid and accessible.")
    
    async def _process_audio_segments(self, audio_path: str, total_duration: float, progress_callback: Optional[Callable] = None) -> str:
        """Process audio in 5-minute segments and combine transcriptions"""
        segment_duration = 300  # 5 minutes
        total_segments = int((total_duration + segment_duration - 1) // segment_duration)  # Ceiling division
        
        logger.info(f"Processing audio in {total_segments} segments of {segment_duration/60:.1f} minutes each")
        
        # Initial progress update
        if progress_callback:
            progress_callback({
                'total_duration': total_duration,
                'processed_duration': 0,
                'total_segments': total_segments,
                'processed_segments': 0,
                'current_segment': 0
            })
        
        all_transcriptions = []
        
        for segment_idx in range(total_segments):
            start_time = segment_idx * segment_duration
            end_time = min(start_time + segment_duration, total_duration)
            
            logger.info(f"Processing audio segment {segment_idx + 1}/{total_segments}: {start_time/60:.1f}min - {end_time/60:.1f}min")
            
            # Update progress before processing segment
            if progress_callback:
                progress_callback({
                    'total_duration': total_duration,
                    'processed_duration': start_time,
                    'total_segments': total_segments,
                    'processed_segments': segment_idx,
                    'current_segment': segment_idx + 1
                })
            
            try:
                segment_text = await self._process_single_audio_segment(
                    audio_path, start_time, end_time - start_time
                )
                
                if segment_text.strip():
                    # Add segment header for context
                    segment_header = f"\n[Segment {segment_idx + 1}: {start_time//60:02.0f}:{start_time%60:02.0f} - {end_time//60:02.0f}:{end_time%60:02.0f}]\n"
                    all_transcriptions.append(segment_header + segment_text.strip())
                    logger.info(f"Audio segment {segment_idx + 1} transcribed: {len(segment_text)} characters")
                else:
                    logger.info(f"Audio segment {segment_idx + 1}: No speech detected")
                    
            except Exception as e:
                logger.error(f"Error processing audio segment {segment_idx + 1}: {e}")
                all_transcriptions.append(f"\n[Segment {segment_idx + 1}: Error - {str(e)}]\n")
            
            # Update progress after processing segment
            if progress_callback:
                progress_callback({
                    'total_duration': total_duration,
                    'processed_duration': end_time,
                    'total_segments': total_segments,
                    'processed_segments': segment_idx + 1,
                    'current_segment': segment_idx + 1
                })
        
        # Combine all transcriptions
        final_text = "\n".join(all_transcriptions)
        
        if not final_text.strip():
            audio_name = Path(audio_path).stem
            final_text = f"Audio '{audio_name}' ({total_duration/60:.1f} minutes) contains no clear speech across {total_segments} segments. The audio may contain background music, noise, or speech that was not clearly audible."
        
        logger.info(f"Audio segmentation complete: {len(final_text)} total characters from {total_segments} segments")
        return final_text.strip()
    
    async def _process_single_audio_segment(self, audio_path: str, start_time: float, duration: float) -> str:
        """Process a single audio segment"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio_path = temp_audio.name
        
        try:
            # Extract audio segment using FFmpeg
            await self._extract_audio_segment_ffmpeg(audio_path, temp_audio_path, start_time, duration)
            
            # Transcribe segment
            text = await self._transcribe_with_whisper(temp_audio_path)
            return text.strip()
            
        finally:
            # Clean up temporary audio file
            if os.path.exists(temp_audio_path):
                try:
                    os.unlink(temp_audio_path)
                except OSError:
                    pass
    
    async def _extract_audio_segment_ffmpeg(self, audio_path: str, output_path: str, start_time: float, duration: float):
        """Extract audio segment using FFmpeg"""
        try:
            cmd = [
                'ffmpeg',
                '-y',  # Overwrite output file
                '-i', audio_path,
                '-ss', str(start_time),  # Start time
                '-t', str(duration),    # Duration
                '-acodec', 'pcm_s16le',  # PCM audio codec
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono channel
                '-af', 'volume=1.0',  # Ensure audio is processed
                output_path
            ]
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._run_ffmpeg_command, cmd)
            
            # Validate extracted audio file
            if not os.path.exists(output_path):
                raise ValueError("FFmpeg failed to create audio segment file")
            
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                raise ValueError("FFmpeg created empty audio segment file")
            
            if file_size < 1024:  # Less than 1KB indicates likely empty audio
                logger.warning(f"Very small audio segment created ({file_size} bytes) - may contain no audio")
                
        except Exception as e:
            logger.error(f"Error extracting audio segment with FFmpeg: {e}")
            raise
    
    async def _validate_video_file(self, video_path: str):
        """Pre-validate video file integrity and audio streams"""
        try:
            # Check if file exists and has reasonable size
            if not os.path.exists(video_path):
                raise ValueError("Video file does not exist")
            
            file_size = os.path.getsize(video_path)
            if file_size == 0:
                raise ValueError("Video file is empty")
            
            if file_size < 1024:  # Less than 1KB
                raise ValueError("Video file is too small to be valid")
            
            # Use FFprobe to check video file integrity and audio streams
            try:
                cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'a:0',  # Check for audio stream
                    '-show_entries', 'stream=codec_name,duration',
                    '-of', 'csv=p=0',
                    video_path
                ]
                
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._run_ffprobe_command, cmd)
                
                output = result.stdout.strip()
                if not output or output == "N/A":
                    logger.warning(f"Video {video_path} may not have an audio stream")
                    # Don't fail here - video might have no audio, which is OK
                else:
                    logger.info(f"Video has audio stream: {output}")
                    
            except Exception as probe_error:
                logger.warning(f"Could not validate video audio stream: {probe_error}")
                # Don't fail validation - proceed anyway
            
            logger.info(f"Video file validation passed: {file_size} bytes")
            
        except Exception as e:
            logger.error(f"Video validation failed: {e}")
            raise ValueError(f"Video file validation failed: {str(e)}")
    
    async def _get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using multiple methods"""
        logger.info(f"Attempting to get duration for: {video_path}")
        
        # Method 1: Try FFprobe (most reliable)
        try:
            logger.info("Trying FFprobe for duration detection...")
            duration = await self._get_duration_ffprobe(video_path)
            if duration and duration > 0:
                logger.info(f"FFprobe detected duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                return duration
        except Exception as e:
            logger.warning(f"FFprobe failed: {e}")
        
        # Method 2: Try MoviePy (good fallback)
        if MOVIEPY_AVAILABLE:
            try:
                logger.info("Trying MoviePy for duration detection...")
                loop = asyncio.get_event_loop()
                duration = await loop.run_in_executor(None, self._get_duration_moviepy, video_path)
                if duration and duration > 0:
                    logger.info(f"MoviePy detected duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                    return duration
            except Exception as e:
                logger.warning(f"MoviePy duration detection failed: {e}")
        
        # Method 3: Try FFmpeg directly (alternative approach)
        try:
            logger.info("Trying direct FFmpeg for duration detection...")
            duration = await self._get_duration_ffmpeg_direct(video_path)
            if duration and duration > 0:
                logger.info(f"FFmpeg detected duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
                return duration
        except Exception as e:
            logger.warning(f"Direct FFmpeg duration detection failed: {e}")
        
        # If all methods fail, log error and raise exception instead of defaulting
        logger.error(f"All duration detection methods failed for {video_path}")
        raise ValueError("Could not determine video duration. Please ensure the video file is valid and accessible.")
    
    async def _get_duration_ffprobe(self, video_path: str) -> float:
        """Get duration using FFprobe"""
        cmd = [
            'ffprobe',
            '-v', 'error',  # Only show errors
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0',
            video_path
        ]
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._run_ffprobe_command, cmd)
        
        duration_str = result.stdout.strip()
        if not duration_str or duration_str == 'N/A':
            raise ValueError("FFprobe returned invalid duration")
        
        return float(duration_str)
    
    async def _get_duration_ffmpeg_direct(self, video_path: str) -> float:
        """Get duration using FFmpeg directly"""
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-f', 'null',
            '-'
        ]
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._run_ffmpeg_for_duration, cmd)
        
        # Parse duration from FFmpeg output
        output = result.stderr
        for line in output.split('\n'):
            if 'Duration:' in line:
                # Extract duration in format HH:MM:SS.ss
                duration_part = line.split('Duration:')[1].split(',')[0].strip()
                time_parts = duration_part.split(':')
                if len(time_parts) == 3:
                    hours = float(time_parts[0])
                    minutes = float(time_parts[1])
                    seconds = float(time_parts[2])
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                    return total_seconds
        
        raise ValueError("Could not parse duration from FFmpeg output")
    
    def _run_ffmpeg_for_duration(self, cmd: list):
        """Run FFmpeg command for duration detection"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,  # 1 minute timeout for duration detection
                check=False  # Don't raise exception on non-zero exit (FFmpeg returns error when using null output)
            )
            return result
        except subprocess.TimeoutExpired:
            raise ValueError("FFmpeg duration detection timed out")
    
    def _run_ffprobe_command(self, cmd: list):
        """Run FFprobe command synchronously"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,  # Increased timeout
                check=True
            )
            logger.debug(f"FFprobe output: {result.stdout.strip()}")
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"FFprobe command failed: {' '.join(cmd)}")
            logger.error(f"FFprobe stderr: {e.stderr}")
            raise ValueError(f"FFprobe error: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise ValueError("FFprobe command timed out")
        except FileNotFoundError:
            raise ValueError("FFprobe not found. Please ensure FFmpeg is properly installed.")
    
    def _get_duration_moviepy(self, video_path: str) -> float:
        """Get video duration using MoviePy"""
        video = None
        try:
            logger.debug(f"Opening video with MoviePy: {video_path}")
            video = VideoFileClip(video_path)
            duration = video.duration
            
            if not duration or duration <= 0:
                raise ValueError("MoviePy returned invalid duration")
            
            logger.debug(f"MoviePy duration: {duration} seconds")
            return duration
            
        except Exception as e:
            logger.error(f"MoviePy error: {e}")
            raise ValueError(f"MoviePy failed to get duration: {e}")
        finally:
            if video is not None:
                try:
                    video.close()
                except Exception as close_error:
                    logger.warning(f"Error closing MoviePy video: {close_error}")
    
    async def _process_video_segments(self, video_path: str, total_duration: float, progress_callback: Optional[Callable] = None) -> str:
        """Process video in 5-minute segments and combine transcriptions"""
        segment_duration = 300  # 5 minutes
        total_segments = int((total_duration + segment_duration - 1) // segment_duration)  # Ceiling division
        
        logger.info(f"Processing video in {total_segments} segments of {segment_duration/60:.1f} minutes each")
        
        # Initial progress update
        if progress_callback:
            progress_callback({
                'total_duration': total_duration,
                'processed_duration': 0,
                'total_segments': total_segments,
                'processed_segments': 0,
                'current_segment': 0
            })
        
        all_transcriptions = []
        
        for segment_idx in range(total_segments):
            start_time = segment_idx * segment_duration
            end_time = min(start_time + segment_duration, total_duration)
            
            logger.info(f"Processing segment {segment_idx + 1}/{total_segments}: {start_time/60:.1f}min - {end_time/60:.1f}min")
            
            # Update progress before processing segment
            if progress_callback:
                progress_callback({
                    'total_duration': total_duration,
                    'processed_duration': start_time,
                    'total_segments': total_segments,
                    'processed_segments': segment_idx,
                    'current_segment': segment_idx + 1
                })
            
            try:
                segment_text = await self._process_single_video_segment(
                    video_path, start_time, end_time - start_time
                )
                
                if segment_text.strip():
                    # Add segment header for context
                    segment_header = f"\n[Segment {segment_idx + 1}: {start_time//60:02.0f}:{start_time%60:02.0f} - {end_time//60:02.0f}:{end_time%60:02.0f}]\n"
                    all_transcriptions.append(segment_header + segment_text.strip())
                    logger.info(f"Segment {segment_idx + 1} transcribed: {len(segment_text)} characters")
                else:
                    logger.info(f"Segment {segment_idx + 1}: No speech detected")
                    
            except Exception as e:
                logger.error(f"Error processing segment {segment_idx + 1}: {e}")
                all_transcriptions.append(f"\n[Segment {segment_idx + 1}: Error - {str(e)}]\n")
            
            # Update progress after processing segment
            if progress_callback:
                progress_callback({
                    'total_duration': total_duration,
                    'processed_duration': end_time,
                    'total_segments': total_segments,
                    'processed_segments': segment_idx + 1,
                    'current_segment': segment_idx + 1
                })
        
        # Combine all transcriptions
        final_text = "\n".join(all_transcriptions)
        
        if not final_text.strip():
            video_name = Path(video_path).stem
            final_text = f"Video '{video_name}' ({total_duration/60:.1f} minutes) contains no clear speech across {total_segments} segments. The video may contain background music, noise, or speech that was not clearly audible."
        
        logger.info(f"Video segmentation complete: {len(final_text)} total characters from {total_segments} segments")
        return final_text.strip()
    
    async def _process_single_video_segment(self, video_path: str, start_time: float, duration: float) -> str:
        """Process a single video segment"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
            temp_audio_path = temp_audio.name
        
        try:
            # Extract audio for this segment
            if MOVIEPY_AVAILABLE:
                try:
                    await self._extract_segment_audio_moviepy(video_path, temp_audio_path, start_time, duration)
                except Exception as e:
                    logger.warning(f"MoviePy segment extraction failed, trying FFmpeg: {e}")
                    await self._extract_segment_audio_ffmpeg(video_path, temp_audio_path, start_time, duration)
            else:
                await self._extract_segment_audio_ffmpeg(video_path, temp_audio_path, start_time, duration)
            
            # Transcribe segment
            text = await self._transcribe_with_whisper(temp_audio_path)
            return text.strip()
            
        finally:
            # Clean up temporary audio file
            if os.path.exists(temp_audio_path):
                try:
                    os.unlink(temp_audio_path)
                except OSError:
                    pass
    
    async def _extract_segment_audio_ffmpeg(self, video_path: str, output_path: str, start_time: float, duration: float):
        """Extract audio segment using FFmpeg"""
        try:
            cmd = [
                'ffmpeg',
                '-y',  # Overwrite output file
                '-i', video_path,
                '-ss', str(start_time),  # Start time
                '-t', str(duration),    # Duration
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # PCM audio codec
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono channel
                '-af', 'volume=1.0',  # Ensure audio is processed
                '-map', '0:a',  # Map first audio stream
                output_path
            ]
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._run_ffmpeg_command, cmd)
            
            # Validate extracted audio file
            if not os.path.exists(output_path):
                raise ValueError("FFmpeg failed to create audio segment file")
            
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                raise ValueError("FFmpeg created empty audio segment file")
            
            if file_size < 1024:  # Less than 1KB indicates likely empty audio
                logger.warning(f"Very small video audio segment created ({file_size} bytes) - may contain no audio")
                
        except Exception as e:
            logger.error(f"Error extracting video audio segment with FFmpeg: {e}")
            raise
    
    async def _extract_segment_audio_moviepy(self, video_path: str, output_path: str, start_time: float, duration: float):
        """Extract audio segment using MoviePy"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._extract_segment_sync, video_path, output_path, start_time, duration)
        except Exception as e:
            logger.error(f"Error extracting audio segment with MoviePy: {e}")
            raise
    
    def _extract_segment_sync(self, video_path: str, output_path: str, start_time: float, duration: float):
        """Synchronous segment extraction with MoviePy"""
        video = None
        audio = None
        
        try:
            video = VideoFileClip(video_path)
            
            # Extract segment - handle both subclip and subclipped methods
            end_time = start_time + duration
            try:
                # Try modern MoviePy method first
                segment = video.subclip(start_time, end_time)
            except AttributeError:
                # Fallback for older MoviePy versions
                try:
                    segment = video.subclipped(start_time, end_time)
                except AttributeError:
                    raise ValueError("MoviePy version not compatible - please upgrade or downgrade MoviePy")
            
            # Extract audio
            audio = segment.audio
            if audio is None:
                raise ValueError("No audio track found in video segment")
            
            # Write optimized audio file
            audio.write_audiofile(
                output_path,
                logger=None,
                verbose=False,
                codec='pcm_s16le',
                ffmpeg_params=["-ar", "16000", "-ac", "1"]
            )
            
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise ValueError("Failed to create audio segment file")
                
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
    
    def _run_ffmpeg_command(self, cmd: list):
        """Run FFmpeg command synchronously"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for longer videos
                check=True
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg command failed: {e.stderr}")
            raise ValueError(f"FFmpeg error: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise ValueError("FFmpeg command timed out")
    
    # Legacy method kept for compatibility - now unused
    async def _optimize_for_transcription_moviepy(self, video_path: str, output_path: str):
        """Legacy method - now replaced by segment processing"""
        logger.warning("Using legacy single-segment processing method")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._optimize_video_sync, video_path, output_path)
        except Exception as e:
            logger.error(f"Error optimizing video with MoviePy: {e}")
            raise
    
    def _optimize_video_sync(self, video_path: str, output_path: str):
        """Legacy synchronous video optimization - now only for short videos"""
        video = None
        audio = None
        
        try:
            video = VideoFileClip(video_path)
            
            # No duration limit anymore - this is only called for short videos
            logger.info(f"Processing full video duration: {video.duration:.1f}s")
            
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
            # Validate audio file before processing
            if not os.path.exists(audio_path):
                raise ValueError("Audio file does not exist")
            
            file_size = os.path.getsize(audio_path)
            if file_size == 0:
                raise ValueError("Audio file is empty")
            
            if file_size < 1024:  # Less than 1KB
                logger.warning(f"Very small audio file ({file_size} bytes) - may not contain speech")
                return "No clear speech detected in this segment."
            
            # Additional audio validation using basic file checks
            try:
                # Check if it's a valid audio file by trying to get its info
                import wave
                with wave.open(audio_path, 'rb') as wav_file:
                    frames = wav_file.getnframes()
                    if frames == 0:
                        logger.warning("Audio file has no frames")
                        return "No audio content detected in this segment."
            except Exception:
                # Not a WAV file or corrupted, but let Whisper try anyway
                logger.debug("Could not validate audio file format, proceeding with Whisper")
            
            # Run Whisper in executor with timeout to prevent hanging
            loop = asyncio.get_event_loop()
            
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._whisper_transcribe_sync, audio_path),
                timeout=300.0  # 5 minute timeout for segment processing
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
            # Check if model is loaded
            if not self.whisper_model:
                raise ValueError("Whisper model not loaded")
            
            # Load audio and check if it's valid
            import numpy as np
            audio_data = whisper.load_audio(audio_path)
            
            # Check for empty or invalid audio data
            if audio_data is None or len(audio_data) == 0:
                logger.warning("Empty audio data loaded from file")
                return "No audio content detected in this segment."
            
            # Check if audio contains only silence (very low energy)
            if np.max(np.abs(audio_data)) < 0.001:
                logger.warning("Audio appears to contain only silence")
                return "No speech detected in this segment."
            
            # Transcribe with Whisper with optimized settings for speed
            result = self.whisper_model.transcribe(
                audio_data,  # Pass the loaded audio data instead of file path
                language="en",  # Force English for better performance
                word_timestamps=False,  # Disable for faster processing
                fp16=False,  # Use fp32 for better compatibility
                condition_on_previous_text=False,  # Disable for faster processing
                temperature=0.0,  # Use greedy decoding for faster processing
                no_speech_threshold=0.6,  # Higher threshold to skip silence faster
                logprob_threshold=-1.0  # Lower threshold for faster processing
            )
            
            # Extract text
            text = result.get("text", "").strip()
            
            # Check if transcription is meaningful
            if not text or len(text) < 3:
                logger.info("No meaningful speech detected in segment")
                return "No clear speech detected in this segment."
            
            # Add timestamps and segment info if available
            if "segments" in result and result["segments"]:
                logger.info(f"Transcribed {len(result['segments'])} segments")
                
            return text
            
        except Exception as e:
            logger.error(f"Whisper sync transcription error: {e}")
            # Return a more helpful error message instead of raising
            if "tensor" in str(e).lower() and "reshape" in str(e).lower():
                return "Audio segment could not be processed - may contain no valid audio data."
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