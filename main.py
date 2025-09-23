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

# Get all tasks with proper formatting
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
                        
                        # Owner IDs
                        owner_ids = []
                        owner_prop = props.get("Owner", {})
                        if owner_prop and owner_prop.get("people"):
                            for person in owner_prop["people"]:
                                if person and person.get("id"):
                                    owner_ids.append(person["id"])
                        
                        # Status with CORRECT emojis
                        status = "Not set"
                        status_prop = props.get("Status", {})
                        if status_prop and status_prop.get("select"):
                            status_raw = status_prop["select"].get("name", "Not set")
                            status_emoji = {
                                "In Progress": "🔵",  # BLUE for In Progress
                                "To-Do": "🟡",        # Yellow for To-Do
                                "Blocked": "🔴",      # Red for Blocked
                                "Done": "✅"          # Green check for Done
                            }.get(status_raw, "⚪")
                            status = f"{status_emoji} {status_raw}"
                        
                        # Due Date
                        due_date = "No date"
                        due_prop = props.get("Due Date", {})
                        if due_prop and due_prop.get("date"):
                            raw_date = due_prop["date"].get("start", "No date")
                            if raw_date != "No date":
                                try:
                                    date_obj = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
                                    due_date = date_obj.strftime("%b %d")
                                except:
                                    due_date = raw_date
                        
                        # Next Step
                        next_step = "Not specified"
                        next_prop = props.get("Next steps", {})
                        if next_prop and next_prop.get("rich_text"):
                            rich_text = next_prop.get("rich_text", [])
                            if rich_text:
                                next_step = rich_text[0].get("plain_text", "Not specified")
                        
                        # Impact
                        impact = "Not specified"
                        impact_prop = props.get("Impact", {})
                        if impact_prop and impact_prop.get("rich_text"):
                            rich_text = impact_prop.get("rich_text", [])
                            if rich_text:
                                impact = rich_text[0].get("plain_text", "Not specified")
                        
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
                        
                    except Exception as e:
                        continue
                        
            except Exception as e:
                continue
                
    except Exception as e:
        logger.error(f"Error: {e}")
    
    return all_tasks

# User ID to name mapping (ONLY REAL PEOPLE)
USER_ID_MAP = {
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Brazil',
    '24d871d8-0a94-4ef7-b4d5-5d3e550e4f8e': 'Omar',
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Deema',
    'ff3909f8-9fa8-4013-9d12-c1e86f8ebffe': 'Team Member'  # Generic for others
}

def get_person_name(owner_ids):
    for owner_id in owner_ids:
        if owner_id in USER_ID_MAP:
            return USER_ID_MAP[owner_id]
    return "Unassigned"

# Find tasks by person
def find_person_tasks(tasks: List[Dict], person_name: str) -> List[Dict]:
    person_tasks = []
    
    for task in tasks:
        task_owner = get_person_name(task['owner_ids'])
        if person_name.lower() in task_owner.lower():
            person_tasks.append(task)
    
    # Sort by due date (soonest first)
    person_tasks.sort(key=lambda x: (x['due_date'] == 'No date', x['due_date']))
    return person_tasks

# Format task for display
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

# AI-powered response for natural language
def generate_ai_response(query: str, tasks: List[Dict]) -> str:
    if not openai_client:
        # Fallback to basic response without AI
        query_lower = query.lower()
        
        if 'brazil' in query_lower:
            person_tasks = find_person_tasks(tasks, 'brazil')
            if person_tasks:
                response = f"👤 **Brazilʼs Tasks**\n\n"
                for i, task in enumerate(person_tasks, 1):
                    response += format_task(task, i) + "\n"
                response += f"📊 **Summary:** {len(person_tasks)} tasks assigned to Brazil"
                return response
        
        elif 'omar' in query_lower:
            person_tasks = find_person_tasks(tasks, 'omar')
            if person_tasks:
                response = f"👤 **Omarʼs Tasks**\n\n"
                for i, task in enumerate(person_tasks, 1):
                    response += format_task(task, i) + "\n"
                response += f"📊 **Summary:** {len(person_tasks)} tasks assigned to Omar"
                return response
        
        elif 'deema' in query_lower:
            person_tasks = find_person_tasks(tasks, 'deema')
            if person_tasks:
                response = f"👤 **Deemaʼs Tasks**\n\n"
                for i, task in enumerate(person_tasks, 1):
                    response += format_task(task, i) + "\n"
                response += f"📊 **Summary:** {len(person_tasks)} tasks assigned to Deema"
                return response
        
        # Default brief for other queries
        dept_counts = {}
        for task in tasks:
            dept = task['department']
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
        
        response = "🏢 **Company Brief**\n\n"
        for dept, count in dept_counts.items():
            response += f"• {dept}: {count} tasks\n"
        
        return response + f"\n💡 Try: 'What is Brazil working on?' or 'Omar's tasks'"
    
    try:
        # Prepare task context
        task_context = ""
        for i, task in enumerate(tasks[:15], 1):
            owner = get_person_name(task['owner_ids'])
            task_context += f"{i}. {owner}: {task['task_name']} ({task['status']}) - Due: {task['due_date']}\n"
        
        system_prompt = """You are Task Intel Bot. Provide concise, professional responses about company tasks.
        Format responses with clear sections, emojis, and bullet points. Be helpful but brief.
        Focus on due dates, status, and impacts. Use the exact task data provided."""
        
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Query: {query}\n\nAvailable Tasks:\n{task_context}\n\nResponse:"}
            ],
            max_tokens=500,
            temperature=0.3
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "I encountered an error. Try a specific command like 'What is Brazil working on?' or 'Company status'."

