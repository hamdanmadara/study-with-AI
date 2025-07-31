# Study AI Assistant 📚🤖

A powerful FastAPI application that enables users to upload PDF documents or videos and interact with them using AI-powered features including Q&A, summarization, and quiz generation.

## Features 🌟

- **📄 PDF Text Extraction**: Extract text content from PDF documents using pypdf
- **🎥 Video Transcription**: Convert video speech to text using moviepy + vosk
- **🔍 Question & Answer**: Ask questions about documents with RAG (Retrieval-Augmented Generation)
- **📝 Smart Summarization**: Generate comprehensive summaries with customizable length
- **🧠 Quiz Generation**: Create multiple-choice quizzes with different difficulty levels
- **🗄️ Vector Storage**: Efficient text chunking and embedding storage using ChromaDB
- **🤖 AI Integration**: Powered by DeepSeek LLM for intelligent responses

## Technology Stack 🛠️

### Backend
- **FastAPI**: Modern web framework for building APIs
- **DeepSeek**: Large Language Model for AI features
- **ChromaDB**: Vector database for similarity search
- **BAAI/bge-en-icl**: Embedding model from Hugging Face

### Text Processing
- **pypdf**: PDF text extraction
- **moviepy**: Video audio extraction
- **vosk**: Speech-to-text transcription
- **sentence-transformers**: Text embeddings

### Frontend
- **HTML5/CSS3/JavaScript**: Responsive web interface
- **Modern UI**: Clean, intuitive design with drag-and-drop

## Installation 🚀

### Prerequisites
- Python 3.8+
- DeepSeek API key

### Setup

1. **Clone the repository**
```bash
git clone <your-repo-url>
cd study-with-AI
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Download Vosk model** (for video transcription)
```bash
wget https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip
unzip vosk-model-en-us-0.22.zip
```

4. **Configure environment**
```bash
cp .env.example .env
# Edit .env and add your DeepSeek API key:
# DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

5. **Create required directories**
```bash
mkdir -p uploads vector_store
```

6. **Run the application**
```bash
python main.py
```

The application will be available at `http://localhost:8000`

## API Endpoints 📡

### File Upload
- `POST /api/upload/file` - Upload PDF or video file
- `GET /api/upload/status/{document_id}` - Check processing status
- `GET /api/upload/documents` - List all documents
- `DELETE /api/upload/documents/{document_id}` - Delete document

### AI Features
- `POST /api/features/question` - Ask questions about document
- `POST /api/features/summary` - Generate document summary
- `POST /api/features/quiz` - Create quiz from document
- `GET /api/features/available/{document_id}` - Get available features

## Usage Guide 📖

### 1. Upload Document
- Navigate to `http://localhost:8000/static/index.html`
- Drag & drop or click to upload PDF/video files
- Wait for processing to complete (status will show "completed")

### 2. Ask Questions
- Select your processed document
- Choose "Ask Questions" from the dropdown
- Type your question and get AI-powered answers with source references

### 3. Generate Summary
- Select "Generate Summary" 
- Choose desired length (Short/Medium/Long)
- Get comprehensive document summaries

### 4. Create Quiz
- Select "Create Quiz"
- Choose number of questions and difficulty
- Generate interactive multiple-choice quizzes

## Project Structure 📁

```
study-with-AI/
├── app/
│   ├── api/              # FastAPI route handlers
│   ├── core/             # Configuration and settings
│   ├── models/           # Pydantic data models
│   ├── services/         # Business logic services
│   └── utils/            # Utility functions
├── static/               # Frontend assets
│   ├── css/
│   ├── js/
│   └── index.html
├── uploads/              # Uploaded files storage
├── vector_store/         # ChromaDB storage
├── main.py              # Application entry point
├── requirements.txt     # Python dependencies
└── README.md
```

## Configuration ⚙️

### Environment Variables
- `DEEPSEEK_API_KEY`: Your DeepSeek API key (required)
- `DEBUG`: Enable debug mode (default: True)

### Settings (app/core/config.py)
- `max_file_size`: Maximum upload size (default: 100MB)
- `chunk_size`: Text chunking size (default: 1000 characters)
- `chunk_overlap`: Overlap between chunks (default: 200 characters)
- `embedding_model`: Hugging Face model (default: BAAI/bge-en-icl)

## Supported File Types 📎

### Documents
- PDF (.pdf)

### Videos  
- MP4 (.mp4)
- AVI (.avi)
- MOV (.mov)
- MKV (.mkv)
- WebM (.webm)

## Performance Optimization 🚀

- **Semantic Chunking**: Intelligent text splitting preserving context
- **Async Processing**: Non-blocking file processing
- **Vector Similarity Search**: Efficient context retrieval
- **Caching**: Document processing results cached locally
- **Streaming**: Large file handling with memory efficiency

## Scaling Considerations 📈

The application is designed with scalability in mind:

- **Modular Architecture**: Separate services for easy scaling
- **Async Operations**: Non-blocking I/O for better performance
- **Vector Database**: ChromaDB can be replaced with cloud solutions
- **Configuration-Driven**: Easy environment-specific deployments
- **API-First Design**: Backend can serve multiple frontends

## Troubleshooting 🔧

### Common Issues

1. **Vosk Model Not Found**
   - Download the vosk-model-en-us-0.22 and place in project root

2. **DeepSeek API Errors**
   - Verify your API key is correct in .env file
   - Check API quotas and limits

3. **File Upload Fails**
   - Check file size limits (default 100MB)
   - Ensure upload directory exists and is writable

4. **Processing Stuck**
   - Check logs for specific error messages
   - Verify all dependencies are installed correctly

## Contributing 🤝

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

## License 📄

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments 🙏

- DeepSeek for providing the LLM API
- ChromaDB for vector storage capabilities
- Hugging Face for the embedding models
- FastAPI team for the excellent framework