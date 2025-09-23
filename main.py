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

# DEBUG: See raw Notion data to understand the structure
def debug_raw_notion_data():
    if not notion:
        return "Notion not connected"
    
    try:
        db_id = os.getenv('NOTION_DB_OPS')  # Use ops database for debugging
        if not db_id:
            return "No database ID"
            
        response = notion.databases.query(database_id=db_id)
        first_page = response.get("results", [])[0] if response.get("results") else None
        
        if not first_page:
            return "No pages found"
        
        props = first_page.get("properties", {})
        owner_prop = props.get("Owner", {})
        
        debug_info = {
            "owner_prop_type": owner_prop.get("type", "unknown"),
            "owner_prop_exists": bool(owner_prop),
            "owner_prop_keys": list(owner_prop.keys()) if owner_prop else [],
            "people_value": owner_prop.get("people", []) if owner_prop else []
        }
        
        return f"Owner property: {debug_info}"
        
    except Exception as e:
        return f"Debug error: {str(e)}"

# FIXED: Proper people column parsing
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
                        
                        # FIXED: Proper People column parsing
                        owners = []
                        owner_prop = props.get("Owner", {})
                        
                        # Debug the owner property structure
                        logger.info(f"Owner prop keys: {list(owner_prop.keys()) if owner_prop else 'None'}")
                        
                        if owner_prop and owner_prop.get("type") == "people":
                            people_list = owner_prop.get("people", [])
                            logger.info(f"People list: {people_list}")
                            
                            for person in people_list:
                                if person and isinstance(person, dict):
                                    # Try different possible name fields
                                    name = (person.get("name") or 
                                           person.get("title") or 
                                           person.get("id", "Unknown"))
                                    if name and name != "Unknown":
                                        owners.append(str(name))
                        
                        # If above doesn't work, try alternative approach
                        if not owners and owner_prop:
                            # Try to get any string values from the property
                            people_list = owner_prop.get("people", [])
                            for person in people_list:
                                if person:
                                    # Convert entire person dict to string for debugging
                                    owners.append(str(person)[:50])
                        
                        # Status
                        status = "Not set"
                        status_prop = props.get("Status", {})
                        if status_prop and status_prop.get("select"):
                            status = status_prop["select"].get("name", "Not set")
                        
                        task = {
                            "task_name": task_name,
                            "owners": owners,
                            "status": status,
                            "department": dept,
                            "raw_owner_prop": str(owner_prop)[:100] if owners else "No owners"  # Debug
                        }
                        all_tasks.append(task)
                        
                        logger.info(f"Task: {task_name} | Owners: {owners}")
                        
                    except Exception as e:
                        logger.error(f"Error parsing task: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error querying {dept} database: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Major error: {e}")
    
    total_owners = sum(len(t['owners']) for t in all_tasks)
    logger.info(f"🎯 Total: {len(all_tasks)} tasks, {total_owners} owners")
    
    return all_tasks

# Process commands with detailed debug info
def process_slack_command(command_text: str) -> str:
    tasks = get_all_tasks()
    raw_debug = debug_raw_notion_data()
    total_owners = sum(len(t['owners']) for t in tasks)
    
    # Show detailed debug info
    response = f"🔍 *DEBUG - People Column Parsing* 🔍\n\n"
    response += f"**Raw Notion Structure:** {raw_debug}\n\n"
    response += f"**Tasks Found:** {len(tasks)}\n"
    response += f"**Total Owner Assignments:** {total_owners}\n\n"
    
    if tasks:
        response += "**Sample Tasks with Owners:**\n"
        tasks_with_owners = [t for t in tasks if t['owners']]
        for i, task in enumerate(tasks_with_owners[:3]):
            response += f"{i+1}. {task['task_name'][:30]}... | Owners: {task['owners']}\n"
        
        if not tasks_with_owners:
            response += "No tasks have owners parsed successfully.\n"
            
        response += f"\n**Raw Owner Property Sample:**\n"
        response += f"{tasks[0].get('raw_owner_prop', 'No data')}\n"
    
    response += f"\n💡 **Next Step:** This debug info will show us exactly how to parse the People column."
    
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
            "text": "⚡ Debug error"
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
        "debug": debug_raw_notion_data()
    }

@app.get("/")
async def home():
    return {"message": "Task Intel Bot - People Column Debug"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
