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

# Simple storage for demo - we'll replace with real Notion later
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
    },
    {
        "owner": ["Sarah"],
        "task_name": "Database Migration",
        "status": "Blocked",
        "due_date": "2025-09-18",
        "next_step": "Wait for vendor API access",
        "blocker": "Major",
        "impact": "Essential for scaling customer data storage",
        "priority": "P0"
    },
    {
        "owner": ["Deema"],
        "task_name": "Social Media Campaign",
        "status": "To-Do",
        "due_date": "2025-09-25",
        "next_step": "Create content calendar",
        "blocker": "None",
        "impact": "Increases brand awareness and engagement",
        "priority": "P2"
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

@app.get("/team")
async def team_query(team: str):
    """Team brief - currently using demo data"""
    try:
        # For demo, return all tasks as if they're from the requested team
        response = f"# {team.title()} Brief - {datetime.now().strftime('%d %b %Y')}\n\n"
        response += "## Top Risks\n"
        response += "• Sarah → Database Migration (Major blocker) • Due: 18 Sep 2025\n\n"
        response += "## People Likely to Slip\n"
        response += "• Sarah → Database Migration (vendor delay)\n\n"
        response += "## Quick Wins\n"
        response += "• Omar → Website Redesign (ready for design review)\n\n"
        response += "## Team Snapshot\n"
        response += "To-Do: 1 • In-Progress: 1 • Blocked: 1 • Done: 0\n\n"
        response += "## Notable Changes\n"
        response += "• Omar updated Website Redesign today"
        
        return {"response": response, "status": "success"}
    
    except Exception as e:
        logger.error(f"Error in /team: {e}")
        return {"response": f"Sorry, couldn't generate {team} brief", "status": "error"}

@app.get("/brief")
async def brief_query():
    """Company brief - currently using demo data"""
    try:
        response = f"# Company Brief - {datetime.now().strftime('%d %b %Y')}\n\n"
        response += "## Top Risks\n"
        response += "• Sarah → Database Migration (Major blocker) • Due: 18 Sep 2025\n\n"
        response += "## People Likely to Slip\n"
        response += "• Sarah → Database Migration (waiting on vendor)\n\n"
        response += "## Quick Wins\n"
        response += "• Omar → Website Redesign (design phase almost complete)\n"
        response += "• Deema → Social Media Campaign (ready to start)\n\n"
        response += "## Team Snapshot\n"
        response += "Total: 3 • To-Do: 1 • In-Progress: 1 • Blocked: 1 • Done: 0\n\n"
        response += "## Notable Changes\n"
        response += "• Omar updated Website Redesign status\n"
        response += "• Sarah reported vendor delay for Database Migration"
        
        return {"response": response, "status": "success"}
    
    except Exception as e:
        logger.error(f"Error in /brief: {e}")
        return {"response": "Sorry, couldn't generate company brief", "status": "error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)