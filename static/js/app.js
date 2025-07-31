// Simple version of the app focused on core functionality
console.log('Loading simple app version');

let selectedDocument = null;

// Simple file upload function
async function uploadFile(file) {
    console.log('Uploading file:', file.name);
    
    try {
        const formData = new FormData();
        formData.append('file', file);

        // Show progress
        showToast('Uploading file...', 'info');

        const response = await fetch('/api/upload/file', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (response.ok) {
            showToast('File uploaded successfully! Processing...', 'success');
            console.log('Upload successful:', result);
            
            // Immediately refresh document list to show the uploaded file
            loadDocuments();
            
            // Start checking status more frequently
            checkDocumentStatus(result.document_id);
            
        } else {
            throw new Error(result.detail || 'Upload failed');
        }
    } catch (error) {
        showToast(`Upload failed: ${error.message}`, 'error');
        console.error('Upload error:', error);
    }
}

// Check document processing status
async function checkDocumentStatus(documentId) {
    console.log('Checking status for:', documentId);
    
    try {
        const response = await fetch(`/api/upload/status/${documentId}`);
        const result = await response.json();
        
        console.log('Status:', result.status);
        
        if (result.status === 'completed') {
            showToast('Document processed successfully!', 'success');
            selectedDocument = result;
            loadDocuments(); // Refresh document list
            showFeatures();
        } else if (result.status === 'failed') {
            showToast(`Processing failed: ${result.error_message || 'Unknown error'}`, 'error');
            loadDocuments(); // Refresh to show failed status
        } else if (result.status === 'processing') {
            showToast('🔄 Processing document... This may take a moment for videos', 'info');
            loadDocuments(); // Refresh to show processing status
            setTimeout(() => checkDocumentStatus(documentId), 2000); // Check every 2 seconds
        } else {
            showToast('📤 Document uploaded, starting processing...', 'info');
            loadDocuments(); // Refresh document list
            setTimeout(() => checkDocumentStatus(documentId), 1000); // Check quickly for pending
        }
    } catch (error) {
        console.error('Status check error:', error);
    }
}

// Show features section
function showFeatures() {
    const featuresSection = document.getElementById('featuresSection');
    if (featuresSection) {
        featuresSection.style.display = 'block';
        featuresSection.innerHTML = `
            <h2>🤖 AI Features</h2>
            <div class="feature-tabs">
                <button onclick="showQuestionTab()" class="feature-tab active" id="questionTab">❓ Ask Questions</button>
                <button onclick="showSummaryTab()" class="feature-tab" id="summaryTab">📝 Generate Summary</button>
                <button onclick="showQuizTab()" class="feature-tab" id="quizTab">🧠 Create Quiz</button>
            </div>
            
            <!-- Q&A Chat Interface -->
            <div id="questionInterface" class="feature-content active">
                <div class="chat-container">
                    <div class="chat-messages" id="chatMessages">
                        <div class="chat-message ai">
                            <div class="message-content">
                                👋 Hi! I've analyzed your document. Ask me anything about it!
                            </div>
                        </div>
                    </div>
                    <div class="chat-input-container">
                        <input type="text" id="questionInput" placeholder="Ask a question about your document..." onkeypress="handleQuestionKeyPress(event)">
                        <button onclick="askQuestion()" id="askBtn" class="chat-send-btn">Send</button>
                    </div>
                </div>
            </div>
            
            <!-- Summary Interface -->
            <div id="summaryInterface" class="feature-content">
                <div class="summary-controls">
                    <label>Summary Length:</label>
                    <select id="summaryLength">
                        <option value="300">Short (300 words)</option>
                        <option value="500" selected>Medium (500 words)</option>
                        <option value="800">Long (800 words)</option>
                    </select>
                    <button onclick="generateSummary()" class="feature-btn">Generate Summary</button>
                </div>
                <div id="summaryResult" class="result-container"></div>
            </div>
            
            <!-- Quiz Interface -->
            <div id="quizInterface" class="feature-content">
                <div class="quiz-controls">
                    <label>Number of Questions:</label>
                    <select id="quizQuestions">
                        <option value="3">3 Questions</option>
                        <option value="5" selected>5 Questions</option>
                        <option value="10">10 Questions</option>
                    </select>
                    <label>Difficulty:</label>
                    <select id="quizDifficulty">
                        <option value="easy">Easy</option>
                        <option value="medium" selected>Medium</option>
                        <option value="hard">Hard</option>
                    </select>
                    <button onclick="generateQuiz()" class="feature-btn">Create Quiz</button>
                </div>
                <div id="quizResult" class="result-container"></div>
            </div>
        `;
        
        // Show the Q&A tab by default
        showQuestionTab();
    }
}

// Tab switching functions
function showQuestionTab() {
    switchTab('questionInterface', 'questionTab');
}

function showSummaryTab() {
    switchTab('summaryInterface', 'summaryTab');
}

function showQuizTab() {
    switchTab('quizInterface', 'quizTab');
}

function switchTab(contentId, tabId) {
    // Hide all content
    document.querySelectorAll('.feature-content').forEach(content => {
        content.classList.remove('active');
    });
    
    // Remove active from all tabs
    document.querySelectorAll('.feature-tab').forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Show selected content and tab
    const content = document.getElementById(contentId);
    const tab = document.getElementById(tabId);
    
    if (content) content.classList.add('active');
    if (tab) tab.classList.add('active');
}

// Handle Enter key in question input
function handleQuestionKeyPress(event) {
    if (event.key === 'Enter') {
        askQuestion();
    }
}

// AI feature functions
async function askQuestion() {
    const questionInput = document.getElementById('questionInput');
    const question = questionInput.value.trim();
    
    if (!question) {
        showToast('Please enter a question', 'warning');
        return;
    }
    
    // Add user message to chat
    addChatMessage(question, 'user');
    questionInput.value = '';
    
    // Show typing indicator
    const typingMsg = addChatMessage('Thinking...', 'ai', true);
    
    try {
        const response = await fetch('/api/features/question', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: selectedDocument.document_id,
                question: question
            })
        });
        
        const result = await response.json();
        
        // Remove typing indicator
        typingMsg.remove();
        
        if (response.ok) {
            addChatMessage(result.answer, 'ai');
        } else {
            addChatMessage('Sorry, I encountered an error processing your question.', 'ai');
        }
    } catch (error) {
        typingMsg.remove();
        addChatMessage('Sorry, I encountered an error processing your question.', 'ai');
        console.error('Question error:', error);
    }
}

