from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os, logging, hmac, hashlib, time
from typing import List, Dict
from notion_client import Client
from openai import OpenAI

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="Task Intel Bot")

# ---------- Clients ----------
notion = None
openai_client = None

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if NOTION_TOKEN:
    try:
        notion = Client(auth=NOTION_TOKEN)
        logger.info("✅ Notion client connected")
    except Exception as e:
        logger.error(f"Notion client error: {e}")

if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("✅ OpenAI client connected")
    except Exception as e:
        logger.error(f"OpenAI client error: {e}")

# ---------- Slack signature ----------
def verify_slack_signature(request: Request, body: bytes) -> bool:
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not secret:
        return True  # allow if not configured (dev mode)
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    slack_sig = request.headers.get("X-Slack-Signature", "")
    # prevent replay
    try:
        if abs(time.time() - float(timestamp)) > 60 * 5:
            return False
    except:
        return False
    base = f"v0:{timestamp}:".encode() + body
    my_sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(my_sig, slack_sig)

# ---------- Safe access helpers (bulletproof against empties) ----------
def safe_title(props: Dict, key: str) -> str:
    block = props.get(key) or {}
    title = block.get("title") or []
    if not title:
        return "Unnamed Task"
    first = title[0] or {}
    return first.get("plain_text") or first.get("text", {}).get("content") or "Unnamed Task"

def safe_people(props: Dict, key: str) -> List[str]:
    block = props.get(key) or {}
    people = block.get("people") or []
    names = []
    for p in people:
        if not isinstance(p, dict):
            continue
        n = p.get("name") or p.get("plain_text")
        if n:
            names.append(n)
    return names

def safe_select(props: Dict, key: str, default: str = "Not set") -> str:
    block = props.get(key) or {}
    sel = block.get("select") or {}
    return sel.get("name") or default

def safe_date(props: Dict, key: str) -> str:
    block = props.get(key) or {}
    date = block.get("date") or {}
    return date.get("start") or "No due date"

def safe_rich_text(props: Dict, key: str, default: str = "Not specified") -> str:
    block = props.get(key) or {}
    rt = block.get("rich_text") or []
    if not rt:
        return default
    first = (rt[0] or {})
    return first.get("plain_text") or first.get("text", {}).get("content") or default

# ---------- Fetch tasks from 4 DBs (safe) ----------
def get_all_tasks() -> List[Dict]:
    if not notion:
        logger.error("Notion client is None")
        return []

    dbs = {
        "ops": os.getenv("NOTION_DB_OPS"),
        "tech": os.getenv("NOTION_DB_TECH"),
        "comm": os.getenv("NOTION_DB_COMM"),
        "fin": os.getenv("NOTION_DB_FIN"),
    }

    tasks: List[Dict] = []
    for dept, db_id in dbs.items():
        if not db_id:
            logger.warning(f"Skipping {dept}: missing DB id")
            continue
        try:
            resp = notion.databases.query(database_id=db_id)
            if not isinstance(resp, dict):
                logger.error(f"Unexpected Notion response for {dept}: {type(resp)}")
                continue
            results = resp.get("results") or []
            for page in results:
                try:
                    props = page.get("properties") or {}

                    task = {
                        "department": dept,
                        "task_name": safe_title(props, "Task Name"),
                        "owners": safe_people(props, "Owner"),
                        "status": safe_select(props, "Status"),
                        "due_date": safe_date(props, "Due Date"),
                        # Keep richer fields safely (won't crash if empty)
                        "next_step": safe_rich_text(props, "Next Steps"),
                        "blocker": safe_select(props, "Blocker", default="None"),
                        "impact": safe_rich_text(props, "Impact"),
                        "priority": safe_select(props, "Priority"),
                    }
                    tasks.append(task)
                except Exception as e:
                    logger.error(f"Parse error in {dept} page: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error querying database {db_id}: {e}")
            continue

    logger.info(f"🎯 Total tasks fetched: {len(tasks)}")
    return tasks

# ---------- Optional AI summary ----------
def ai_summary(query: str, tasks: List[Dict]) -> str:
    if not openai_client:
        return ""
    try:
        # compact context for AI
        lines = []
        for t in tasks[:20]:
            owner = ", ".join(t["owners"]) if t["owners"] else "Unassigned"
            lines.append(f"{owner} — {t['task_name']} [{t['status']}] Due: {t['due_date']} Blocker: {t['blocker']}")
        context = "\n".join(lines) if lines else "No tasks."

        sys = (
            "You are Task Intel Bot. Return concise, executive-ready answers. "
            "Prefer bullet points. Call out blockers, due dates, and priorities."
        )
        msg = f"Query: {query}\n\nTasks:\n{context}\n\nAnswer succinctly for a CEO."
        r = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": msg}],
            max_tokens=350,
            temperature=0.2,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return ""

