from kafka import KafkaConsumer
import PyPDF2
import docx
import os
import json
import re
import mysql.connector
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import logging
from tqdm import tqdm
import sys
import socketio
import ollama
import time
import time
import signal
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    filename='resume_processor.log',
    format='%(asctime)s %(levelname)s:%(message)s'
)

# Kafka Configuration
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC")
KAFKA_SERVER = os.getenv("KAFKA_SERVER")

# Validate Kafka configuration
if not KAFKA_TOPIC or not KAFKA_SERVER:
    logging.error("KAFKA_TOPIC or KAFKA_SERVER not set in .env file")
    raise ValueError("KAFKA_TOPIC and KAFKA_SERVER are required")

# SocketIO client
sio = socketio.Client()

try:
    sio.connect('http://localhost:5000')
    logging.info("Connected to SocketIO server")
except Exception as e:
    logging.error(f"Failed to connect to SocketIO server: {e}")

try:
    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_SERVER,
        auto_offset_reset="earliest",
        group_id="resume-consumer-group",
        value_deserializer=lambda x: json.loads(x.decode("utf-8")),
        enable_auto_commit=False,
        session_timeout_ms=30000,
        heartbeat_interval_ms=10000,
        max_poll_interval_ms=600000
    )
    logging.info(f"Connected to Kafka broker, subscribed to topic: {KAFKA_TOPIC}")
except Exception as e:
    logging.error(f"Failed to initialize Kafka consumer: {e}")
    raise

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("Operation timed out")

