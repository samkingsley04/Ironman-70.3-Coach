"""
FastAPI backend for the Elite Triathlon Coach Dashboard.

Serves the static frontend, exposes read-only endpoints over the JSON files
in data/ (the same files a future Garmin/TrainingPeaks sync script will
write to), and proxies chat turns to the Anthropic API with the athlete's
recent training data injected into the system prompt.
"""

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
MODEL = "claude-sonnet-4-6"

app = FastAPI(title="Triathlon Coach Dashboard")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def load_json(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return json.loads(path.read_text())


@app.get("/api/daily-metrics")
def get_daily_metrics():
    return load_json("daily_metrics.json")


@app.get("/api/activities")
def get_activities():
    return load_json("activities.json")


@app.get("/api/pmc")
def get_pmc():
    return load_json("pmc.json")


@app.get("/api/weekly-summary")
def get_weekly_summary():
    return load_json("weekly_summary.json")


@app.get("/api/blocks")
def get_blocks():
    return load_json("blocks.json")


ATHLETE_CONTEXT = """You are an experienced elite endurance coach speaking directly with your athlete \
in a one-on-one coaching relationship. Talk like a coach who knows this athlete well — direct, \
specific, technically fluent — not like a generic fitness assistant addressing a beginner.

Athlete profile:
- Elite amateur triathlete. 70.3 PB ~4:30, Ironman PB ~10:25.
- Fully self-coached, using TrainingPeaks Premium and elite-level published training programs for structure.
- Goal race: Sunshine Coast 70.3, September 2027 — building toward qualifying for the 2028 70.3 World Championships.
- FTP ~285W on the bike, run threshold pace ~3:42/km, swim CSS ~1:22/100m.

You have this athlete's real recent training and physiological data injected below on every request \
(daily metrics, recent activities, current PMC/fitness numbers). Reference it specifically — actual \
numbers, actual sessions, actual trends — never give generic advice that could apply to anyone. When \
asked about trends, look at the data and reason about it like a coach reviewing a TrainingPeaks account. \
When asked to plan a session or a block, ground it in the athlete's current CTL/ATL/TSB, recent training \
load, and how it fits the long buildup to Sept 2027.

When building sessions or training blocks, structure your response so it could later be parsed into \
concrete workouts (date, sport, session name, target duration, target TSS/IF, description) — use clear \
structure (tables or labeled fields) rather than pure prose, since these plans are meant to eventually be \
exported into the athlete's training log."""


def build_chat_system_prompt() -> str:
    daily_metrics = load_json("daily_metrics.json")[-30:]
    activities = load_json("activities.json")[-20:]
    pmc = load_json("pmc.json")
    current_pmc = pmc[-1] if pmc else None

    return (
        f"{ATHLETE_CONTEXT}\n\n"
        f"--- Current fitness (latest PMC) ---\n{json.dumps(current_pmc, indent=2)}\n\n"
        f"--- Last 30 days of daily metrics ---\n{json.dumps(daily_metrics, indent=2)}\n\n"
        f"--- Last 20 activities ---\n{json.dumps(activities, indent=2)}\n"
    )


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in .env")

    system_prompt = build_chat_system_prompt()
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    def generate():
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    return StreamingResponse(generate(), media_type="text/plain")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC_DIR / "app.js")


@app.get("/styles.css")
def styles_css():
    return FileResponse(STATIC_DIR / "styles.css")
