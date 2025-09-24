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
import httpx

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize clients
notion = None
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

# Send message to Slack
async def send_slack_message(channel: str, text: str):
    try:
        slack_token = os.getenv('SLACK_BOT_TOKEN')
        if not slack_token:
            return False
            
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={
                    "channel": channel,
                    "text": text,
                    "unfurl_links": False,
                    "unfurl_media": False
                }
            )
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error sending Slack message: {e}")
        return False

# Fast task fetching with caching
def get_all_tasks_fast() -> List[Dict]:
    if not notion:
        return []
    
    # Simple cache to avoid repeated calls
    cache_key = "tasks_cache"
    cache_time_key = "tasks_cache_time"
    cache_duration = 30  # 30 seconds
    
    if hasattr(get_all_tasks_fast, cache_key) and hasattr(get_all_tasks_fast, cache_time_key):
        cache_age = time.time() - getattr(get_all_tasks_fast, cache_time_key)
        if cache_age < cache_duration:
            return getattr(get_all_tasks_fast, cache_key)
    
    all_tasks = []
    
    try:
        databases = {
            'Operations': os.getenv('NOTION_DB_OPS'),
            'Commercial': os.getenv('NOTION_DB_COMM')
        }
        
        for dept, db_id in databases.items():
            if not db_id:
                continue
                
            try:
                response = notion.databases.query(database_id=db_id, page_size=50)
                
                for page in response.get("results", []):
                    try:
                        props = page.get("properties", {})
                        
                        # Fast parsing
                        task_name = "Unnamed Task"
                        title_prop = props.get("Task Name", {})
                        if title_prop.get('title'):
                            title_text = title_prop.get('title', [])
                            if title_text:
                                task_name = title_text[0].get('plain_text', 'Unnamed Task')[:100]
                        
                        # Status with emoji
                        status = "⚪ Not set"
                        status_prop = props.get("Status", {})
                        if status_prop.get('select'):
                            status_raw = status_prop['select'].get('name', 'Not set')
                            status_emoji = {
                                "In Progress": "🔵",
                                "To-Do": "🟡", 
                                "Blocked": "🔴",
                                "Done": "✅"
                            }.get(status_raw, "⚪")
                            status = f"{status_emoji} {status_raw}"
                        
                        # Due Date
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop.get('date'):
                            raw_date = due_prop['date'].get('start', 'No date')
                            due_date = raw_date
                        
                        # Owner IDs
                        owner_ids = []
                        owner_prop = props.get("Owner", {})
                        if owner_prop.get('people'):
                            for person in owner_prop['people']:
                                if person and person.get('id'):
                                    owner_ids.append(person['id'])
                        
                        task = {
                            "task_name": task_name,
                            "owner_ids": owner_ids,
                            "status": status,
                            "due_date": due_date,
                            "department": dept
                        }
                        all_tasks.append(task)
                        
                    except Exception as e:
                        continue
                        
            except Exception as e:
                continue
                
        # Cache results
        setattr(get_all_tasks_fast, cache_key, all_tasks)
        setattr(get_all_tasks_fast, cache_time_key, time.time())
                
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return all_tasks

# User ID mapping for your entire team
USER_ID_MAP = {
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Brazil',
    '24d871d8-0a94-4ef7-b4d5-5d3e550e4f8e': 'Omar', 
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Deema',
    # Add others as you discover their user IDs
}

def get_person_name(owner_ids):
    for owner_id in owner_ids:
        if owner_id in USER_ID_MAP:
            return USER_ID_MAP[owner_id]
    return "Unassigned"

def find_person_tasks_fast(tasks: List[Dict], person_name: str) -> List[Dict]:
    person_tasks = []
    person_lower = person_name.lower()
    
    for task in tasks:
        task_owner = get_person_name(task['owner_ids'])
        if person_lower in task_owner.lower():
            person_tasks.append(task)
    
    person_tasks.sort(key=lambda x: (x['due_date'] == 'No date', x['due_date']))
    return person_tasks

