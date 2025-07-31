#!/usr/bin/env python3
"""
Test script to diagnose and test all application functionality
"""
import os
import asyncio
import requests
import json
from pathlib import Path

# Test configuration
BASE_URL = "http://localhost:8000"
TEST_PDF = "resume-hamdan-software-engineer.pdf"

def test_server_connection():
    """Test basic server connectivity"""
    print("🔍 Testing server connection...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✅ Server is running")
            return True
        else:
            print(f"❌ Server returned status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to server. Make sure it's running on port 8000")
        return False

def test_static_files():
    """Test static file serving"""
    print("\n🔍 Testing static file serving...")
    
    # Test main app route
    try:
        response = requests.get(f"{BASE_URL}/app")
        if response.status_code == 200:
            print("✅ /app route works")
        else:
            print(f"❌ /app route failed: {response.status_code}")
    except Exception as e:
        print(f"❌ /app route error: {e}")
    
    # Test static index.html
    try:
        response = requests.get(f"{BASE_URL}/static/index.html")
        if response.status_code == 200:
            print("✅ /static/index.html works")
        else:
            print(f"❌ /static/index.html failed: {response.status_code}")
    except Exception as e:
        print(f"❌ /static/index.html error: {e}")
    
    # Test CSS file
    try:
        response = requests.get(f"{BASE_URL}/static/css/style.css")
        if response.status_code == 200:
            print("✅ CSS file loads")
        else:
            print(f"❌ CSS file failed: {response.status_code}")
    except Exception as e:
        print(f"❌ CSS file error: {e}")
    
    # Test JS file
    try:
        response = requests.get(f"{BASE_URL}/static/js/app.js")
        if response.status_code == 200:
            print("✅ JS file loads")
        else:
            print(f"❌ JS file failed: {response.status_code}")
    except Exception as e:
        print(f"❌ JS file error: {e}")

def test_pdf_upload():
    """Test PDF file upload"""
    print(f"\n🔍 Testing PDF upload with {TEST_PDF}...")
    
    if not os.path.exists(TEST_PDF):
        print(f"❌ Test PDF file {TEST_PDF} not found")
        return None
    
    try:
        with open(TEST_PDF, 'rb') as f:
            files = {'file': (TEST_PDF, f, 'application/pdf')}
            response = requests.post(f"{BASE_URL}/api/upload/file", files=files)
        
        if response.status_code == 202:
            result = response.json()
            document_id = result.get('document_id')
            print(f"✅ PDF uploaded successfully! Document ID: {document_id}")
            return document_id
        else:
            print(f"❌ Upload failed: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Upload error: {e}")
        return None

def test_document_status(document_id):
    """Test document processing status"""
    print(f"\n🔍 Testing document status for {document_id}...")
    
    import time
    max_wait = 60  # Wait up to 60 seconds
    wait_time = 0
    
    while wait_time < max_wait:
        try:
            response = requests.get(f"{BASE_URL}/api/upload/status/{document_id}")
            if response.status_code == 200:
                result = response.json()
                status = result.get('status')
                print(f"📊 Status: {status}")
                
                if status == 'completed':
                    print("✅ Document processing completed!")
                    return True
                elif status == 'failed':
                    error = result.get('error_message', 'Unknown error')
                    print(f"❌ Document processing failed: {error}")
                    return False
                else:
                    print("⏳ Still processing... waiting 5 seconds")
                    time.sleep(5)
                    wait_time += 5
            else:
                print(f"❌ Status check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Status check error: {e}")
            return False
    
    print("⏰ Timeout waiting for processing to complete")
    return False

def test_ai_features(document_id):
    """Test AI features"""
    print(f"\n🔍 Testing AI features for document {document_id}...")
    
    # Test Q&A
    print("\n🤖 Testing Q&A feature...")
    try:
        question_data = {
            "document_id": document_id,
            "question": "What is the person's name and profession?"
        }
        response = requests.post(f"{BASE_URL}/api/features/question", 
                               json=question_data)
        if response.status_code == 200:
            result = response.json()
            print("✅ Q&A works!")
            print(f"Answer: {result.get('answer', 'No answer')[:100]}...")
        else:
            print(f"❌ Q&A failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Q&A error: {e}")
    
    # Test Summary
    print("\n📝 Testing Summary feature...")
    try:
        summary_data = {
            "document_id": document_id,
            "max_length": 300
        }
        response = requests.post(f"{BASE_URL}/api/features/summary", 
                               json=summary_data)
        if response.status_code == 200:
            result = response.json()
            print("✅ Summary works!")
            print(f"Summary: {result.get('summary', 'No summary')[:100]}...")
        else:
            print(f"❌ Summary failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Summary error: {e}")
    
    # Test Quiz
    print("\n🧠 Testing Quiz feature...")
    try:
        quiz_data = {
            "document_id": document_id,
            "num_questions": 3,
            "difficulty": "medium"
        }
        response = requests.post(f"{BASE_URL}/api/features/quiz", 
                               json=quiz_data)
        if response.status_code == 200:
            result = response.json()
            questions = result.get('questions', [])
            print(f"✅ Quiz works! Generated {len(questions)} questions")
            if questions:
                print(f"Sample question: {questions[0].get('question', 'No question')}")
        else:
            print(f"❌ Quiz failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Quiz error: {e}")

def main():
    """Run all tests"""
    print("🧪 Starting comprehensive application test...\n")
    
    # Test 1: Server connection
    if not test_server_connection():
        print("\n❌ Server is not running. Please start with: python run.py")
        return
    
    # Test 2: Static files
    test_static_files()
    
    # Test 3: PDF upload
    document_id = test_pdf_upload()
    if not document_id:
        print("\n❌ Cannot continue without successful upload")
        return
    
    # Test 4: Document processing
    if not test_document_status(document_id):
        print("\n❌ Document processing failed")
        return
    
    # Test 5: AI features
    test_ai_features(document_id)
    
    print("\n🎉 Test completed! Check the results above.")
    print(f"💻 Try the web interface at: {BASE_URL}/app")

if __name__ == "__main__":
    main()