def extract_text(file_path):
    """Extract text from a resume (PDF/DOCX/TXT)."""
    try:
        if file_path.endswith(".pdf"):
            with open(file_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
                print("yes")
                return text.strip()

        elif file_path.endswith(".docx"):
            doc = docx.Document(file_path)
            text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
            return text.strip()

        elif file_path.endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as file:
                return file.read().strip()

        else:
            print(f"Unsupported file format: {file_path}")
            return None

    except Exception as e:
        print(f"Error extracting text from {file_path}: {e}")
        return None


def extract_json(response_text):
    """Extract and validate JSON from response text."""
    logging.info(f"Raw Ollama response: {response_text[:1000]}")
    brace_count = 0
    start = -1
    for i, char in enumerate(response_text):
        if char == '{':
            if start == -1:
                start = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start != -1:
                json_text = response_text[start:i + 1]
                try:
                    parsed = json.loads(json_text)
                    required_fields = ["name", "phone", "email", "skills", "summary", "job_score", "positives"]
                    if all(field in parsed for field in required_fields):
                        return parsed
                    else:
                        return {
                            "error": f"Missing required fields: {', '.join(f for f in required_fields if f not in parsed)}",
                            "raw_output": response_text,
                            "extracted_text": json_text
                        }
                except json.JSONDecodeError as e:
                    return {
                        "error": f"Failed to parse extracted JSON: {e}",
                        "raw_output": response_text,
                        "extracted_text": json_text
                    }
    if start != -1 and brace_count > 0:
        json_text = response_text[start:] + '}'
        logging.info(f"Attempted fix by adding closing brace: {json_text[:1000]}")
        try:
            parsed = json.loads(json_text)
            required_fields = ["name", "phone", "email", "skills", "summary", "job_score", "positives"]
            if all(field in parsed for field in required_fields):
                return parsed
            else:
                return {
                    "error": f"Missing required fields: {', '.join(f for f in required_fields if f not in parsed)}",
                    "raw_output": response_text,
                    "attempted_text": json_text
                }
        except json.JSONDecodeError as e:
            return {
                "error": f"Failed to parse after fix attempt: {e}",
                "raw_output": response_text,
                "attempted_text": json_text
            }
    return {
        "error": "No complete JSON object found",
        "raw_output": response_text
    }

def process_resume_with_llama(resume_text, job_description, required_skills):
    """Extracts structured data and assigns a relevance score using local Ollama."""
    prompt = f"""
IMPORTANT: Output only the JSON object. Do not include any additional text, explanations, or notes.

Extract the relevant details from the given resume and return a valid JSON object.

### *STRICT RULES:*
- *Output ONLY JSON.* No explanations, reasoning, or formatting notes.
- *Ensure valid JSON.* No missing brackets, extra characters, or incorrect structures.
- *Follow the exact format* without placeholders.
- If any data is missing, explicitly mention "None" in the respective field.
- *Phone, Email, Skills* must be strings, not lists.

### *Data Extraction Requirements:*
- *name:* Extract the full name accurately.
- *phone:* Extract a valid phone number as a string (or "None" if missing).
- *email:* Extract a valid email address as a string (or "None" if missing).
- *skills:* Extract all skills listed in the resume as a comma-separated string.
- *summary:* Provide a brief professional summary if present, or "None."
- *job_score:* Score the applicant based on job relevance (0-100).
- *positives:* Highlight key strengths as a string.

---

### *Required Skills:* {required_skills}
### *Job Description:* {job_description}

Resume Content:
{resume_text[:5000]}  # Limit to 5000 characters

### *Expected JSON Output:*
{{
  "name": "<Full Name or 'None'>",
  "phone": "<Phone Number or 'None'>",
  "email": "<Email or 'None'>",
  "skills": "<Comma-separated skills or 'None'>",
  "summary": "<Brief summary or 'None'>",
  "job_score": <Score between 0-100>,
  "positives": "<Briefly highlight strengths>"
}}
    """

    max_retries = 2
    for attempt in range(max_retries):
        try:
            logging.info(f"Sending request to local Ollama server (attempt {attempt + 1}/{max_retries})")
            start_time = time.time()
            ollama_response = ollama.chat(
                model="llama3.2",
                messages=[{"role": "user", "content": prompt}],
                options={"timeout": 60}
            )
            end_time = time.time()
            logging.info(f"Received response from Ollama in {end_time - start_time:.2f} seconds")
            raw_output = ollama_response["message"]["content"]
            logging.info(f"Raw Ollama response: {raw_output[:1000]}")
            extracted_data = extract_json(raw_output)
            return extracted_data
        except Exception as e:
            logging.error(f"Local Ollama error on attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                logging.info("Retrying Ollama request after 5 seconds...")
                time.sleep(5)
            else:
                return {"error": f"Local Ollama error after {max_retries} attempts: {str(e)}", "raw_output": ""}

def save_to_database(resume_data, file_path):
    try:
        conn = mysql.connector.connect(
            host=os.getenv("host"),
            user=os.getenv("user"),
            password=os.getenv("password"),
            database=os.getenv("database"),
            connection_timeout=10
        )
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM resumes WHERE file_path = %s", (file_path,))
        if cursor.fetchone():
            logging.info(f"Skipped duplicate resume: {file_path}")
            sio.emit('resume_processed', {
                'file_path': file_path,
                'status': 'failed',
                'error': 'Duplicate resume'
            })
            return

        logging.info(f"Attempting to save resume data for: {file_path}")
        query = """
        INSERT INTO resumes (name, phone, email, skills, summary, job_score, positives, uploaded_at, file_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (
            resume_data.get("name", "None"),
            resume_data.get("phone", "None"),
            resume_data.get("email", "None"),
            resume_data.get("skills", "None"),
            resume_data.get("summary", "None"),
            resume_data.get("job_score", 0),
            resume_data.get("positives", "None"),
            datetime.now(),
            file_path
        )

        cursor.execute(query, values)
        conn.commit()
        logging.info(f"Successfully saved resume: {file_path}")
        cursor.close()
        conn.close()

        if sio.connected:
            sio.emit('resume_processed', {
                'file_path': file_path,
                'status': 'completed',
                'data': resume_data
            })
            logging.info(f"Emitted resume_processed event for: {file_path}")
        else:
            logging.error(f"SocketIO not connected, failed to emit resume_processed for: {file_path}")

    except mysql.connector.Error as e:
        logging.error(f"Database error while saving resume: {file_path}, Error: {e}")
        if sio.connected:
            sio.emit('resume_processed', {
                'file_path': file_path,
                'status': 'failed',
                'error': str(e)
            })
        else:
            logging.error(f"SocketIO not connected, failed to emit resume_processed for: {file_path}")

def process_message(message, consumer):
    logging.info(f"Received Kafka message: {message.value}")
    message_data = message.value
    file_path = message_data.get("file_path", "")
    job_description = message_data.get("job_description", "default job description")
    required_skills = message_data.get("skills", [])

    logging.info(f"Checking if file exists: {file_path}")
    if not os.path.exists(file_path):
        logging.error(f"File not found: {file_path}")
        if sio.connected:
            sio.emit('resume_processed', {
                'file_path': file_path,
                'status': 'failed',
                'error': 'File not found'
            })
        else:
            logging.error(f"SocketIO not connected, failed to emit resume_processed for: {file_path}")
        consumer.commit()
        return

    logging.info(f"Started processing: {file_path}")
    if sio.connected:
        sio.emit('resume_processing', {
            'file_path': file_path,
            'status': 'started'
        })
        logging.info(f"Emitted resume_processing event for: {file_path}")
    else:
        logging.error(f"SocketIO not connected, failed to emit resume_processing for: {file_path}")

    logging.info(f"Attempting to extract text from: {file_path}")
    resume_text = extract_text(file_path)
    if resume_text:
        logging.info(f"Text extracted successfully, length: {len(resume_text)}")
        extracted_details = process_resume_with_llama(resume_text, job_description, required_skills)
        if "error" in extracted_details:
            logging.error(f"Error processing resume: {file_path}, Error: {extracted_details['error']}")
            if sio.connected:
                sio.emit('resume_processed', {
                    'file_path': file_path,
                    'status': 'failed',
                    'error': extracted_details['error']
                })
            else:
                logging.error(f"SocketIO not connected, failed to emit resume_processed for: {file_path}")
        else:
            logging.info(f"Saving extracted details to database: {file_path}")
            save_to_database(extracted_details, file_path)
    else:
        logging.error(f"Failed to extract text from: {file_path}")
        if sio.connected:
            sio.emit('resume_processed', {
                'file_path': file_path,
                'status': 'failed',
                'error': 'Failed to extract text'
            })
        else:
            logging.error(f"SocketIO not connected, failed to emit resume_processed for: {file_path}")

    # Commit offset after processing
    try:
        consumer.commit()
        logging.info(f"Committed Kafka offset for message: {message.offset}")
    except Exception as e:
        logging.error(f"Failed to commit Kafka offset: {e}")

logging.info("Starting Kafka consumer loop")
with ThreadPoolExecutor(max_workers=4) as executor:
    progress_bar = tqdm(desc="Processed Resumes", unit="resume", file=sys.stdout)
    futures = []
    for message in consumer:
        logging.info("Fetched a message from Kafka")
        futures.append(executor.submit(process_message, message, consumer))
        progress_bar.update(1)
        if len(futures) >= 4:  # Process in batches of 4
            for future in futures:
                future.result()  # Wait for threads to complete
            futures = []  # Clear completed futures
    # Process any remaining futures
    for future in futures:
        future.result()
logging.info("Consumer loop ended")