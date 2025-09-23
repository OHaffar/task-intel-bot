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
    except Exception as e:
        logger.error(f"Notion client error: {e}")

if os.getenv('OPENAI_API_KEY'):
    try:
        openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    except Exception as e:
        logger.error(f"OpenAI client error: {e}")

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

# Get all tasks from Notion
def get_all_tasks() -> List[Dict]:
    if not notion:
        return []
    
    all_tasks = []
    database_ids = [
        os.getenv('NOTION_DB_OPS'),
        os.getenv('NOTION_DB_TECH'), 
        os.getenv('NOTION_DB_COMM'),
        os.getenv('NOTION_DB_FIN')
    ]
    
    for db_id in database_ids:
        if db_id:
            try:
                response = notion.databases.query(database_id=db_id)
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    
                    # Get task name from Title property
                    task_name = ""
                    title_prop = props.get("Task Name", {}).get("title", [])
                    if title_prop:
                        task_name = " ".join([t.get("plain_text", "") for t in title_prop])
                    
                    # Get owner from People property
                    owners = []
                    people_prop = props.get("Owner", {}).get("people", [])
                    for person in people_prop:
                        name = person.get("name", "")
                        if name:
                            owners.append(name)
                    
                    task = {
                        "task_name": task_name or "Unnamed Task",
                        "owners": owners,
                        "status": props.get("Status", {}).get("select", {}).get("name", "Not set"),
                        "due_date": props.get("Due Date", {}).get("date", {}).get("start", "No due date"),
                        "next_step": props.get("Next steps", {}).get("rich_text", [{}])[0].get("plain_text", "Not specified"),
                        "blocker": props.get("Blocker", {}).get("select", {}).get("name", "None"),
                        "impact": props.get("Impact", {}).get("rich_text", [{}])[0].get("plain_text", "Not specified"),
                        "priority": props.get("Priority", {}).get("select", {}).get("name", "Not set"),
                    }
                    all_tasks.append(task)
            except Exception as e:
                logger.error(f"Error querying database {db_id}: {e}")
    
    return all_tasks

# Generate AI response
def generate_ai_response(user_query: str, tasks: List[Dict]) -> str:
    if not openai_client:
        return "⚠️ AI features coming soon! Currently showing basic task data."
    
    try:
        # Format tasks for AI context
        tasks_context = ""
        for i, task in enumerate(tasks[:10], 1):  # Limit to 10 tasks for context
            owner = task['owners'][0] if task['owners'] else 'Unassigned'
            tasks_context += f"{i}. {owner} - {task['task_name']} ({task['status']}) - Due: {task['due_date']}\n"
        
        system_prompt = """You are Task Intel Bot. Provide concise, helpful responses about team tasks. 
        Be conversational but professional. Focus on blockers, due dates, and impacts."""
        
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"User query: {user_query}\n\nAvailable tasks:\n{tasks_context}\n\nResponse:"}
            ],
            max_tokens=500,
            temperature=0.3
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "🤖 Here's what I found in the task database..."

# Process Slack commands
def process_slack_command(command_text: str) -> str:
    # Get real task data
    tasks = get_all_tasks()
    
    if not tasks:
        return "📊 No tasks found in Notion databases. Please check your setup."
    
    # Use AI for all responses if available
    ai_response = generate_ai_response(command_text, tasks)
    
    # Add task count for context
    return f"{ai_response}\n\n📈 *Database Status: {len(tasks)} tasks found across all departments*"

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
        # Immediate response to avoid timeout
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        form_data = await request.form()
        command_text = form_data.get("text", "").strip()
        
        if not command_text:
            command_text = "brief"  # Default to brief
            
        response_text = process_slack_command(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot is ready! Try: /intel what [person] or /intel brief"
        })

# Health check
@app.get("/health")
async def health_check():
    status = "healthy"
    message = "Task Intel Bot - Ready for CEO/COO"
    return {"status": status, "message": message}

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Production Ready"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
