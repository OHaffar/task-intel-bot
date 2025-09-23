from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import os
import logging
from typing import List, Dict
import hmac
import hashlib
import time
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Slack signature verification
def verify_slack_signature(request: Request, body: bytes) -> bool:
    slack_signing_secret = os.getenv('SLACK_SIGNING_SECRET', '')
    if not slack_signing_secret:
        return True  # Skip verification if secret not set yet
    
    timestamp = request.headers.get('X-Slack-Request-Timestamp', '')
    slack_signature = request.headers.get('X-Slack-Signature', '')
    
    # Prevent replay attacks
    if abs(time.time() - float(timestamp)) > 60 * 5:
        return False
    
    sig_basestring = f"v0:{timestamp}:".encode() + body
    my_signature = 'v0=' + hmac.new(
        slack_signing_secret.encode(),
        sig_basestring,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(my_signature, slack_signature)

# Slack events endpoint
@app.post("/slack/events")
async def slack_events(request: Request):
    try:
        body = await request.body()
        
        # Verify Slack signature
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        data = await request.json()
        
        # Slack URL verification challenge
        if "challenge" in data:
            return JSONResponse(content={"challenge": data["challenge"]})
        
        # Handle actual events later
        return JSONResponse(content={"status": "ok"})
        
    except Exception as e:
        logger.error(f"Slack events error: {e}")
        return JSONResponse(content={"status": "error"})

# Slack command endpoint
@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        body = await request.body()
        
        # Verify Slack signature
        if not verify_slack_signature(request, body):
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        # Simple response for now
        return JSONResponse(content={
            "response_type": "in_channel",
            "text": "🤖 Task Intel Bot is working! I'll be smarter soon!"
        })
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={"text": "Sorry, I encountered an error."})

# Demo data and other endpoints remain the same...
demo_tasks = [
    {
        "owner": ["Omar"],
        "task_name": "Website Redesign",
        "status": "In-Progress",
        "due_date": "2025-09-20",
        "next_step": "Finalize color scheme",
        "blocker": "None",
        "impact": "Improves user experience and conversion rates",
        "priority": "P1"
    }
]

@app.get("/")
async def home():
    return {"message": "Task Intel Bot is running! Add /what?owner=Omar to test"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Bot is ready for action!"}

@app.get("/what")
async def what_query(owner: str):
    """Find tasks by owner - currently using demo data"""
    try:
        person_tasks = [task for task in demo_tasks if owner.lower() in [o.lower() for o in task["owner"]]]
        
        if not person_tasks:
            return {"response": f"No tasks found for {owner}", "status": "success"}
        
        response_lines = []
        for task in person_tasks:
            response_lines.append(
                f"{task['owner'][0]} → {task['task_name']}\n"
                f"Next: {task['next_step']} • Due: {task['due_date']} • Blocker: {task['blocker']}\n"
                f"Why it matters: {task['impact']}\n"
            )
        
        return {"response": "\n".join(response_lines), "status": "success"}
    
    except Exception as e:
        logger.error(f"Error in /what: {e}")
        return {"response": f"Sorry, couldn't find tasks for {owner}", "status": "error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
