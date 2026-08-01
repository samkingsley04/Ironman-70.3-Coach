"""
FastAPI backend for the Elite Triathlon Coach Dashboard.

Serves the static frontend, exposes read-only endpoints over the JSON files
in data/ (the same files a future Garmin/TrainingPeaks sync script will
write to), and proxies chat turns to the Anthropic API with the athlete's
recent training data injected into the system prompt.
"""

import json
import os
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from plan_analysis import build_plan_summary, get_planned_for_date
from progression_analysis import build_progression_summary
from session_analysis import build_progression_verdict, compute_activity_metrics
from today_analysis import build_today_summary

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DETAIL_DIR = DATA_DIR / "activities"
STATIC_DIR = BASE_DIR / "static"
MODEL = "claude-sonnet-4-6"

app = FastAPI(title="Triathlon Coach Dashboard")

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def load_json(filename: str):
    path = DATA_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    return json.loads(path.read_text())


PLANNED_SESSIONS_FILE = "planned_sessions.json"


def load_planned_sessions() -> list[dict]:
    path = DATA_DIR / PLANNED_SESSIONS_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_planned_sessions(sessions: list[dict]) -> None:
    (DATA_DIR / PLANNED_SESSIONS_FILE).write_text(json.dumps(sessions, indent=2))


@app.get("/api/daily-metrics")
def get_daily_metrics():
    return load_json("daily_metrics.json")


@app.get("/api/activities")
def get_activities():
    return load_json("activities.json")


def load_activity_detail(activity_id: int) -> dict:
    path = DETAIL_DIR / f"{activity_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no detail file for activity {activity_id}")
    return json.loads(path.read_text())


def find_activity(activity_id: int) -> dict:
    for a in load_json("activities.json"):
        if a.get("id") == activity_id:
            return a
    raise HTTPException(status_code=404, detail=f"activity {activity_id} not found")


@app.get("/api/activities/{activity_id}")
def get_activity_detail(activity_id: int):
    activity = find_activity(activity_id)
    detail = load_activity_detail(activity_id)
    computed = compute_activity_metrics(activity, detail)
    return {
        "activity": activity,
        "details": detail.get("details", {}),
        "laps": detail.get("laps", []),
        "records": detail.get("records", []),
        "computed": computed,
    }


@app.get("/api/activities/{activity_id}/comparable")
def get_activity_comparable(activity_id: int):
    activity = find_activity(activity_id)
    all_activities = load_json("activities.json")
    return build_progression_verdict(activity, all_activities, load_activity_detail)


@app.get("/api/pmc")
def get_pmc():
    return load_json("pmc.json")


@app.get("/api/weekly-summary")
def get_weekly_summary():
    return load_json("weekly_summary.json")


@app.get("/api/blocks")
def get_blocks():
    return load_json("blocks.json")


@app.get("/api/today")
def get_today():
    today = date.today()
    summary = build_today_summary(
        blocks=load_json("blocks.json"),
        pmc=load_json("pmc.json"),
        activities=load_json("activities.json"),
        plan_config=load_json("plan_config.json"),
        today=today,
    )
    summary["planned_session"] = get_planned_for_date(load_planned_sessions(), today.isoformat())
    return summary


@app.get("/api/progression")
def get_progression():
    return build_progression_summary(
        activities=load_json("activities.json"),
        load_detail=load_activity_detail,
        thresholds_history=load_json("thresholds.json"),
        today=date.today(),
    )


@app.get("/api/plan")
def get_plan():
    return build_plan_summary(
        planned_sessions=load_planned_sessions(),
        activities=load_json("activities.json"),
        today=date.today(),
    )


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

When you and the athlete land on specific session(s) to schedule — a single workout or a whole block's \
breakdown — call the propose_planned_sessions tool to actually write them to the plan, rather than just \
describing them in prose. That's the only way a plan you propose actually shows up on the athlete's Plan \
and Today pages, so use it whenever a session becomes concrete enough to schedule."""


def _trim_progression_for_chat(progression: dict) -> dict:
    """Full history is fetched for the Progression page's charts; the chat
    context only needs the recent tail of each trend, not decades of rows."""
    trimmed = dict(progression)
    trimmed["durability_trend"] = {sport: rows[-8:] for sport, rows in progression["durability_trend"].items()}
    trimmed["ef_trend"] = {sport: rows[-8:] for sport, rows in progression["ef_trend"].items()}
    return trimmed


