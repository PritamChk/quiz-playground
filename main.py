import json
import os
import random
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- PERSISTENCE SETTINGS ---
LEADERBOARD_FILE = "data/leaderboard.json"

def load_leaderboard():
    """Load leaderboard from JSON file on startup"""
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_leaderboard():
    """Write current leaderboard to JSON file"""
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(LEADERBOARD, f, indent=4)

# Initialize global variable from file
LEADERBOARD = load_leaderboard()

# --- HELPERS ---
def get_topics():
    with open("data/topics_index.json", "r") as f:
        return json.load(f)

def get_questions_for_topic(topic_id):
    path = f"data/topics/{topic_id}.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []

# --- ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Page 1: Landing"""
    # Sort by score (descending) and take top 10
    sorted_leaderboard = sorted(LEADERBOARD, key=lambda x: x['score'], reverse=True)[:10]
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "topics": get_topics(),
        "leaderboard": sorted_leaderboard
    })

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
    """Page 2: Quiz"""
    all_questions = get_questions_for_topic(topic_id)
    topic_info = next((t for t in get_topics() if t["id"] == topic_id), {"name": "Unknown"})

    # Randomize Sequence
    random.shuffle(all_questions)

    # Slice Questions
    limit = min(num_questions, len(all_questions))
    selected_questions = all_questions[:limit]
    
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

    return templates.TemplateResponse("quiz.html", {
        "request": request,
        "quiz_data": json.dumps(quiz_context)
    })

@app.post("/submit_result", response_class=HTMLResponse)
async def submit_result(request: Request, payload: str = Form(...)):
    """Page 3: Result"""
    data = json.loads(payload)
    
    # Calculate Score
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
            
    # Handle Skipped
    answered_indices = [int(k) for k in data['answers'].keys()]
    for i, q in enumerate(data['questions']):
        if i not in answered_indices:
             wrong_questions.append({
                "text": q['text'],
                "user_ans_indices": [],
                "correct_ans_indices": q['correct'],
                "options": q['options']
            })

    total_questions = len(data['questions'])
    score_percent = (correct_count / total_questions) * 100 if total_questions > 0 else 0
    passed = score_percent >= data['pass_percent']

    # --- SAVE TO FILE ---
    new_entry = {
        "name": data['guest_name'],
        "country": data['country_code'],
        "score": round(score_percent, 0)
    }
    LEADERBOARD.append(new_entry)
    save_leaderboard() # <--- Triggers the write to JSON file
    # --------------------

    return templates.TemplateResponse("result.html", {
        "request": request,
        "stats": {
            "guest_name": data['guest_name'],
            "topic": data['topic_name'],
            "score_percent": round(score_percent, 2),
            "passed": passed,
            "correct_count": correct_count,
            "total": total_questions,
            "skipped": data['skipped_count'],
            "time_taken": data['time_taken_formatted'],
            "wrong_questions": wrong_questions
        }
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)