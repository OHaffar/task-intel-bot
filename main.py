from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import JSONResponse
import os
import logging
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional
import cachetools
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize cache
cache = cachetools.TTLCache(maxsize=100, ttl=30)

# Database configuration
DATABASES = {
    'Operations': os.getenv('NOTION_DB_OPS', ''),
    'Commercial': os.getenv('NOTION_DB_COMM', ''),
    'Tech': os.getenv('NOTION_DB_TECH', ''),
    'Finance': os.getenv('NOTION_DB_FIN', '')
}

# MANUAL USER ID MAPPING
USER_ID_TO_NAME = {
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Brazil',
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Nishanth',
    'ff3909f8-9fa8-4013-9d12-c1e86f8ebffe': 'Chethan',
    'ec6410cf-b2cb-4ea8-8539-fb973e00a028': 'Derrick',
    'f9776ebc-9f9c-4bc1-89de-903114a4107a': 'Deema',
    '24d871d8-8afe-498b-a434-e2609bb1789d': 'Omar',
    'beadea32-bdbc-4a49-be45-5096886c493a': 'Bhavya'
}

# Team member names for natural conversation
TEAM_MEMBERS = {
    'omar': 'Omar',
    'derrick': 'Derrick', 
    'bhavya': 'Bhavya',
    'nishanth': 'Nishanth',
    'chethan': 'Chethan',
    'deema': 'Deema',
    'brazil': 'Brazil'
}

# Initialize Notion client
notion = None
try:
    from notion_client import Client
    notion_token = os.getenv('NOTION_TOKEN')
    if notion_token:
        notion = Client(auth=notion_token, timeout_ms=10000)
        logger.info("Notion client initialized")
except Exception as e:
    logger.error(f"Notion init failed: {e}")

@app.get("/")
async def home():
    return {"status": "ready", "service": "Conversational Task Intel"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy", 
        "timestamp": datetime.utcnow().isoformat(),
        "team_members": len(TEAM_MEMBERS)
    }

async def understand_query(query: str) -> Dict:
    """Understand natural language queries"""
    if not query:
        return {"intent": "company_update"}
    
    query_lower = query.lower()
    
    # Check for team members
    for person_key, person_name in TEAM_MEMBERS.items():
        if person_key in query_lower:
            return {"intent": "person_update", "person": person_name}
    
    # Check for other intents
    if any(word in query_lower for word in ['brief', 'overview', 'company', 'status']):
        return {"intent": "company_update"}
    
    if any(word in query_lower for word in ['block', 'stuck', 'issue']):
        return {"intent": "blockers_update"}
    
    if any(word in query_lower for word in ['priority', 'important']):
        return {"intent": "priorities_update"}
    
    if any(word in query_lower for word in ['tech', 'engineering']):
        return {"intent": "department_update", "department": "Tech"}
    elif any(word in query_lower for word in ['commercial', 'sales']):
        return {"intent": "department_update", "department": "Commercial"}
    elif any(word in query_lower for word in ['operations', 'ops']):
        return {"intent": "department_update", "department": "Operations"}
    elif any(word in query_lower for word in ['finance']):
        return {"intent": "department_update", "department": "Finance"}
    
    return {"intent": "company_update"}

