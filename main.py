from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import logging
from typing import List, Dict
import hmac
import hashlib
import time
from datetime import datetime
import urllib.parse
from notion_client import Client

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize Notion client
notion = None
if os.getenv('NOTION_TOKEN'):
    try:
        notion = Client(auth=os.getenv('NOTION_TOKEN'))
    except Exception as e:
        logger.error(f"Notion client error: {e}")

# Slack signature verification
def verify_slack_signature(request: Request, body: bytes) -> bool:
    slack_signing_secret = os.getenv('SLACK_SIGNING_SECRET', '')
    if not slack_signing_secret:
        return True
    
    timestamp = request.headers.get('X-Slack-Request-Timestamp', '')
    slack_signature = request.headers.get('X-Slack-Signature', '')
    
    if abs(time.time() - float(timestamp)) > 60 * 5:
        return False
    
    sig_basestring = f"v0:{timestamp}:".encode() + body
    my_signature = 'v0=' + hmac.new(
        slack_signing_secret.encode(),
        sig_basestring,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(my_signature, slack_signature)

# Get real tasks from Notion
def get_tasks_from_notion(database_id: str) -> List[Dict]:
    if not notion or not database_id:
        return []
    
    try:
        response = notion.databases.query(database_id=database_id)
        tasks = []
        for page in response.get("results", []):
            props = page.get("properties", {})
            task = {
                "id": page.get("id"),
                "url": page.get("url"),
                "task_name": get_property_text(props.get("Task Name", {})),
                "owner": get_property_people(props.get("Owner", {})),
                "status": get_property_select(props.get("Status", {})),
                "due_date": get_property_date(props.get("Due Date", {})),
                "next_step": get_property_text(props.get("Next Step", {})),
                "blocker": get_property_select(props.get("Blocker", {})),
                "impact": get_property_text(props.get("Impact", {})),
                "priority": get_property_select(props.get("Priority", {})),
            }
            tasks.append(task)
        return tasks
    except Exception as e:
        logger.error(f"Notion query error: {e}")
        return []

# Notion property helpers
def get_property_text(prop: Dict) -> str:
    return " ".join([text.get("plain_text", "") for text in prop.get("rich_text", [])])

def get_property_people(prop: Dict) -> List[str]:
    return [person.get("name", "Unknown") for person in prop.get("people", [])]

def get_property_select(prop: Dict) -> str:
    return prop.get("select", {}).get("name", "Not set")

def get_property_date(prop: Dict) -> str:
    date_obj = prop.get("date")
    return date_obj.get("start", "No due date") if date_obj else "No due date"

# Process Slack commands with REAL data
def process_slack_command(command_text: str) -> str:
    command_text = command_text.lower().strip()
    
    # Get real data from all databases
    all_tasks = []
    for db_key in ['NOTION_DB_OPS', 'NOTION_DB_TECH', 'NOTION_DB_COMM', 'NOTION_DB_FIN']:
        db_id = os.getenv(db_key)
        if db_id:
            all_tasks.extend(get_tasks_from_notion(db_id))
    
    # If no real data, use demo fallback
    if not all_tasks:
        return "🤖 *Task Intel Bot* - Real data coming soon! Currently setting up Notion connection."
    
    # Person query
    if 'what' in command_text or 'working' in command_text:
        for task in all_tasks:
            owners = [owner.lower() for owner in task.get("owner", [])]
            if any(owner in command_text for owner in ['omar', 'sarah', 'deema', 'brazil']):
                response = f"*{task['owner'][0] if task['owner'] else 'Unassigned'}'s Tasks:*\n\n"
                response += f"• *{task['task_name']}* ({task['status']})\n"
                response += f"  Next: {task['next_step'] or 'Not specified'}\n"
                response += f"  Due: {task['due_date']} | Blocker: {task['blocker']}\n"
                response += f"  Impact: {task['impact'] or 'Not specified'}\n"
                return response
        return "I found tasks but couldn't match that person. Try: Check your Notion data."
    
    # Team/brief queries
    elif 'team' in command_text or 'brief' in command_text:
        response = f"*Real Task Data - {datetime.now().strftime('%d %b %Y')}*\n\n"
        response += f"*Found {len(all_tasks)} tasks across all departments:*\n\n"
        
        for i, task in enumerate(all_tasks[:5], 1):  # Show first 5 tasks
            owner = task['owner'][0] if task['owner'] else 'Unassigned'
            response += f"{i}. *{owner}* → {task['task_name']}\n"
            response += f"   Status: {task['status']} | Due: {task['due_date']}\n\n"
        
        return response
    
    # Help
    else:
        return "*Task Intel Bot - Real Data Mode* 🤖\n\n" + \
               "• `/intel what [person]` - Real tasks from Notion\n" + \
               "• `/intel team` - Department overview\n" + \
               "• `/intel brief` - Company brief\n\n" + \
               "*Status:* Connected to Notion ✅"

# Slack endpoints
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
        
        form_data = await request.form()
        command_text = form_data.get("text", "").strip()
        
        response_text = process_slack_command(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "text": "🚨 Error: Check environment variables and Notion connection"
        })

# Health check
@app.get("/health")
async def health_check():
    status = "healthy" if notion else "degraded"
    message = "Connected to Notion" if notion else "Notion not connected"
    return {"status": status, "message": message}

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - REAL DATA MODE"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