function addChatMessage(message, sender, isTyping = false) {
    const chatMessages = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${sender} ${isTyping ? 'typing' : ''}`;
    
    messageDiv.innerHTML = `
        <div class="message-content">
            ${message}
        </div>
    `;
    
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    
    return messageDiv;
}

async function generateSummary() {
    const summaryResult = document.getElementById('summaryResult');
    const summaryLength = document.getElementById('summaryLength').value;
    
    try {
        showToast('Generating summary...', 'info');
        summaryResult.innerHTML = '<div class="loading">🤖 Analyzing document and generating summary...</div>';
        
        const response = await fetch('/api/features/summary', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: selectedDocument.document_id,
                max_length: parseInt(summaryLength)
            })
        });
        
        const result = await response.json();
        
        if (response.ok) {
            summaryResult.innerHTML = `
                <div class="summary-content">
                    <h3>📝 Document Summary</h3>
                    <div class="summary-text">${result.summary}</div>
                    <div class="summary-stats">
                        <span>📊 Word count: ${result.word_count}</span>
                        <span>📄 Chunks analyzed: ${result.chunks_used || 0}</span>
                    </div>
                </div>
            `;
            showToast('Summary generated successfully!', 'success');
        } else {
            throw new Error(result.detail || 'Failed to generate summary');
        }
    } catch (error) {
        summaryResult.innerHTML = `<div class="error">❌ Error generating summary: ${error.message}</div>`;
        showToast('Error generating summary', 'error');
    }
}

async function generateQuiz() {
    const quizResult = document.getElementById('quizResult');
    const numQuestions = document.getElementById('quizQuestions').value;
    const difficulty = document.getElementById('quizDifficulty').value;
    
    try {
        showToast('Generating quiz...', 'info');
        quizResult.innerHTML = '<div class="loading">🧠 Creating quiz questions...</div>';
        
        const response = await fetch('/api/features/quiz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: selectedDocument.document_id,
                num_questions: parseInt(numQuestions),
                difficulty: difficulty
            })
        });
        
        const result = await response.json();
        
        if (response.ok && result.questions && result.questions.length > 0) {
            let quizHtml = `
                <div class="quiz-content">
                    <h3>🧠 Quiz (${result.questions.length} questions)</h3>
                    <div class="quiz-instructions">
                        <p>📝 Answer the questions below. Click "Show Answers" when you're done!</p>
                    </div>
            `;
            
            result.questions.forEach((q, i) => {
                quizHtml += `
                    <div class="quiz-question">
                        <div class="question-header">
                            <strong>Question ${i+1}:</strong>
                        </div>
                        <div class="question-text">${q.question}</div>
                        <div class="question-options">
                `;
                
                q.options.forEach((option, j) => {
                    quizHtml += `
                        <label class="quiz-option">
                            <input type="radio" name="q${i}" value="${j}">
                            <span class="option-letter">${String.fromCharCode(65+j)}.</span>
                            <span class="option-text">${option}</span>
                        </label>
                    `;
                });
                
                quizHtml += `
                        </div>
                        <div class="correct-answer" data-correct="${q.correct_answer}" style="display: none;">
                            ✅ Correct answer: ${String.fromCharCode(65+q.correct_answer)}. ${q.options[q.correct_answer]}
                        </div>
                    </div>
                `;
            });
            
            quizHtml += `
                <div class="quiz-actions">
                    <button onclick="showQuizAnswers()" class="feature-btn" id="showAnswersBtn">Show Answers</button>
                    <button onclick="checkQuizAnswers()" class="feature-btn" id="checkAnswersBtn" style="display: none;">Check My Answers</button>
                </div>
                </div>
            `;
            
            quizResult.innerHTML = quizHtml;
            showToast('Quiz generated successfully!', 'success');
        } else {
            throw new Error(result.error || 'No questions generated');
        }
    } catch (error) {
        quizResult.innerHTML = `<div class="error">❌ Error generating quiz: ${error.message}</div>`;
        showToast('Error generating quiz', 'error');
    }
}

function showQuizAnswers() {
    document.querySelectorAll('.correct-answer').forEach(answer => {
        answer.style.display = 'block';
    });
    document.getElementById('showAnswersBtn').style.display = 'none';
    document.getElementById('checkAnswersBtn').style.display = 'inline-block';
}

function checkQuizAnswers() {
    const questions = document.querySelectorAll('.quiz-question');
    let correct = 0;
    let total = questions.length;
    
    questions.forEach((question, i) => {
        const correctAnswer = parseInt(question.querySelector('.correct-answer').dataset.correct);
        const selectedOption = question.querySelector(`input[name="q${i}"]:checked`);
        const selectedAnswer = selectedOption ? parseInt(selectedOption.value) : -1;
        
        const options = question.querySelectorAll('.quiz-option');
        options.forEach((option, j) => {
            if (j === correctAnswer) {
                option.classList.add('correct');
            } else if (j === selectedAnswer && j !== correctAnswer) {
                option.classList.add('incorrect');
            }
        });
        
        if (selectedAnswer === correctAnswer) {
            correct++;
        }
    });
    
    const percentage = Math.round((correct / total) * 100);
    const resultMessage = `
        <div class="quiz-score">
            <h4>📊 Your Score: ${correct}/${total} (${percentage}%)</h4>
            <p>${percentage >= 80 ? '🎉 Excellent!' : percentage >= 60 ? '👍 Good job!' : '📚 Keep studying!'}</p>
        </div>
    `;
    
    document.querySelector('.quiz-actions').innerHTML = resultMessage;
}

// Load and display documents
async function loadDocuments() {
    console.log('Loading documents...');
    
    try {
        const response = await fetch('/api/upload/documents');
        const data = await response.json();
        
        const documentsList = document.getElementById('documentsList');
        if (!documentsList) return;
        
        if (data.documents && data.documents.length > 0) {
            documentsList.innerHTML = data.documents.map(doc => {
                const statusIcon = {
                    'pending': '⏳',
                    'processing': '🔄',
                    'completed': '✅',
                    'failed': '❌'
                }[doc.status] || '📄';
                
                const statusText = {
                    'pending': 'Pending',
                    'processing': 'Processing...',
                    'completed': 'Ready',
                    'failed': 'Failed'
                }[doc.status] || 'Unknown';
                
                return `
                    <div class="document-item ${doc.status}" onclick="selectDocument('${doc.document_id}')" data-id="${doc.document_id}">
                        <div class="document-info">
                            <div class="document-name">
                                ${statusIcon} ${doc.filename}
                            </div>
                            <div class="document-status">
                                Status: ${statusText}
                                ${doc.chunk_count ? ` • ${doc.chunk_count} chunks` : ''}
                            </div>
                            <div class="document-date">
                                ${new Date(doc.created_at).toLocaleString()}
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            documentsList.innerHTML = '<p class="no-documents">No documents uploaded yet</p>';
        }
    } catch (error) {
        console.error('Error loading documents:', error);
        const documentsList = document.getElementById('documentsList');
        if (documentsList) {
            documentsList.innerHTML = '<p class="error">Error loading documents</p>';
        }
    }
}

