from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import JSONResponse, HTMLResponse
import os
import logging
import asyncio
import aiohttp
from datetime import datetime, date, timedelta
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
        "mode": "conversational"
    }

async def understand_ceo_query(query: str) -> Dict:
    """Understand what the CEO/COO is asking in natural language"""
    if not query:
        return {"intent": "company_update", "tone": "confident"}
    
    query_lower = query.lower()
    
    # Check if asking about a specific person
    for person_key, person_name in TEAM_MEMBERS.items():
        if person_key in query_lower:
            return {
                "intent": "person_update", 
                "person": person_name,
                "tone": "supportive"
            }
    
    # Check for specific types of updates
    if any(word in query_lower for word in ['how are we', 'how things', 'company', 'brief', 'overview']):
        return {"intent": "company_update", "tone": "confident"}
    
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem']):
        return {"intent": "blockers_update", "tone": "concerned"}
    
    if any(word in query_lower for word in ['priority', 'important', 'critical']):
        return {"intent": "priorities_update", "tone": "focused"}
    
    if any(word in query_lower for word in ['tech', 'engineering', 'commercial', 'operations', 'finance']):
        dept = next((d for d in ['tech', 'commercial', 'operations', 'finance'] if d in query_lower), 'company')
        return {"intent": "department_update", "department": dept.title(), "tone": "informative"}
    
    # Default to company update
    return {"intent": "company_update", "tone": "confident"}

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
                    task = parse_task_naturally(page, dept)
                    if task:
                        tasks.append(task)
                        
            except Exception as e:
                logger.error(f"Error fetching {dept}: {e}")
    
    cache[cache_key] = tasks
    return tasks

