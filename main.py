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

# DEBUG: Get raw property data to see what's actually there
def debug_property(prop, prop_name):
    if not prop:
        return f"{prop_name}: None"
    
    prop_type = prop.get('type', 'unknown')
    if prop_type == 'rich_text':
        rich_text = prop.get('rich_text', [])
        if rich_text:
            return f"{prop_name}: {rich_text[0].get('plain_text', 'Empty')}"
        else:
            return f"{prop_name}: No rich text"
    elif prop_type == 'select':
        select = prop.get('select', {})
        return f"{prop_name}: {select.get('name', 'Not set')}"
    elif prop_type == 'date':
        date_obj = prop.get('date', {})
        return f"{prop_name}: {date_obj.get('start', 'No date')}"
    elif prop_type == 'people':
        people = prop.get('people', [])
        return f"{prop_name}: {len(people)} people"
    else:
        return f"{prop_name}: {prop_type} - {str(prop)[:100]}"

# FIXED: Proper property parsing with debugging
def get_all_tasks() -> List[Dict]:
    if not notion:
        return []
    
    all_tasks = []
    
    try:
        databases = {
            'Operations': os.getenv('NOTION_DB_OPS'),
            'Commercial': os.getenv('NOTION_DB_COMM'),
            'Tech': os.getenv('NOTION_DB_TECH'),
            'Finance': os.getenv('NOTION_DB_FIN')
        }
        
        for dept, db_id in databases.items():
            if not db_id:
                continue
                
            try:
                response = notion.databases.query(database_id=db_id)
                logger.info(f"📊 Found {len(response.get('results', []))} pages in {dept}")
                
                for page in response.get("results", []):
                    try:
                        props = page.get("properties", {})
                        
                        # DEBUG: Log what we're actually getting
                        debug_info = []
                        for prop_name in ['Task Name', 'Status', 'Due Date', 'Next steps', 'Impact', 'Owner']:
                            if prop_name in props:
                                debug_info.append(debug_property(props[prop_name], prop_name))
                        
                        logger.info(f"🔍 Page properties: {' | '.join(debug_info)}")
                        
                        # Task name - FIXED parsing
                        task_name = "Unnamed Task"
                        title_prop = props.get("Task Name", {})
                        if title_prop.get('title'):
                            title_text = title_prop.get('title', [])
                            if title_text:
                                task_name = title_text[0].get('plain_text', 'Unnamed Task')
                        
                        # Status - FIXED parsing with correct emojis
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
                        
                        # Due Date - FIXED parsing
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop.get('date'):
                            raw_date = due_prop['date'].get('start', 'No date')
                            if raw_date != 'No date':
                                try:
                                    date_obj = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                                    due_date = date_obj.strftime("%b %d")
                                except:
                                    due_date = raw_date
                        
                        # Next Step - FIXED rich text parsing
                        next_step = "Not specified"
                        next_prop = props.get("Next steps", {})
                        if next_prop.get('rich_text'):
                            rich_text = next_prop.get('rich_text', [])
                            if rich_text and rich_text[0].get('plain_text'):
                                next_step = rich_text[0]['plain_text']
                        
                        # Impact - FIXED rich text parsing  
                        impact = "Not specified"
                        impact_prop = props.get("Impact", {})
                        if impact_prop.get('rich_text'):
                            rich_text = impact_prop.get('rich_text', [])
                            if rich_text and rich_text[0].get('plain_text'):
                                impact = rich_text[0]['plain_text']
                        
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
                            "next_step": next_step,
                            "impact": impact,
                            "department": dept,
                            "url": page.get("url", "")
                        }
                        all_tasks.append(task)
                        logger.info(f"✅ Parsed task: {task_name} | Status: {status} | Next: {next_step}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error parsing page: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"❌ Error querying {dept} database: {e}")
                continue
                
    except Exception as e:
        logger.error(f"❌ Major error: {e}")
    
    logger.info(f"🎯 Total tasks parsed: {len(all_tasks)}")
    return all_tasks

# User ID to name mapping
USER_ID_MAP = {
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Brazil',
    '24d871d8-0a94-4ef7-b4d5-5d3e550e4f8e': 'Omar', 
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Deema'
}

def get_person_name(owner_ids):
    for owner_id in owner_ids:
        if owner_id in USER_ID_MAP:
            return USER_ID_MAP[owner_id]
    return "Unassigned"

def find_person_tasks(tasks: List[Dict], person_name: str) -> List[Dict]:
    person_tasks = []
    for task in tasks:
        task_owner = get_person_name(task['owner_ids'])
        if person_name.lower() in task_owner.lower():
            person_tasks.append(task)
    person_tasks.sort(key=lambda x: (x['due_date'] == 'No date', x['due_date']))
    return person_tasks

def format_task(task: Dict, index: int = None) -> str:
    task_text = ""
    if index is not None:
        task_text += f"**{index}. {task['task_name']}**\n"
    else:
        task_text += f"**{task['task_name']}**\n"
    
    task_text += f"   {task['status']} — Due: {task['due_date']}\n"
    task_text += f"   → Next: {task['next_step']}\n" 
    task_text += f"   → Impact: {task['impact']}\n"
    return task_text

def generate_ai_response(query: str, tasks: List[Dict]) -> str:
    if not openai_client:
        # Fallback without AI
        query_lower = query.lower()
        
        for person in ['brazil', 'omar', 'deema']:
            if person in query_lower:
                person_tasks = find_person_tasks(tasks, person)
                if person_tasks:
                    response = f"👤 **{person.title()}ʼs Tasks**\n\n"
                    for i, task in enumerate(person_tasks, 1):
                        response += format_task(task, i) + "\n"
                    
                    status_counts = {}
                    for task in person_tasks:
                        status = task['status'].split()[-1]
                        status_counts[status] = status_counts.get(status, 0) + 1
                    
                    status_summary = " • ".join([f"{status}: {count}" for status, count in status_counts.items()])
                    response += f"📊 **Summary:** {len(person_tasks)} tasks ({status_summary})"
                    return response
        
        # Default brief
        dept_counts = {}
        for task in tasks:
            dept = task['department']
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
        
        response = "🏢 **Company Brief**\n\n"
        for dept, count in dept_counts.items():
            response += f"• {dept}: {count} tasks\n"
        
        return response
    
    try:
        task_context = ""
        for i, task in enumerate(tasks[:15], 1):
            owner = get_person_name(task['owner_ids'])
            task_context += f"{i}. {owner}: {task['task_name']} ({task['status']}) - Due: {task['due_date']} - Next: {task['next_step']}\n"
        
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are Task Intel Bot. Provide concise, professional responses about company tasks. Use exact task data provided."},
                {"role": "user", "content": f"Query: {query}\n\nTasks:\n{task_context}\n\nResponse:"}
            ],
            max_tokens=500,
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "I encountered an error. Try: 'What is Brazil working on?' or 'Company status'"