function selectDocument(documentId) {
    console.log('Selecting document:', documentId);
    
    // Find the document
    fetch(`/api/upload/status/${documentId}`)
        .then(response => response.json())
        .then(result => {
            if (result.status === 'completed') {
                selectedDocument = result;
                showFeatures();
                showToast('Document selected!', 'success');
                
                // Highlight selected document
                document.querySelectorAll('.document-item').forEach(item => {
                    item.classList.remove('selected');
                });
                document.querySelector(`[data-id="${documentId}"]`).classList.add('selected');
            } else {
                showToast(`Document is ${result.status}. Please wait for processing to complete.`, 'info');
            }
        })
        .catch(error => {
            console.error('Error selecting document:', error);
            showToast('Error selecting document', 'error');
        });
}

// Helper functions
function showResult(html) {
    const resultDiv = document.getElementById('featureResult');
    if (resultDiv) {
        resultDiv.innerHTML = html;
        resultDiv.style.display = 'block';
    }
}

function showToast(message, type = 'info') {
    console.log(`Toast: ${message}`);
    
    const toast = document.getElementById('toast');
    if (toast) {
        toast.textContent = message;
        toast.className = `toast ${type} show`;
        
        setTimeout(() => {
            toast.classList.remove('show');
        }, 3000);
    }
}

// Initialize simple app
document.addEventListener('DOMContentLoaded', () => {
    console.log('Simple app initializing...');
    
    const fileInput = document.getElementById('fileInput');
    const uploadBtn = document.getElementById('uploadBtn');
    
    if (fileInput && uploadBtn) {
        uploadBtn.addEventListener('click', (e) => {
            e.preventDefault();
            console.log('Upload button clicked');
            fileInput.click();
        });
        
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                console.log('File selected:', e.target.files[0].name);
                uploadFile(e.target.files[0]);
            }
        });
        
        console.log('Simple app ready!');
        loadDocuments(); // Load existing documents on startup
        showToast('App ready! Upload PDF or video files to get started.', 'success');
    } else {
        console.error('Could not find upload elements');
    }
});