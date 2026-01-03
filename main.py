import json
import os
import random
import time  # <--- Added for sleep/timeout logic
import boto3
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from botocore.exceptions import ClientError

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- AWS CONFIGURATION ---
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME","tf-learning-s3b-pc")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
LOCK_FILE_KEY = "system_lock.json"  # <--- The Lock File

s3_client = boto3.client('s3', region_name=AWS_REGION)

# --- S3 HELPER FUNCTIONS ---

def read_s3_json(key, default_value=None):
    """Generic helper to read JSON from S3"""
    if default_value is None: default_value = []
    if not S3_BUCKET_NAME:
        # Local fallback
        local_path = f"data/{key}"
        if os.path.exists(local_path):
            with open(local_path, "r") as f: return json.load(f)
        return default_value

    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except ClientError as e:
        if e.response['Error']['Code'] in ["NoSuchKey", "404"]:
            return default_value
        print(f"Error reading {key}: {e}")
        return default_value
    except Exception as e:
        print(f"Error: {e}")
        return default_value

def write_s3_json(key, data):
    """Generic helper to write JSON to S3"""
    if not S3_BUCKET_NAME:
        # Local fallback
        local_path = f"data/{key}"
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "w") as f: json.dump(data, f, indent=4)
        return

    try:
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=json.dumps(data, indent=4),
            ContentType='application/json'
        )
    except Exception as e:
        print(f"Error writing {key}: {e}")

# --- LOCKING LOGIC ---

def acquire_lock():
    """
    Checks lock file. 
    If LOCKED: Wait 1s, retry (max 10s).
    If UNLOCKED/Missing: Write 'LOCKED' and return True.
    """
    retries = 0
    max_retries = 10
    
    while retries < max_retries:
        # 1. Check status
        lock_data = read_s3_json(LOCK_FILE_KEY, default_value={"status": "UNLOCKED"})
        
        if lock_data.get("status") == "LOCKED":
            print(f"🔒 System locked. Waiting... ({retries+1}/{max_retries})")
            time.sleep(1) # Wait 1 second
            retries += 1
        else:
            # 2. It's free! seize the lock
            print("🔓 Lock acquired. locking system...")
            write_s3_json(LOCK_FILE_KEY, {"status": "LOCKED", "owner": "process_id"})
            return True
            
    print("⚠️ Lock timeout reached. Proceeding anyway (Force Unlock).")
    # Optional: You could return False here to fail safely, 
    # but for this app, we force proceed to avoid stuck locks.
    write_s3_json(LOCK_FILE_KEY, {"status": "LOCKED"}) 
    return True

def release_lock():
    """Sets lock file status to UNLOCKED"""
    print("🔓 Releasing lock...")
    write_s3_json(LOCK_FILE_KEY, {"status": "UNLOCKED"})

# --- DATA LOADERS (Unchanged) ---
def get_topics(): return read_s3_json("topics_index.json", default_value=[])
def get_questions_for_topic(t_id): return read_s3_json(f"topics/{t_id}.json", default_value=[])
def load_leaderboard(): return read_s3_json("leaderboard.json", default_value=[])
def save_leaderboard(data): write_s3_json("leaderboard.json", data)

# --- ROUTES (GET / and POST /start_quiz Unchanged) ---

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    leaderboard_data = load_leaderboard()
    sorted_leaderboard = sorted(leaderboard_data, key=lambda x: x['score'], reverse=True)[:10]
    return templates.TemplateResponse("index.html", {"request": request, "topics": get_topics(), "leaderboard": sorted_leaderboard})

@app.post("/start_quiz", response_class=HTMLResponse)
async def start_quiz(
    request: Request,
    guest_name: str = Form(...),
    country_code: str = Form(...),
    topic_id: str = Form(...),
    hours: int = Form(0),
    minutes: int = Form(15),
    num_questions: int = Form(10),
    pass_percent: int = Form(70)
):
    all_questions = get_questions_for_topic(topic_id)
    topics = get_topics()
    topic_info = next((t for t in topics if t["id"] == topic_id), {"name": "Unknown"})
    random.shuffle(all_questions)
    selected_questions = all_questions[:min(num_questions, len(all_questions))]
    duration_seconds = (hours * 3600) + (minutes * 60)

    quiz_context = {
        "guest_name": guest_name,
        "country_code": country_code,
        "topic_name": topic_info["name"],
        "topic_id": topic_id,
        "duration_seconds": duration_seconds,
        "pass_percent": pass_percent,
        "questions": selected_questions
    }
    return templates.TemplateResponse("quiz.html", {"request": request, "quiz_data": json.dumps(quiz_context)})

# --- MODIFIED SUBMIT RESULT ---

@app.post("/submit_result", response_class=HTMLResponse)
async def submit_result(request: Request, payload: str = Form(...)):
    """Page 3: Result with LOCKING logic"""
    data = json.loads(payload)
    
    # 1. Scoring Logic (Pure calculation, no lock needed yet)
    correct_count = 0
    wrong_questions = []
    
    for q_idx, user_ans in data['answers'].items():
        question = data['questions'][int(q_idx)]
        if sorted(user_ans) == sorted(question['correct']):
            correct_count += 1
        else:
            wrong_questions.append({
                "text": question['text'],
                "user_ans_indices": user_ans,
                "correct_ans_indices": question['correct'],
                "options": question['options']
            })
            
    answered_indices = [int(k) for k in data['answers'].keys()]
    for i, q in enumerate(data['questions']):
        if i not in answered_indices:
             wrong_questions.append({
                "text": q['text'],
                "user_ans_indices": [],
                "correct_ans_indices": q['correct'],
                "options": q['options']
            })

    total = len(data['questions'])
    score_percent = (correct_count / total) * 100 if total > 0 else 0
    passed = score_percent >= data['pass_percent']

    # 2. CRITICAL SECTION: Update S3 Leaderboard
    # We wrap this in try...finally to ensure we UNLOCK even if code fails
    try:
        acquire_lock() # <--- Waits here if locked
        
        # --- Read / Append / Write ---
        current_leaderboard = load_leaderboard()
        new_entry = {
            "name": data['guest_name'],
            "country": data['country_code'],
            "score": round(score_percent, 0)
        }
        current_leaderboard.append(new_entry)
        save_leaderboard(current_leaderboard)
        # -----------------------------
        
    except Exception as e:
        print(f"CRITICAL ERROR updating leaderboard: {e}")
    finally:
        release_lock() # <--- ALWAYS runs
    
    return templates.TemplateResponse("result.html", {
        "request": request,
        "stats": {
            "guest_name": data['guest_name'],
            "topic": data['topic_name'],
            "score_percent": round(score_percent, 2),
            "passed": passed,
            "correct_count": correct_count,
            "total": total,
            "skipped": data['skipped_count'],
            "time_taken": data['time_taken_formatted'],
            "wrong_questions": wrong_questions
        }
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)