# Process all types of queries
def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    
    if not tasks:
        return "📊 No tasks found in Notion databases. Please check your setup."
    
    command_lower = command_text.lower()
    
    # Use AI for natural language queries
    if len(command_text) > 10 and not command_text.startswith('/'):
        return generate_ai_response(command_text, tasks)
    
    # Specific person query
    if any(word in command_lower for word in ['what', 'who', 'working']) and any(name in command_lower for name in ['brazil', 'omar', 'deema']):
        person_name = None
        for name in ['brazil', 'omar', 'deema']:  # ONLY REAL PEOPLE
            if name in command_lower:
                person_name = name
                break
        
        if person_name:
            person_tasks = find_person_tasks(tasks, person_name)
            
            if person_tasks:
                response = f"👤 **{person_name.title()}ʼs Tasks**\n\n"
                
                for i, task in enumerate(person_tasks, 1):
                    response += format_task(task, i)
                    response += "\n"
                
                dept_tasks = {}
                for task in person_tasks:
                    dept = task['department']
                    dept_tasks[dept] = dept_tasks.get(dept, 0) + 1
                
                dept_summary = " • ".join([f"{dept}: {count}" for dept, count in dept_tasks.items()])
                response += f"📊 **Summary:** {len(person_tasks)} tasks assigned to {person_name.title()} ({dept_summary})"
                
                return response
            else:
                return f"👤 No tasks found for {person_name.title()}. Available people: Brazil, Omar, Deema"
    
    # Brief/overview
    elif any(word in command_lower for word in ['brief', 'overview', 'summary', 'status']):
        dept_counts = {}
        status_counts = {}
        
        for task in tasks:
            dept = task['department']
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
            
            status_clean = task['status'].split()[-1]  # Get status without emoji
            status_counts[status_clean] = status_counts.get(status_clean, 0) + 1
        
        response = "🏢 **Company Brief**\n\n"
        response += "📈 **Overview:**\n"
        for dept, count in dept_counts.items():
            response += f"• {dept}: {count} tasks\n"
        
        response += "\n🔄 **Status:**\n"
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        # Show urgent tasks (due soon or blocked)
        urgent_tasks = [t for t in tasks if 'Blocked' in t['status'] or 'In Progress' in t['status']]
        if urgent_tasks:
            response += "\n🚨 **Active Tasks:**\n"
            for task in urgent_tasks[:3]:
                owner = get_person_name(task['owner_ids'])
                response += f"• {owner}: {task['task_name']} - Due: {task['due_date']}\n"
        
        return response
    
    # Help
    else:
        return ("🤖 **Task Intel Bot**\n\n"
               "**Natural Language Queries:**\n"
               "• `What is Brazil working on?`\n"
               "• `Show me Omar's tasks`\n"
               "• `Company status`\n\n"
               "**Available People:** Brazil, Omar, Deema\n\n"
               f"📊 Tracking {len(tasks)} tasks across the company")

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
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Try: 'What is Brazil working on?' or 'Company status'"
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

# Health check
@app.get("/health")
async def health_check():
    tasks = get_all_tasks()
    return {
        "status": "healthy",
        "total_tasks": len(tasks),
        "message": f"Ready - {len(tasks)} tasks, natural language enabled"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Company Wide with Natural Language"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