# Process commands for both slash commands and natural language
def process_query(query_text: str) -> str:
    tasks = get_all_tasks_fast()
    
    if not tasks:
        return "📊 No tasks found in Notion databases."
    
    query_lower = query_text.lower()
    
    # Person query
    team_members = ['brazil', 'omar', 'deema', 'derrick', 'chethan', 'nishanth', 'bhavya']
    found_person = None
    
    for person in team_members:
        if person in query_lower:
            found_person = person
            break
    
    if found_person:
        person_tasks = find_person_tasks_fast(tasks, found_person)
        
        if person_tasks:
            response = f"👤 **{found_person.title()}ʼs Tasks**\n\n"
            
            for i, task in enumerate(person_tasks[:6], 1):
                response += f"**{i}. {task['task_name']}**\n"
                response += f"   {task['status']} — Due: {task['due_date']}\n\n"
            
            status_counts = {}
            for task in person_tasks:
                status_clean = task['status'].split()[-1]
                status_counts[status_clean] = status_counts.get(status_clean, 0) + 1
            
            status_summary = " • ".join([f"{status}: {count}" for status, count in status_counts.items()])
            response += f"📊 **Summary:** {len(person_tasks)} tasks ({status_summary})"
            
            return response
        else:
            return f"👤 No tasks found for {found_person.title()}."
    
    # Brief/overview
    elif any(word in query_lower for word in ['brief', 'overview', 'summary', 'status', 'company', 'team']):
        dept_counts = {}
        status_counts = {}
        
        for task in tasks:
            dept = task['department']
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            status_clean = task['status'].split()[-1]
            status_counts[status_clean] = status_counts.get(status_clean, 0) + 1
        
        response = "🏢 **Company Brief**\n\n"
        response += "📈 **Departments:**\n"
        for dept, count in dept_counts.items():
            response += f"• {dept}: {count} tasks\n"
        
        response += "\n🔄 **Status:**\n"
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        return response
    
    # Help
    else:
        return ("🤖 **Task Intel Bot**\n\n"
               "**Natural Language Examples:**\n"
               "• `What is Brazil working on?`\n"
               "• `Show me company status`\n"
               "• `Omar's tasks`\n"
               "• `Team update`\n\n"
               "**Slash Commands:**\n"
               "• `/intel what brazil`\n"
               "• `/intel brief`\n\n"
               f"📊 Tracking {len(tasks)} tasks")

# SLASH COMMAND endpoint
@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        form_data = await request.form()
        command_text = form_data.get("text", "").strip()
        
        if not command_text:
            command_text = "help"
        
        response_text = process_query(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slash command error: {e}")
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Try: 'What is Brazil working on?' or '/intel brief'"
        })

# EVENT SUBSCRIPTIONS endpoint (for natural language)
@app.post("/slack/events")
async def slack_events(request: Request):
    try:
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        data = await request.json()
        
        # URL verification
        if "challenge" in data:
            return JSONResponse(content={"challenge": data["challenge"]})
        
        # Handle app mentions (@Task Intel Bot)
        if data.get("event", {}).get("type") == "app_mention":
            event = data["event"]
            text = event["text"]
            channel = event["channel"]
            
            # Remove bot mention and process
            query = text.replace("<@", "").replace(">", "").split(" ", 1)[-1].strip()
            response_text = process_query(query)
            
            # Send response back to Slack
            await send_slack_message(channel, response_text)
        
        # Handle direct messages (no @ needed)
        elif data.get("event", {}).get("type") == "message" and not data.get("event", {}).get("bot_id"):
            event = data["event"]
            channel = event["channel"]
            text = event["text"]
            
            # Ignore messages from bots and avoid loops
            if event.get("bot_id") or event.get("subtype"):
                return JSONResponse(content={"status": "ok"})
            
            response_text = process_query(text)
            await send_slack_message(channel, response_text)
        
        return JSONResponse(content={"status": "ok"})
        
    except Exception as e:
        logger.error(f"Slack events error: {e}")
        return JSONResponse(content={"status": "error"})

@app.get("/health")
async def health_check():
    tasks = get_all_tasks_fast()
    return {
        "status": "healthy",
        "total_tasks": len(tasks),
        "message": f"Ready - {len(tasks)} tasks, natural language enabled"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Natural Language + Slash Commands"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
