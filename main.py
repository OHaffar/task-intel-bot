from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import logging
from typing import List, Dict
import hmac
import hashlib
import time
from datetime import datetime
from notion_client import Client
from openai import OpenAI

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize clients
notion = None
openai_client = None

if os.getenv('NOTION_TOKEN'):
    try:
        notion = Client(auth=os.getenv('NOTION_TOKEN'))
        logger.info("✅ Notion client connected")
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

# DEBUG: See what's actually in the database
def debug_database_contents():
    if not notion:
        return "Notion not connected"
    
    try:
        databases = {
            'ops': os.getenv('NOTION_DB_OPS'),
            'tech': os.getenv('NOTION_DB_TECH'),
            'comm': os.getenv('NOTION_DB_COMM'),
            'fin': os.getenv('NOTION_DB_FIN')
        }
        
        debug_info = []
        
        for dept, db_id in databases.items():
            if not db_id:
                continue
                
            try:
                response = notion.databases.query(database_id=db_id)
                tasks_in_dept = []
                
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    
                    # Task name
                    task_name = "Unnamed"
                    title_prop = props.get("Task Name", {})
                    if title_prop:
                        title_text = title_prop.get("title", [])
                        if title_text:
                            task_name = title_text[0].get("plain_text", "Unnamed")[:30]
                    
                    # Check if Owner field exists and has data
                    owner_prop = props.get("Owner", {})
                    has_owner_field = bool(owner_prop)
                    owner_count = len(owner_prop.get("people", [])) if owner_prop else 0
                    
                    tasks_in_dept.append({
                        "name": task_name,
                        "has_owner_field": has_owner_field,
                        "owner_count": owner_count
                    })
                
                debug_info.append(f"{dept}: {len(tasks_in_dept)} tasks, {sum(t['owner_count'] for t in tasks_in_dept)} owners")
                
            except Exception as e:
                debug_info.append(f"{dept}: ERROR - {str(e)}")
        
        return " | ".join(debug_info)
        
    except Exception as e:
        return f"DEBUG ERROR: {str(e)}"

# Simple task getter for now
def get_all_tasks() -> List[Dict]:
    if not notion:
        return []
    
    all_tasks = []
    
    try:
        databases = {
            'ops': os.getenv('NOTION_DB_OPS'),
            'tech': os.getenv('NOTION_DB_TECH'),
            'comm': os.getenv('NOTION_DB_COMM'),
            'fin': os.getenv('NOTION_DB_FIN')
        }
        
        for dept, db_id in databases.items():
            if not db_id:
                continue
                
            try:
                response = notion.databases.query(database_id=db_id)
                
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    
                    # Task name
                    task_name = "Unnamed Task"
                    title_prop = props.get("Task Name", {})
                    if title_prop:
                        title_text = title_prop.get("title", [])
                        if title_text:
                            task_name = title_text[0].get("plain_text", "Unnamed Task")
                    
                    # Owners
                    owners = []
                    owner_prop = props.get("Owner", {})
                    if owner_prop:
                        people = owner_prop.get("people", [])
                        for person in people:
                            if person and person.get("name"):
                                owners.append(person.get("name"))
                    
                    task = {
                        "task_name": task_name,
                        "owners": owners,
                        "department": dept
                    }
                    all_tasks.append(task)
                    
            except Exception as e:
                continue
                
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return all_tasks

# Process commands with DEBUG info
def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    debug_info = debug_database_contents()
    
    # DEBUG RESPONSE - show what's actually happening
    response = f"🔍 *DEBUG MODE* 🔍\n\n"
    response += f"**Database Info:** {debug_info}\n\n"
    response += f"**Tasks Found:** {len(tasks)}\n"
    
    # Count tasks with owners
    tasks_with_owners = [t for t in tasks if t.get('owners')]
    response += f"**Tasks with Owners:** {len(tasks_with_owners)}\n\n"
    
    # Show sample of what we found
    if tasks:
        response += "**Sample Tasks:**\n"
        for i, task in enumerate(tasks[:3]):
            owners = task.get('owners', [])
            response += f"{i+1}. {task['task_name'][:40]}... | Owners: {len(owners)} | Dept: {task['department']}\n"
    
    response += f"\n💡 **Issue:** The Owner field might be empty in your Notion database."
    response += f"\n🔧 **Fix:** Assign people to the 'Owner' field in Notion tasks."
    
    return response

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
            "text": "⚡ Debug mode error"
        })

# Health check
@app.get("/health")
async def health_check():
    tasks = get_all_tasks()
    debug_info = debug_database_contents()
    return {
        "status": "healthy",
        "tasks_found": len(tasks),
        "debug_info": debug_info
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Debug Mode"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