# ---------- Command handling ----------
def find_tasks_by_name(tasks: List[Dict], name_query: str) -> List[Dict]:
    q = (name_query or "").strip().lower()
    if not q:
        return []
    hits = []
    for t in tasks:
        for owner in t.get("owners", []):
            if owner and q in owner.lower():
                hits.append(t)
                break
    return hits

def format_task_line(t: Dict) -> str:
    owner = ", ".join(t["owners"]) if t["owners"] else "Unassigned"
    return f"• {owner} — *{t['task_name']}* ({t['status']}) — Due: {t['due_date']} — Blocker: {t['blocker']}"

def process_slack_command(text: str) -> str:
    logger.info(f"👂 Command: {text}")
    tasks = get_all_tasks()

    if not tasks:
        return "📊 No tasks found yet. Once your Notion databases have tasks, try `/intel brief`."

    lower = (text or "").strip().lower()

    # /intel what <name>
    if lower.startswith("what "):
        name_query = text.strip()[5:]  # keep original casing after "what "
        matches = find_tasks_by_name(tasks, name_query)
        if not matches:
            return f"🤔 No tasks found for *{name_query}*. Tip: owners must be set in the Notion 'Owner' field."
        # optional AI polish
        ai = ai_summary(f"What is {name_query} working on?", matches)
        header = f"👤 *What {name_query} is working on* ({len(matches)} tasks)"
        body = "\n".join(format_task_line(t) for t in matches[:20])
        tail = f"\n\n📈 Total tasks across all departments: {len(tasks)}"
        return f"{header}\n\n{body}{('\n\n' + ai) if ai else ''}{tail}"

    # /intel brief
    if "brief" in lower:
        counts = {
            "Operations": len([t for t in tasks if t["department"] == "ops"]),
            "Tech":       len([t for t in tasks if t["department"] == "tech"]),
            "Commercial": len([t for t in tasks if t["department"] == "comm"]),
            "Finance":    len([t for t in tasks if t["department"] == "fin"]),
        }
        ai = ai_summary("Company brief by department with blockers and priorities.", tasks)
        lines = [f"• {k}: {v} tasks" for k, v in counts.items()]
        msg = f"📊 *Company Brief* — {len(tasks)} total tasks\n" + "\n".join(lines)
        if ai:
            msg += f"\n\n{ai}"
        msg += "\n\nTry `/intel what <name>` for a person-specific view."
        return msg

    # /intel team status (simple demo)
    if "team status" in lower or "status" in lower:
        by_status = {}
        for t in tasks:
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        lines = [f"• {k}: {v}" for k, v in sorted(by_status.items(), key=lambda x: x[0])]
        return f"🧭 *Team Status* — {len(tasks)} tasks\n" + "\n".join(lines)

    # default help
    return (
        f"🤖 *Task Intel Bot* — {len(tasks)} tasks indexed\n"
        "Try:\n"
        "• `/intel brief`\n"
        "• `/intel what <name>` (e.g., `/intel what nishanth`)\n"
        "• `/intel team status`"
    )

# ---------- Slack endpoints ----------
@app.post("/slack/events")
async def slack_events(request: Request):
    try:
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        data = await request.json()
        if "challenge" in data:
            return JSONResponse(content={"challenge": data["challenge"]})
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"Slack events error: {e}")
        return JSONResponse(content={"status": "error"})

@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        form = await request.form()
        text = (form.get("text") or "").strip()
        if not text:
            text = "help"
        reply = process_slack_command(text)
        return JSONResponse(content={"response_type": "in_channel", "text": reply})
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={"text": "⚡ Task Intel Bot error. Try `/intel brief` again."})

# ---------- Health & root ----------
@app.get("/health")
async def health():
    t = get_all_tasks()
    return {
        "status": "healthy",
        "notion_connected": bool(notion),
        "openai_connected": bool(openai_client),
        "tasks_found": len(t),
        "message": "OK",
    }

@app.get("/")
async def root():
    return {"message": "Task Intel Bot — Live"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
