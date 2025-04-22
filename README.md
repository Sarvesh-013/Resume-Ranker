**Resume Processing System**
A Python-based web application that processes resumes uploaded via a web interface, extracts structured data (name, phone, email, skills, summary, job score, positives) using AI (Ollama), and stores results in a MySQL database. The system uses Kafka for asynchronous processing, Flask with SocketIO for real-time feedback, and supports concurrent processing with threading.
Features

Resume Upload: Upload PDF, DOCX, or TXT resumes via a web interface.
AI Processing: Extract structured data using Ollama’s llama3.2 model.
Real-Time Feedback: SocketIO provides live updates on processing status.
Database Storage: Store processed resume data in MySQL.
Concurrent Processing: Process multiple resumes concurrently using ThreadPoolExecutor.
Environment Configuration: Use .env for secure configuration.

**Prerequisites**

Python: 3.8 or higher
MySQL: 8.0 or higher
Kafka: Apache Kafka 2.8 or higher (with ZooKeeper)
Ollama: Installed with llama3.2 model
Windows: For running start_all.bat (modify for Linux/macOS if needed)

**Installation**
1. Clone the Repository
git clone https://github.com/Sarvesh-13/Resume_Ranker.git
cd <your-repo>

2. Set Up a Virtual Environment
3. Install Dependencies
Install required Python packages:
pip install -r requirements.txt

4. Configure Environment Variables
Create a .env file in the project root with the following:
KAFKA_TOPIC=resume-topic
KAFKA_SERVER=localhost:9092
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_NAME=resume_db

5. Set Up MySQL
Start MySQL and create a database:CREATE DATABASE resume_db;

Create the resumes table:USE resume_db;
CREATE TABLE resumes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    phone VARCHAR(50),
    email VARCHAR(255),
    skills TEXT,
    summary TEXT,
    job_score INT,
    positives TEXT,
    uploaded_at DATETIME,
    file_path VARCHAR(255),
    UNIQUE (file_path)
);

6. Install and Configure Kafka

Download and install Kafka: Apache Kafka
Start ZooKeeper and Kafka server (modify paths as needed):# Start ZooKeeper
.\bin\windows\zookeeper-server-start.bat .\config\zookeeper.properties

# Start Kafka
.\bin\windows\kafka-server-start.bat .\config\server.properties


Create the Kafka topic:kafka-topics.sh --create --bootstrap-server localhost:9092 --replication-factor 1 --partitions 1 --topic resume-topic

7. Install Ollama

Install Ollama: Ollama Installation
Pull the llama3.2 model:ollama pull llama3.2

Start the Ollama server:ollama serve

**Usage**
1. Start the Application
Run the batch file to start Flask and the resume processor (Windows):
start_all.bat

Alternatively, start manually:
# Start Flask app
python app.py
# Start resume processor in a separate terminal
python resume_processor.py

2. Access the Web Interface
Open a browser and navigate to:
http://localhost:5000

Upload a resume (PDF, DOCX, or TXT).
Monitor processing status in real-time.
View processed resumes on the dashboard.

3. Monitor Logs
Check resume_processor.log for processing details and errors.
File Structure
<your-repo>/
├── templates/
│   ├── index.html       # Upload page
│   ├── dashboard.html   # Processed resumes dashboard
├── app.py               # Flask app with upload and SocketIO
├── resume_processor.py  # Kafka consumer for resume processing
├── start_all.bat        # Windows script to start services(chnage the paths according to your project and kafka location)
├── .env                 # Environment variables (not in repo)
├── requirements.txt     # Python dependencies
├── README.md            # This file

**Troubleshooting**

Kafka Connection Issues: Ensure ZooKeeper and Kafka are running, and KAFKA_SERVER matches your setup.
MySQL Errors: Verify DB_HOST, DB_USER, DB_PASSWORD, and DB_NAME in .env.
Ollama Failures: Check if llama3.2 is pulled and the server is running (curl http://localhost:11434).
UI Feedback Missing: Inspect browser console for SocketIO errors and ensure app.py is running.
Reprocessing Resumes: Verify offset commits in logs (Committed Kafka offset).
