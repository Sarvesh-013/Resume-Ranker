from flask import Flask, request, jsonify, render_template, send_file
from kafka import KafkaProducer
import mysql.connector
import os
import json
from flask_cors import CORS
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, emit
import time
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


app = Flask(__name__, template_folder='templates')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Store uploaded file statuses (in-memory for simplicity)
file_statuses = {}

# Kafka Producer Configuration
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC")
KAFKA_SERVER = os.getenv("KAFKA_SERVER")
try:
    producer = KafkaProducer(bootstrap_servers=KAFKA_SERVER)
    print("✅ Kafka producer connected successfully")
except Exception as e:
    print(f"❌ Failed to connect to Kafka: {e}")
    exit(1)

UPLOAD_FOLDER = "Uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# MySQL Database Configuration
DB_CONFIG = {
    "host": os.getenv("host"),
    "user": os.getenv("user"),
    "password": os.getenv("password"),
    "database": os.getenv("database")
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        print("✅ MySQL connected successfully")
        return conn
    except Exception as e:
        print(f"❌ MySQL connection error: {e}")
        return None

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/dashboard", methods=["GET"])
def dashboard():
    return render_template("dashboard.html")

@app.route("/upload", methods=["POST"])
def upload_resume():
    print("📥 Received upload request")
    if "files" not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist("files")
    if not files or all(file.filename == "" for file in files):
        return jsonify({"error": "No selected files"}), 400

    allowed_extensions = {".pdf", ".docx", ".txt"}
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    file_paths = []

    for file in files:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            return jsonify({"error": f"Unsupported file format: {file.filename}. Only PDF, DOCX, TXT allowed"}), 400

        file.seek(0, os.SEEK_END)
        if file.tell() > MAX_FILE_SIZE:
            return jsonify({"error": f"File too large: {file.filename}. Max 5MB allowed"}), 400
        file.seek(0)

        timestamp = int(time.time() * 1000)
        original_filename = secure_filename(file.filename)
        filename = f"{timestamp}_{original_filename}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)
        print(f"💾 File saved to {filepath}")
        file_paths.append(filepath)
        file_statuses[filepath] = {"status": "queued", "filename": original_filename}

    job_description = request.form.get("job_description", "default job description")
    skills = [s.strip() for s in request.form.get("skills", "").split(",") if s.strip()]

    try:
        for filepath in file_paths:
            message = {
                "file_path": filepath,
                "original_filename": os.path.basename(filepath),
                "job_description": job_description,
                "skills": skills
            }
            producer.send(KAFKA_TOPIC, value=json.dumps(message).encode("utf-8"))
            print(f"📡 Sent to Kafka: {message}")
        producer.flush()
    except Exception as e:
        for filepath in file_paths:
            file_statuses[filepath]["status"] = "failed"
            file_statuses[filepath]["error"] = str(e)
        socketio.emit('upload_status', {
            'file_paths': file_paths,
            'status': 'failed',
            'error': str(e),
            'filenames': {fp: file_statuses[fp]["filename"] for fp in file_paths}
        })
        return jsonify({"error": f"Kafka error: {str(e)}"}), 500

    socketio.emit('upload_status', {
        'file_paths': file_paths,
        'status': 'queued',
        'filenames': {fp: file_statuses[fp]["filename"] for fp in file_paths}
    })

    return jsonify({
        "message": "Resumes uploaded successfully, processing started",
        "file_paths": file_paths
    }), 200

@app.route("/resume/<int:resume_id>", methods=["GET"])
def serve_resume(resume_id):
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM resumes WHERE id = %s", (resume_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result or not result[0]:
            return jsonify({"error": "Resume not found"}), 404

        file_path = result[0]
        if not os.path.exists(file_path):
            return jsonify({"error": "File not found on server"}), 404

        return send_file(file_path, as_attachment=False)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/store_resume", methods=["POST"])
def store_resume():
    try:
        data = request.json
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid data provided"}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor()

        sql = """
        INSERT INTO resumes (name, phone, email, skills, summary, job_score, positives, uploaded_at, file_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        """
        values = (
            data.get("name", "None"),
            data.get("phone", "None"),
            data.get("email", "None"),
            data.get("skills", "None"),
            data.get("summary", "None"),
            data.get("job_score", 0),
            data.get("positives", "None"),
            data.get("file_path", None)
        )

        cursor.execute(sql, values)
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Resume data stored successfully")
        socketio.emit('new_resume', data)
        return jsonify({"message": "Resume data stored successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/resumes", methods=["GET"])
def get_resumes():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor(dictionary=True)

        query = "SELECT id, name, phone, email, skills, job_score, positives, file_path FROM resumes"
        conditions = []
        params = []

        skills = request.args.get("skills", "").strip()
        if skills:
            skills_list = [s.strip() for s in skills.split(",") if s.strip()]
            if skills_list:
                conditions.append("skills LIKE %s")
                params.append("%" + "%".join(skills_list) + "%")

        min_score = request.args.get("min_score", "").strip()
        max_score = request.args.get("max_score", "").strip()
        if min_score and min_score.isdigit():
            conditions.append("job_score >= %s")
            params.append(int(min_score))
        if max_score and max_score.isdigit():
            conditions.append("job_score <= %s")
            params.append(int(max_score))

        name = request.args.get("name", "").strip()
        if name:
            conditions.append("name LIKE %s")
            params.append(f"%{name}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        sort_by = request.args.get("sort_by", "job_score").lower()
        sort_order = request.args.get("sort_order", "desc").lower()
        if sort_by in ["name", "job_score"]:
            query += f" ORDER BY {sort_by} {sort_order.upper()}"

        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        offset = (page - 1) * per_page
        query += " LIMIT %s OFFSET %s"
        params.extend([per_page, offset])

        cursor.execute(query, params)
        resumes = cursor.fetchall()

        count_query = "SELECT COUNT(*) as total FROM resumes"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        cursor.execute(count_query, params[:-2] if conditions else [])
        total = cursor.fetchone()["total"]

        cursor.close()
        conn.close()
        return jsonify({
            "resumes": resumes,
            "total": total,
            "page": page,
            "per_page": per_page
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats", methods=["GET"])
def get_stats():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500
        cursor = conn.cursor()

        score_bins = [0, 20, 40, 60, 80, 100]
        score_counts = []
        for i in range(len(score_bins) - 1):
            cursor.execute(
                "SELECT COUNT(*) FROM resumes WHERE job_score >= %s AND job_score < %s",
                (score_bins[i], score_bins[i + 1])
            )
            count = cursor.fetchone()[0]
            score_counts.append(count)

        cursor.execute("SELECT skills FROM resumes WHERE skills IS NOT NULL AND skills != 'None'")
        skills_data = cursor.fetchall()
        skills_count = {}
        for row in skills_data:
            skills = [s.strip() for s in row[0].split(',') if s.strip()]
            for skill in skills:
                skills_count[skill] = skills_count.get(skill, 0) + 1

        top_skills = sorted(skills_count.items(), key=lambda x: x[1], reverse=True)[:5]
        skills_labels = [skill[0] for skill in top_skills]
        skills_values = [skill[1] for skill in top_skills]

        cursor.close()
        conn.close()
        return jsonify({
            "job_score": {
                "labels": [f"{score_bins[i]}-{score_bins[i+1]}" for i in range(len(score_bins)-1)],
                "values": score_counts
            },
            "skills": {
                "labels": skills_labels,
                "values": skills_values
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# SocketIO event handlers
@socketio.on('resume_processing')
def handle_resume_processing(data):
    print(f"Received resume_processing: {data}")
    file_path = data['file_path']
    if file_path in file_statuses:
        file_statuses[file_path]['status'] = 'processing'
    emit('upload_status', {
        'file_path': file_path,
        'status': 'processing',
        'filename': file_statuses.get(file_path, {}).get('filename', os.path.basename(file_path))
    }, broadcast=True)

@socketio.on('resume_processed')
def handle_resume_processed(data):
    print(f"Received resume_processed: {data}")
    file_path = data['file_path']
    if file_path in file_statuses:
        file_statuses[file_path]['status'] = data['status']
        if data['status'] == 'completed':
            file_statuses[file_path]['data'] = data.get('data', {})
        else:
            file_statuses[file_path]['error'] = data.get('error', 'Unknown error')
    emit('upload_status', {
        'file_path': file_path,
        'status': data['status'],
        'filename': file_statuses.get(file_path, {}).get('filename', os.path.basename(file_path)),
        'data': data.get('data'),
        'error': data.get('error')
    }, broadcast=True)

@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

if __name__ == "__main__":
    print("🚀 Starting Flask app...")
    socketio.run(app, debug=True, port=5000)