def parse_task_naturally(page: Dict, department: str) -> Optional[Dict]:
    """Parse task in a way that enables natural conversation"""
    try:
        props = page.get('properties', {})
        
        # Get task name
        name = get_property(props, 'Task Name', 'title')
        if not name or name == 'No name':
            return None
        
        # Get owner (simplified - show whatever Notion provides)
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            name = person.get('name') or f"user_{person.get('id', '')[-6:]}"
            owners.append(name)
        
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        
        return {
            'name': name,
            'owners': owners,
            'status': get_property(props, 'Status', 'select'),
            'due_date': due_date_raw.split('T')[0] if due_date_raw else 'Not scheduled',
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

def generate_conversational_response(tasks: List[Dict], analysis: Dict, original_query: str) -> str:
    """Generate a human-like response instead of robot columns"""
    intent = analysis['intent']
    tone = analysis.get('tone', 'neutral')
    
    if intent == 'person_update':
        person = analysis['person']
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        
        if not person_tasks:
            return f"👤 *{person}* doesn't have any tasks assigned right now. They might be focusing on ad-hoc work or their tasks are completed."
        
        # Group by status for natural reporting
        in_progress = [t for t in person_tasks if t['status'] == 'In progress']
        todo = [t for t in person_tasks if t['status'] == 'To Do']
        done = [t for t in person_tasks if t['status'] == 'Done']
        
        response = f"👤 *Here's what {person} is working on:*\n\n"
        
        if in_progress:
            response += f"*In Progress ({len(in_progress)}):*\n"
            for task in in_progress[:3]:
                response += f"• {task['name']}"
                if task['due_date'] != 'Not scheduled':
                    response += f" (due {task['due_date']})"
                if task['blocker'] not in ['None', 'Not set']:
                    response += f" - ⚠️ {task['blocker']} blocker"
                response += "\n"
            response += "\n"
        
        if todo:
            response += f"*Up Next ({len(todo)}):*\n"
            for task in todo[:2]:
                response += f"• {task['name']}"
                if task['due_date'] != 'Not scheduled':
                    response += f" (starts {task['due_date']})"
                response += "\n"
            response += "\n"
        
        response += f"{person} has {len(person_tasks)} total tasks across {len(set(t['department'] for t in person_tasks))} departments."
        return response
    
    elif intent == 'company_update':
        total_tasks = len(tasks)
        in_progress = len([t for t in tasks if t['status'] == 'In progress'])
        blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
        
        response = "🏢 *Company Update*\n\n"
        response += f"We have *{total_tasks} active tasks* across the company.\n\n"
        
        response += f"• *{in_progress} in progress* right now\n"
        response += f"• *{blocked} currently blocked* and need attention\n"
        response += f"• *{len([t for t in tasks if t['priority'] == 'High'])} high-priority items*\n\n"
        
        # Mention any critical blockers
        major_blockers = [t for t in tasks if t['blocker'] == 'Major']
        if major_blockers:
            response += "🚨 *Critical items needing attention:*\n"
            for task in major_blockers[:2]:
                response += f"• {task['name']} ({task['department']})\n"
        
        return response
    
    elif intent == 'blockers_update':
        blocked_tasks = [t for t in tasks if t['blocker'] not in ['None', 'Not set']]
        
        if not blocked_tasks:
            return "✅ *No major blockers right now!* Everything seems to be moving smoothly across all departments."
        
        response = "⚠️ *Here are the current blockers:*\n\n"
        
        major_blockers = [t for t in blocked_tasks if t['blocker'] == 'Major']
        minor_blockers = [t for t in blocked_tasks if t['blocker'] == 'Minor']
        
        if major_blockers:
            response += "🚨 *Major Blockers:*\n"
            for task in major_blockers[:3]:
                owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
                response += f"• {task['name']} ({owners}) - {task['department']}\n"
            response += "\n"
        
        if minor_blockers:
            response += "🔸 *Minor Issues:*\n"
            for task in minor_blockers[:2]:
                response += f"• {task['name']} - {task['department']}\n"
        
        return response
    
    elif intent == 'priorities_update':
        high_priority = [t for t in tasks if t['priority'] == 'High']
        
        if not high_priority:
            return "📋 *No high-priority tasks right now.* The team is focused on regular work items."
        
        response = "🎯 *High-Priority Focus Items:*\n\n"
        
        for i, task in enumerate(high_priority[:5], 1):
            owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
            response += f"*{i}. {task['name']}*\n"
            response += f"   {owners} | {task['department']} | Due: {task['due_date']}\n"
            
            if task['blocker'] not in ['None', 'Not set']:
                response += f"   ⚠️ {task['blocker']} blocker\n"
            
            response += "\n"
        
        return response
    
    else:  # department_update or fallback
        dept = analysis.get('department', 'All')
        dept_tasks = [t for t in tasks if t['department'] == dept] if dept != 'All' else tasks
        
        response = f"📊 *{dept} Department Update*\n\n"
        response += f"*{len(dept_tasks)} active tasks* in progress.\n\n"
        
        status_counts = {}
        for task in dept_tasks:
            status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
        
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        return response

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands with conversational responses"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        # Immediate response
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Let me check on that for you..."
        }
        
        # Process in background
        if response_url:
            background_tasks.add_task(process_conversational_query, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Hmm, I'm having trouble understanding that. Try asking about a team member or company status."
        })

async def process_conversational_query(query: str, response_url: str):
    """Process query and respond conversationally"""
    try:
        # Understand what the CEO is asking
        analysis = await understand_ceo_query(query)
        
        # Get current tasks
        tasks = await get_all_tasks()
        
        if not tasks:
            response = "📭 I don't see any tasks in the system right now. The team might be between projects or the connection needs checking."
        else:
            # Generate natural response
            response = generate_conversational_response(tasks, analysis, query)
        
        # Send response
        await send_slack_response(response_url, {"response_type": "in_channel", "text": response})
        
    except Exception as e:
        logger.error(f"Conversation error: {e}")
        error_msg = "❌ Sorry, I'm having trouble pulling the latest updates. Try again in a moment."
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