def build_chat_system_prompt() -> str:
    today = date.today()
    activities_all = load_json("activities.json")
    daily_metrics = load_json("daily_metrics.json")[-30:]
    activities = activities_all[-20:]
    pmc = load_json("pmc.json")
    current_pmc = pmc[-1] if pmc else None

    today_summary = build_today_summary(
        blocks=load_json("blocks.json"),
        pmc=pmc,
        activities=activities_all,
        plan_config=load_json("plan_config.json"),
        today=today,
    )
    today_summary["planned_session"] = get_planned_for_date(load_planned_sessions(), today.isoformat())

    progression_summary = _trim_progression_for_chat(build_progression_summary(
        activities=activities_all,
        load_detail=load_activity_detail,
        thresholds_history=load_json("thresholds.json"),
        today=today,
    ))

    plan_summary = build_plan_summary(
        planned_sessions=load_planned_sessions(),
        activities=activities_all,
        today=today,
    )

    return (
        f"{ATHLETE_CONTEXT}\n\n"
        f"--- Current fitness (latest PMC) ---\n{json.dumps(current_pmc, indent=2)}\n\n"
        f"--- Today (block, race countdown, ramp rate, discipline balance, intensity distribution) ---\n"
        f"{json.dumps(today_summary, indent=2)}\n\n"
        f"--- Progression (bike power curve, durability trend, EF trend, thresholds) ---\n"
        f"{json.dumps(progression_summary, indent=2)}\n\n"
        f"--- Plan (today's planned session, upcoming, adherence) ---\n{json.dumps(plan_summary, indent=2)}\n\n"
        f"--- Last 30 days of daily metrics ---\n{json.dumps(daily_metrics, indent=2)}\n\n"
        f"--- Last 20 activities ---\n{json.dumps(activities, indent=2)}\n"
    )


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


PLANNED_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "sport": {"type": "string", "enum": ["swim", "bike", "run", "strength"]},
        "name": {"type": "string"},
        "target_duration_min": {"type": "number"},
        "target_tss": {"type": "number"},
        "target_if": {"type": "number"},
        "description": {"type": "string"},
    },
    "required": ["date", "sport", "name"],
}

CHAT_TOOLS = [
    {
        "name": "propose_planned_sessions",
        "description": (
            "Write one or more planned training sessions to the athlete's plan (data/planned_sessions.json). "
            "Use this any time you and the athlete agree on concrete session(s) or a block breakdown to "
            "schedule -- a single tomorrow's workout, or several weeks of sessions at once. Writing a session "
            "for a (date, sport) that already has a planned entry replaces it, so this also handles revising "
            "an existing plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sessions": {"type": "array", "items": PLANNED_SESSION_SCHEMA}},
            "required": ["sessions"],
        },
    }
]


def _execute_propose_planned_sessions(tool_input: dict) -> dict:
    new_sessions = tool_input.get("sessions", [])
    existing = load_planned_sessions()
    by_key = {(s.get("date"), s.get("sport")): s for s in existing}
    for s in new_sessions:
        by_key[(s.get("date"), s.get("sport"))] = {
            "date": s.get("date"),
            "sport": s.get("sport"),
            "name": s.get("name", ""),
            "target_duration_min": s.get("target_duration_min"),
            "target_tss": s.get("target_tss"),
            "target_if": s.get("target_if"),
            "description": s.get("description", ""),
            "status": "planned",
        }
    merged = sorted(by_key.values(), key=lambda s: (s["date"], s["sport"]))
    save_planned_sessions(merged)
    return {"status": "ok", "written": len(new_sessions), "total_planned": len(merged)}


def execute_chat_tool(name: str, tool_input: dict) -> dict:
    if name == "propose_planned_sessions":
        return _execute_propose_planned_sessions(tool_input)
    return {"status": "error", "message": f"unknown tool {name}"}


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in .env")

    system_prompt = build_chat_system_prompt()
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    def generate():
        # Tool-use loop: stream text each round, execute any tool calls, feed
        # the results back, and continue -- capped so a confused model can't
        # loop forever instead of finishing its answer.
        for _ in range(4):
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                messages=messages,
                tools=CHAT_TOOLS,
            ) as stream:
                for text in stream.text_stream:
                    yield text
                final = stream.get_final_message()

            if final.stop_reason != "tool_use":
                return

            messages.append({"role": "assistant", "content": final.content})
            tool_results = []
            for block in final.content:
                if block.type == "tool_use":
                    result = execute_chat_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
            messages.append({"role": "user", "content": tool_results})

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
