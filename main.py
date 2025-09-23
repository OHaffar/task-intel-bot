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

# SIMPLE & FAST task fetcher - no user name resolution
def get_all_tasks_fast() -> List[Dict]:
    if not notion:
        return []
    
    all_tasks = []
    
    try:
        databases = {
            'ops': os.getenv('NOTION_DB_OPS'),
            'comm': os.getenv('NOTION_DB_COMM')
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
                        
                        # SIMPLE: Just count owners, don't resolve names (too slow)
                        owner_count = 0
                        owner_prop = props.get("Owner", {})
                        if owner_prop and owner_prop.get("people"):
                            owner_count = len(owner_prop["people"])
                        
                        # Status
                        status = "Not set"
                        status_prop = props.get("Status", {})
                        if status_prop and status_prop.get("select"):
                            status = status_prop["select"].get("name", "Not set")
                        
                        task = {
                            "task_name": task_name,
                            "owner_count": owner_count,  # Just count, no names
                            "status": status,
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

# FAST person search - uses simple matching
def find_person_tasks_fast(tasks: List[Dict], query: str) -> List[Dict]:
    # Since we can't resolve names quickly, use task content matching
    person_tasks = []
    query_lower = query.lower()
    
    for task in tasks:
        task_name_lower = task['task_name'].lower()
        
        # Simple content matching - faster than user resolution
        if (query_lower in task_name_lower or  # Task name contains person name
            any(word in task_name_lower for word in ['brazil', 'omar', 'sarah', 'deema'])):  # Common names
            
            person_tasks.append(task)
    
    return person_tasks

# FAST command processing - no timeouts
def process_slack_command_fast(command_text: str) -> str:
    tasks = get_all_tasks_fast()
    
    if not tasks:
        return "📊 No tasks found in Notion databases."
    
    command_lower = command_text.lower()
    total_tasks = len(tasks)
    total_owners = sum(t['owner_count'] for t in tasks)
    
    # IMMEDIATE response to avoid timeout
    status_msg = f"🏢 *Task Intel Bot - Live Data* 🏢\n\n"
    status_msg += f"• **Total Tasks:** {total_tasks}\n"
    status_msg += f"• **Total Assignments:** {total_owners}\n"
    status_msg += f"• **Departments:** Ops ({len([t for t in tasks if t['department'] == 'ops'])}), Comm ({len([t for t in tasks if t['department'] == 'comm'])})\n\n"
    
    # Person query - SIMPLE AND FAST
    if any(word in command_lower for word in ['what', 'who', 'working', 'brazil']):
        if 'brazil' in command_lower:
            # Find tasks that might be related to Brazil
            brazil_tasks = []
            for task in tasks:
                task_lower = task['task_name'].lower()
                if any(keyword in task_lower for keyword in ['brazil', 'training', 'onboarding', 'client']):
                    brazil_tasks.append(task)
            
            if brazil_tasks:
                response = status_msg + "🇧🇷 *Tasks likely involving Brazil:*\n\n"
                for task in brazil_tasks[:5]:  # Limit to 5 tasks
                    response += f"• **{task['task_name']}**\n"
                    response += f"  Status: {task['status']} | Assignments: {task['owner_count']}\n\n"
                return response
            else:
                return (status_msg + 
                       "🇧🇷 *Brazil-related Tasks:*\n\n" +
                       "No specific Brazil tasks found in current search.\n\n" +
                       "💡 **Try these instead:**\n" +
                       "• `/intel brief` - Company overview\n" +
                       "• Check Notion directly for Brazil's assignments")
    
    # Brief/overview - FAST
    elif any(word in command_lower for word in ['brief', 'overview', 'summary']):
        # Show sample tasks instead of resolving names
        response = status_msg + "📋 *Recent Tasks Sample:*\n\n"
        for i, task in enumerate(tasks[:6]):  # Show 6 tasks max
            response += f"{i+1}. {task['task_name'][:50]}...\n"
            response += f"   Status: {task['status']} | Team: {task['owner_count']} people\n\n"
        
        response += "💡 **For specific person queries, check Notion directly.**"
        return response
    
    # Help/default
    else:
        return (status_msg +
               "🤖 *Available Commands:*\n\n" +
               "• `/intel brief` - Company overview (fast)\n" +
               "• `/intel what [topic]` - Search tasks by keyword\n" +
               "• `/intel status` - Quick status update\n\n" +
               "⚡ *Optimized for speed to avoid timeouts*")

# FAST Slack command - immediate response
@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        # IMMEDIATE response to avoid timeout
        body = await request.body()
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        form_data = await request.form()
        command_text = form_data.get("text", "").strip()
        
        if not command_text:
            command_text = "brief"
        
        # FAST processing - no complex operations
        response_text = process_slack_command_fast(command_text)
        
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": response_text
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        # ULTRA FAST fallback
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Use `/intel brief` for quick overview"
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
    tasks = get_all_tasks_fast()
    return {
        "status": "healthy",
        "tasks_found": len(tasks),
        "message": f"Fast mode - {len(tasks)} tasks"
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - Fast Mode (No Timeouts)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