async def get_all_tasks() -> List[Dict]:
    """Get all tasks with caching"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        return tasks
    
    # Fetch all databases
    for dept, db_id in DATABASES.items():
        if db_id:
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: notion.databases.query(database_id=db_id, page_size=100)
                )
                
                for page in result.get('results', []):
                    task = parse_task(page, dept)
                    if task:
                        tasks.append(task)
                        
            except Exception as e:
                logger.error(f"Error fetching {dept}: {e}")
    
    cache[cache_key] = tasks
    return tasks

def parse_task(page: Dict, department: str) -> Optional[Dict]:
    """Parse task using manual user ID mapping"""
    try:
        props = page.get('properties', {})
        
        # Get task name
        name = get_property(props, 'Task Name', 'title')
        if not name or name == 'No name':
            return None
        
        # Convert user IDs to names using our mapping
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id and user_id in USER_ID_TO_NAME:
                owners.append(USER_ID_TO_NAME[user_id])
            elif person.get('name'):
                owners.append(person.get('name'))
            elif user_id:
                owners.append(f"user_{user_id[-6:]}")
        
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        
        return {
            'name': name,
            'owners': owners,
            'status': get_property(props, 'Status', 'select'),
            'due_date': due_date_raw.split('T')[0] if due_date_raw else 'No date',
            'next_step': get_property(props, 'Next Steps', 'rich_text'),
            'blocker': get_property(props, 'Blocker', 'select'),
            'impact': get_property(props, 'Impact', 'rich_text'),
            'priority': get_property(props, 'Priority', 'select'),
            'department': department,
        }
        
    except Exception as e:
        logger.error(f"Error parsing task: {e}")
        return None

def get_property(props, field_name: str, field_type: str) -> str:
    """Extract property value from Notion"""
    field = props.get(field_name, {})
    
    if field_type == 'title':
        titles = field.get('title', [])
        return titles[0].get('plain_text', '') if titles else ''
    elif field_type == 'select':
        select = field.get('select', {})
        return select.get('name', 'Not set')
    elif field_type == 'date':
        date_obj = field.get('date', {})
        return date_obj.get('start', 'No date')
    elif field_type == 'rich_text':
        rich_text = field.get('rich_text', [])
        return rich_text[0].get('plain_text', '') if rich_text else ''
    
    return ''

def generate_response(tasks: List[Dict], analysis: Dict) -> str:
    """Generate conversational response"""
    intent = analysis['intent']
    
    if intent == 'person_update':
        person = analysis['person']
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        
        if not person_tasks:
            return f"👤 *{person}* doesn't have any tasks assigned right now."
        
        in_progress = [t for t in person_tasks if t['status'] == 'In progress']
        todo = [t for t in person_tasks if t['status'] == 'To Do']
        
        response = f"👤 *Here's what {person} is working on:*\n\n"
        
        if in_progress:
            response += f"*In Progress ({len(in_progress)}):*\n"
            for task in in_progress[:3]:
                response += f"• {task['name']}"
                if task['due_date'] != 'No date':
                    response += f" (due {task['due_date']})"
                if task['blocker'] not in ['None', 'Not set']:
                    response += f" - ⚠️ {task['blocker']} blocker"
                response += "\n"
        
        if todo:
            response += f"\n*Up Next ({len(todo)}):*\n"
            for task in todo[:2]:
                response += f"• {task['name']}\n"
        
        return response
    
    elif intent == 'company_update':
        total_tasks = len(tasks)
        in_progress = len([t for t in tasks if t['status'] == 'In progress'])
        blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
        
        response = "🏢 *Company Update*\n\n"
        response += f"*{total_tasks} active tasks* across the company:\n"
        response += f"• {in_progress} in progress\n"
        response += f"• {blocked} currently blocked\n"
        
        return response
    
    elif intent == 'blockers_update':
        blocked_tasks = [t for t in tasks if t['blocker'] not in ['None', 'Not set']]
        
        if not blocked_tasks:
            return "✅ *No blockers right now!* Everything is moving smoothly."
        
        response = "⚠️ *Current Blockers:*\n\n"
        for task in blocked_tasks[:5]:
            owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
            response += f"• {task['name']} ({owners}) - {task['department']}\n"
        
        return response
    
    elif intent == 'priorities_update':
        high_priority = [t for t in tasks if t['priority'] == 'High']
        
        if not high_priority:
            return "📋 *No high-priority tasks right now.*"
        
        response = "🎯 *High-Priority Items:*\n\n"
        for task in high_priority[:5]:
            owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
            response += f"• {task['name']} ({owners})\n"
        
        return response
    
    else:  # department_update
        dept = analysis.get('department', 'All')
        dept_tasks = [t for t in tasks if t['department'] == dept] if dept != 'All' else tasks
        
        response = f"📊 *{dept} Department:* {len(dept_tasks)} tasks\n"
        
        status_counts = {}
        for task in dept_tasks:
            status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
        
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        return response

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        # Immediate response
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Checking tasks..."
        }
        
        # Process in background
        if response_url:
            background_tasks.add_task(process_query, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Error processing command"
        })

async def process_query(query: str, response_url: str):
    """Process query in background"""
    try:
        analysis = await understand_query(query)
        tasks = await get_all_tasks()
        
        if not tasks:
            response = "📭 No tasks found in the system."
        else:
            response = generate_response(tasks, analysis)
        
        payload = {"response_type": "in_channel", "text": response}
        await send_slack_response(response_url, payload)
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        error_msg = "❌ Sorry, I'm having trouble right now."
        await send_slack_response(response_url, {"response_type": "in_channel", "text": error_msg})

async def send_slack_response(response_url: str, payload: Dict):
    """Send response to Slack"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(response_url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Slack response failed: {await resp.text()}")
    except Exception as e:
        logger.error(f"Failed to send to Slack: {e}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
