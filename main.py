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

# Get tasks from Notion
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
                        
                        # Status
                        status = "Not set"
                        status_prop = props.get("Status", {})
                        if status_prop:
                            status_select = status_prop.get("select", {})
                            if status_select:
                                status = status_select.get("name", "Not set")
                        
                        # Due Date
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop:
                            date_obj = due_prop.get("date", {})
                            if date_obj:
                                due_date = date_obj.get("start", "No date")
                        
                        task = {
                            "task_name": task_name,
                            "owners": owners,
                            "status": status,
                            "due_date": due_date,
                            "department": dept
                        }
                        all_tasks.append(task)
                        
                    except Exception as e:
                        continue
                        
            except Exception as e:
                continue
                
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return all_tasks

# SUPER FLEXIBLE person search - finds ANY format!
def find_person_tasks(tasks: List[Dict], query: str) -> List[Dict]:
    person_tasks = []
    query_lower = query.lower()
    
    for task in tasks:
        if task.get("owners"):
            for owner in task["owners"]:
                if owner:
                    owner_lower = owner.lower()
                    
                    # MULTIPLE SEARCH STRATEGIES:
                    # 1. Exact match (Brazil = Brazil)
                    # 2. Partial match (Brazil = Brazil Silva)
                    # 3. First name match (Brazil = Brazil)
                    # 4. Email match (Brazil = brazil@company.com)
                    # 5. Any word match (Brazil = Mr. Brazil Consultant)
                    
                    if (query_lower == owner_lower or  # Exact match
                        query_lower in owner_lower or  # Partial match
                        any(word == query_lower for word in owner_lower.split()) or  # Word match
                        query_lower in owner_lower.replace('.', '').replace('@', '')):  # Email/name variants
                        
                        person_tasks.append(task)
                        break
    
    return person_tasks

# Get all unique owner names for debugging
def get_all_owner_names(tasks: List[Dict]) -> List[str]:
    all_names = set()
    for task in tasks:
        if task.get("owners"):
            for owner in task["owners"]:
                if owner:
                    all_names.add(owner)
    return sorted(list(all_names))

# Process commands with FLEXIBLE search
def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    
    if not tasks:
        return "📊 No tasks found. Check your Notion database setup."
    
    command_lower = command_text.lower()
    
    # AI-powered response if available
    if openai_client and len(command_text) > 5:
        try:
            tasks_text = "\n".join([f"- {t['task_name']} ({t['status']})" for t in tasks[:8]])
            
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful task assistant. Be concise and professional."},
                    {"role": "user", "content": f"Query: {command_text}\nAvailable tasks: {tasks_text}\nResponse:"}
                ],
                max_tokens=300,
                temperature=0.3
            )
            
            ai_response = response.choices[0].message.content
            return f"{ai_response}\n\n📈 Based on {len(tasks)} tasks in Notion"
            
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            # Fall through to basic response
    
    # PERSON QUERY - SUPER FLEXIBLE
    if any(word in command_lower for word in ['what', 'who', 'working', 'task']):
        # Try to extract person name using multiple strategies
        possible_names = ['brazil', 'omar', 'sarah', 'deema']  # Add more as needed
        
        found_person = None
        for name in possible_names:
            if name in command_lower:
                found_person = name
                break
        
        # If no specific name found, try to extract from query
        if not found_person and len(command_lower) > 10:
            words = command_lower.split()
            for word in words:
                if len(word) > 3 and word not in ['what', 'who', 'working', 'tasks', 'about']:
                    found_person = word
                    break
        
        if found_person:
            person_tasks = find_person_tasks(tasks, found_person)
            all_owners = get_all_owner_names(tasks)
            
            if person_tasks:
                response = f"👤 *{found_person.title()}'s Tasks:*\n\n"
                for task in person_tasks:
                    owners_str = ", ".join(task["owners"]) if task["owners"] else "Unassigned"
                    response += f"• {task['task_name']} ({task['status']}) - Due: {task['due_date']}\n"
                return response + f"\n📊 Found {len(person_tasks)} tasks for {found_person.title()}"
            else:
                # Helpful debug info
                return (f"🤔 No tasks found for '{found_person}'. " 
                       f"But I found {len(tasks)} tasks total with owners like: {', '.join(all_owners[:5])}...")
    
    # Brief/overview
    elif any(word in command_lower for word in ['brief', 'overview', 'summary', 'status']):
        dept_counts = {}
        for task in tasks:
            dept = task.get('department', 'unknown')
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
        
        response = f"🏢 *Company Brief* ({len(tasks)} total tasks)\n\n"
        for dept, count in dept_counts.items():
            response += f"• {dept.title()}: {count} tasks\n"
        
        all_owners = get_all_owner_names(tasks)
        response += f"\n👥 People with tasks: {', '.join(all_owners[:8])}"
        
        return response
    
    # Help/default
    else:
        all_owners = get_all_owner_names(tasks)
        return ("🤖 *Task Intel Bot* - Available Commands:\n\n" + 
               "• `/intel what [name]` - Tasks for any person\n" + 
               "• `/intel brief` - Company overview\n" + 
               "• `/intel [any question]` - Natural language\n\n" + 
               f"📊 Tracking {len(tasks)} tasks | 👥 {len(all_owners)} people: {', '.join(all_owners[:6])}...")

# Slack endpoints (same as before)
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
            command_text = "help"
            
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
    all_owners = get_all_owner_names(tasks)
    return {
        "status": "healthy",
        "tasks_found": len(tasks),
        "people_found": len(all_owners),
        "message": f"Ready - {len(tasks)} tasks, {len(all_owners)} people"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Flexible Search"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
