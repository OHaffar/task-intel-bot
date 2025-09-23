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

if os.getenv('OPENAI_API_KEY'):
    try:
        openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        logger.info("✅ OpenAI client connected")
    except Exception as e:
        logger.error(f"OpenAI error: {e}")

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

# FIXED: Proper owner parsing for multiple owners
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
                    try:
                        props = page.get("properties", {})
                        
                        # Task name
                        task_name = "Unnamed Task"
                        title_prop = props.get("Task Name", {})
                        if title_prop and title_prop.get("title"):
                            title_text = title_prop.get("title", [])
                            if title_text:
                                task_name = title_text[0].get("plain_text", "Unnamed Task")
                        
                        # FIXED: Proper owner extraction for multiple owners
                        owners = []
                        owner_prop = props.get("Owner", {})
                        if owner_prop and owner_prop.get("people"):
                            people_list = owner_prop.get("people", [])
                            for person in people_list:
                                if person and isinstance(person, dict) and person.get("name"):
                                    owners.append(person.get("name"))
                        
                        # Status
                        status = "Not set"
                        status_prop = props.get("Status", {})
                        if status_prop and status_prop.get("select"):
                            status_select = status_prop.get("select", {})
                            status = status_select.get("name", "Not set")
                        
                        # Due Date
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop and due_prop.get("date"):
                            date_obj = due_prop.get("date", {})
                            due_date = date_obj.get("start", "No date")
                        
                        task = {
                            "task_name": task_name,
                            "owners": owners,  # Now properly handles multiple owners
                            "status": status,
                            "due_date": due_date,
                            "department": dept,
                            "url": page.get("url", "")
                        }
                        all_tasks.append(task)
                        
                    except Exception as e:
                        logger.error(f"Error parsing task: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error querying {dept} database: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Major error: {e}")
    
    logger.info(f"🎯 Total tasks found: {len(all_tasks)}")
    logger.info(f"👥 Total owners found: {sum(len(t['owners']) for t in all_tasks)}")
    
    return all_tasks

# FLEXIBLE person search
def find_person_tasks(tasks: List[Dict], query: str) -> List[Dict]:
    person_tasks = []
    query_lower = query.lower()
    
    for task in tasks:
        for owner in task.get("owners", []):
            if owner and query_lower in owner.lower():
                person_tasks.append(task)
                break
    
    return person_tasks

# Get all unique owner names
def get_all_owner_names(tasks: List[Dict]) -> List[str]:
    all_names = set()
    for task in tasks:
        for owner in task.get("owners", []):
            if owner:
                all_names.add(owner)
    return sorted(list(all_names))

# Process commands
def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    
    if not tasks:
        return "📊 No tasks found in Notion databases."
    
    command_lower = command_text.lower()
    total_owners = sum(len(t['owners']) for t in tasks)
    
    # Show database status first
    status_msg = f"📊 *Database Status:* {len(tasks)} tasks, {total_owners} owner assignments\n\n"
    
    # Person query
    if any(word in command_lower for word in ['what', 'who', 'working', 'task', 'brazil', 'omar', 'sarah', 'deema']):
        # Try to find person
        possible_names = ['brazil', 'omar', 'sarah', 'deema']
        found_person = None
        
        for name in possible_names:
            if name in command_lower:
                found_person = name
                break
        
        if found_person:
            person_tasks = find_person_tasks(tasks, found_person)
            all_owners = get_all_owner_names(tasks)
            
            if person_tasks:
                response = status_msg + f"👤 *{found_person.title()}'s Tasks:*\n\n"
                for task in person_tasks:
                    owners_str = ", ".join(task["owners"]) if task["owners"] else "Unassigned"
                    response += f"• **{task['task_name']}**\n"
                    response += f"  Status: {task['status']} | Due: {task['due_date']}\n"
                    response += f"  Owners: {owners_str}\n\n"
                return response
            else:
                return (status_msg + 
                       f"🤔 No tasks found for '{found_person.title()}'.\n\n" +
                       f"**Available people:** {', '.join(all_owners[:10])}")
    
    # Brief/overview
    elif any(word in command_lower for word in ['brief', 'overview', 'summary', 'status']):
        dept_counts = {}
        for task in tasks:
            dept = task.get('department', 'unknown')
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
        
        response = status_msg + f"🏢 *Company Brief:*\n\n"
        for dept, count in dept_counts.items():
            response += f"• {dept.title()}: {count} tasks\n"
        
        all_owners = get_all_owner_names(tasks)
        if all_owners:
            response += f"\n👥 **People with tasks:** {', '.join(all_owners[:8])}"
        
        return response
    
    # Help/default
    else:
        all_owners = get_all_owner_names(tasks)
        return (status_msg +
               "🤖 *Available Commands:*\n\n" +
               "• `/intel what [name]` - Tasks for any person\n" +
               "• `/intel brief` - Company overview\n" +
               "• `/intel [question]` - Natural language\n\n" +
               f"👥 **Team:** {', '.join(all_owners[:6])}...")

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
        
        if not command_text:
            command_text = "brief"
            
        response_text = process_slack_command(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Use /intel what [name] or /intel brief"
        })

# Health check
@app.get("/health")
async def health_check():
    tasks = get_all_tasks()
    total_owners = sum(len(t['owners']) for t in tasks)
    return {
        "status": "healthy",
        "tasks_found": len(tasks),
        "owners_found": total_owners,
        "message": f"Ready - {len(tasks)} tasks, {total_owners} owner assignments"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Fixed Owner Parsing"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
