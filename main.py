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

# ULTRA-FAST task fetching with minimal processing
def get_all_tasks_fast() -> List[Dict]:
    if not notion:
        return []
    
    all_tasks = []
    cache_duration = 60  # Cache for 60 seconds
    cache_key = "tasks_cache"
    cache_time_key = "tasks_cache_time"
    
    # Simple in-memory cache to avoid repeated Notion calls
    if hasattr(get_all_tasks_fast, cache_key) and hasattr(get_all_tasks_fast, cache_time_key):
        cache_age = time.time() - getattr(get_all_tasks_fast, cache_time_key)
        if cache_age < cache_duration:
            return getattr(get_all_tasks_fast, cache_key)
    
    try:
        # Only query active databases
        databases = {
            'Operations': os.getenv('NOTION_DB_OPS'),
            'Commercial': os.getenv('NOTION_DB_COMM')
        }
        
        for dept, db_id in databases.items():
            if not db_id:
                continue
                
            try:
                # Fast query with minimal properties
                response = notion.databases.query(
                    database_id=db_id,
                    page_size=50  # Limit results for speed
                )
                
                for page in response.get("results", []):
                    try:
                        props = page.get("properties", {})
                        
                        # FAST parsing - minimal processing
                        task_name = "Unnamed Task"
                        title_prop = props.get("Task Name", {})
                        if title_prop.get('title'):
                            title_text = title_prop.get('title', [])
                            if title_text:
                                task_name = title_text[0].get('plain_text', 'Unnamed Task')[:100]  # Limit length
                        
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
                        
                        # Due Date (fast parsing)
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop.get('date'):
                            raw_date = due_prop['date'].get('start', 'No date')
                            if raw_date != 'No date':
                                due_date = raw_date
                        
                        # Owner IDs (fast)
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
                logger.error(f"Error querying {dept}: {e}")
                continue
                
        # Cache the results
        setattr(get_all_tasks_fast, cache_key, all_tasks)
        setattr(get_all_tasks_fast, cache_time_key, time.time())
                
    except Exception as e:
        logger.error(f"Major error: {e}")
    
    return all_tasks

# COMPLETE USER ID MAPPING FOR YOUR ENTIRE TEAM
USER_ID_MAP = {
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Brazil',
    '24d871d8-0a94-4ef7-b4d5-5d3e550e4f8e': 'Omar', 
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Deema',
    # Add these based on your actual user IDs from Notion:
    'user_id_derrick_1': 'Derrick',
    'user_id_chethan_1': 'Chethan', 
    'user_id_nishanth_1': 'Nishanth',
    'user_id_derrick_2': 'Derrick',  # If multiple Derricks
    'user_id_bhavya_1': 'Bhavya'
}

def get_person_name(owner_ids):
    for owner_id in owner_ids:
        if owner_id in USER_ID_MAP:
            return USER_ID_MAP[owner_id]
    return "Unassigned"

# FAST person search
def find_person_tasks_fast(tasks: List[Dict], person_name: str) -> List[Dict]:
    person_tasks = []
    person_lower = person_name.lower()
    
    for task in tasks:
        task_owner = get_person_name(task['owner_ids'])
        if person_lower in task_owner.lower():
            person_tasks.append(task)
    
    # Fast sort by due date
    person_tasks.sort(key=lambda x: (x['due_date'] == 'No date', x['due_date']))
    return person_tasks

# FAST command processing - NO TIMEOUTS
def process_slack_command_fast(command_text: str) -> str:
    # IMMEDIATE response to show we're working
    initial_response = "⏳ Fetching latest task data..."
    
    try:
        tasks = get_all_tasks_fast()
        
        if not tasks:
            return "📊 No tasks found in Notion databases."
        
        command_lower = command_text.lower()
        
        # PERSON QUERY - FAST AND EFFICIENT
        team_members = ['brazil', 'omar', 'deema', 'derrick', 'chethan', 'nishanth', 'bhavya']
        found_person = None
        
        for person in team_members:
            if person in command_lower:
                found_person = person
                break
        
        if found_person:
            person_tasks = find_person_tasks_fast(tasks, found_person)
            
            if person_tasks:
                response = f"👤 **{found_person.title()}ʼs Tasks**\n\n"
                
                # Limit to 8 tasks for speed
                for i, task in enumerate(person_tasks[:8], 1):
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
        
        # BRIEF/OVERVIEW - FAST
        elif any(word in command_lower for word in ['brief', 'overview', 'summary', 'status']):
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
            
            response += "\n🔄 **Status Overview:**\n"
            for status, count in status_counts.items():
                response += f"• {status}: {count} tasks\n"
            
            # Show team members with tasks
            team_with_tasks = set()
            for task in tasks:
                owner = get_person_name(task['owner_ids'])
                if owner != "Unassigned":
                    team_with_tasks.add(owner)
            
            if team_with_tasks:
                response += f"\n👥 **Active Team:** {', '.join(sorted(team_with_tasks))}"
            
            return response
        
        # HELP
        else:
            return ("🤖 **Task Intel Bot**\n\n"
                   "**Ask about any team member:**\n"
                   "• `What is Brazil working on?`\n"
                   "• `Show me Omar's tasks`\n" 
                   "• `Deema's current projects`\n"
                   "• `Derrick's assignments`\n"
                   "• `Chethan's workload`\n"
                   "• `Nishanth's tasks`\n"
                   "• `Bhavya's projects`\n\n"
                   "**Or get overviews:**\n"
                   "• `Company brief`\n"
                   "• `Team status`\n\n"
                   f"📊 Tracking {len(tasks)} tasks across the company")
    
    except Exception as e:
        logger.error(f"Processing error: {e}")
        return "⚡ Task Intel Bot is ready! Try: 'What is Brazil working on?' or 'Company status'"

# ULTRA-FAST Slack endpoint
@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        # Immediate response to avoid timeout
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        form_data = await request.form()
        command_text = form_data.get("text", "").strip()
        
        if not command_text:
            command_text = "help"
        
        # FAST processing - minimal operations
        response_text = process_slack_command_fast(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Use: 'What is [person] working on?' or 'Company status'"
        })

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

@app.get("/health")
async def health_check():
    tasks = get_all_tasks_fast()
    return {
        "status": "healthy",
        "total_tasks": len(tasks),
        "message": f"Ready - {len(tasks)} tasks, 8 team members supported"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Fast & Company Wide"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