def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    
    if not tasks:
        return "📊 No tasks found in Notion databases."
    
    command_lower = command_text.lower()
    
    if len(command_text) > 10 and not command_text.startswith('/'):
        return generate_ai_response(command_text, tasks)
    
    # Person query
    if any(word in command_lower for word in ['what', 'who', 'working']) and any(name in command_lower for name in ['brazil', 'omar', 'deema']):
        person_name = next((name for name in ['brazil', 'omar', 'deema'] if name in command_lower), None)
        if person_name:
            person_tasks = find_person_tasks(tasks, person_name)
            if person_tasks:
                response = f"👤 **{person_name.title()}ʼs Tasks**\n\n"
                for i, task in enumerate(person_tasks, 1):
                    response += format_task(task, i) + "\n"
                
                status_counts = {}
                for task in person_tasks:
                    status = task['status'].split()[-1]
                    status_counts[status] = status_counts.get(status, 0) + 1
                
                status_summary = " • ".join([f"{status}: {count}" for status, count in status_counts.items()])
                response += f"📊 **Summary:** {len(person_tasks)} tasks ({status_summary})"
                return response
            else:
                return f"👤 No tasks found for {person_name.title()}."
    
    # Brief
    elif any(word in command_lower for word in ['brief', 'overview', 'summary', 'status']):
        dept_counts = {}
        status_counts = {}
        
        for task in tasks:
            dept = task['department']
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            status = task['status'].split()[-1]
            status_counts[status] = status_counts.get(status, 0) + 1
        
        response = "🏢 **Company Brief**\n\n📈 **Overview:**\n"
        for dept, count in dept_counts.items():
            response += f"• {dept}: {count} tasks\n"
        
        response += "\n🔄 **Status:**\n"
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        return response
    
    # Help
    else:
        return ("🤖 **Task Intel Bot**\n\n"
               "**Ask naturally:**\n"
               "• `What is Brazil working on?`\n" 
               "• `Show me overdue tasks`\n"
               "• `Company status`\n\n"
               f"📊 Tracking {len(tasks)} tasks")

# Slack endpoints
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
        return JSONResponse(content={"text": "⚡ Task Intel Bot - Try: 'What is Brazil working on?'"})

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
    tasks = get_all_tasks()
    return {
        "status": "healthy",
        "total_tasks": len(tasks),
        "message": f"Ready - {len(tasks)} tasks"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Fixed Data Parsing"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
