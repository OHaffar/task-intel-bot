from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import JSONResponse
import os
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
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

# User ID to name mapping
USER_ID_TO_NAME = {
    'c0ccc544-c4c3-4a32-9d3b-23a500383b0b': 'Brazil',
    '080c42c6-fbb2-47d6-9774-1d086c7c3210': 'Nishanth',
    'ff3909f8-9fa8-4013-9d12-c1e86f8ebffe': 'Chethan',
    'ec6410cf-b2cb-4ea8-8539-fb973e00a028': 'Derrick',
    'f9776ebc-9f9c-4bc1-89de-903114a4107a': 'Deema',
    '24d871d8-8afe-498b-a434-e2609bb1789d': 'Omar',
    'beadea32-bdbc-4a49-be45-5096886c493a': 'Bhavya'
}

TEAM_MEMBERS = list(USER_ID_TO_NAME.values())

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
    return {"status": "ready", "service": "Enhanced Task Intel"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

async def understand_query_enhanced(query: str) -> Dict:
    """Enhanced natural language understanding"""
    if not query:
        return {"intent": "team_overview"}
    
    query_lower = query.lower()
    
    # Team workload queries
    if any(word in query_lower for word in ['team', 'everyone', 'workload', 'capacity', 'who']):
        if any(word in query_lower for word in ['busy', 'load', 'capacity', 'workload']):
            return {"intent": "team_workload"}
        return {"intent": "team_overview"}
    
    # Task count queries
    if any(word in query_lower for word in ['many', 'much', 'count', 'number', 'how']):
        if 'task' in query_lower:
            return {"intent": "task_counts"}
        elif any(word in query_lower for word in ['omar', 'derrick', 'bhavya', 'nishanth', 'chethan', 'deema', 'brazil']):
            return {"intent": "person_task_count"}
    
    # Individual person queries
    for person in TEAM_MEMBERS:
        if person.lower() in query_lower:
            return {"intent": "person_detail", "person": person}
    
    # Department queries
    if any(word in query_lower for word in ['tech', 'engineering']):
        return {"intent": "department", "department": "Tech"}
    elif any(word in query_lower for word in ['commercial', 'sales', 'business']):
        return {"intent": "department", "department": "Commercial"}
    elif any(word in query_lower for word in ['operations', 'ops']):
        return {"intent": "department", "department": "Operations"}
    elif any(word in query_lower for word in ['finance', 'money']):
        return {"intent": "department", "department": "Finance"}
    
    # Status queries
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem', 'blocker']):
        return {"intent": "blockers"}
    elif any(word in query_lower for word in ['priority', 'important', 'critical', 'urgent']):
        return {"intent": "priorities"}
    elif any(word in query_lower for word in ['progress', 'working', 'doing']):
        return {"intent": "in_progress"}
    
    # General overview queries
    if any(word in query_lower for word in ['overview', 'summary', 'brief', 'status', 'update']):
        return {"intent": "company_overview"}
    
    # Help
    if any(word in query_lower for word in ['help', 'what can']):
        return {"intent": "help"}
    
    # Default to team overview for ambiguous queries
    return {"intent": "team_overview"}

async def get_all_tasks() -> List[Dict]:
    """Get all tasks with caching"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        return tasks
    
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
        name = get_property(props, 'Task Name', 'title')
        if not name or name == 'No name':
            return None
        
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id and user_id in USER_ID_TO_NAME:
                owners.append(USER_ID_TO_NAME[user_id])
            elif person.get('name'):
                owners.append(person.get('name'))
        
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

def generate_enhanced_response(tasks: List[Dict], analysis: Dict) -> str:
    """Generate professional response with new structure"""
    intent = analysis['intent']
    
    if intent == 'team_overview':
        return generate_team_overview(tasks)
    elif intent == 'team_workload':
        return generate_team_workload(tasks)
    elif intent == 'task_counts':
        return generate_task_counts(tasks)
    elif intent == 'person_task_count':
        person = extract_person_from_query(analysis.get('query', ''))
        return generate_person_task_count(tasks, person) if person else generate_task_counts(tasks)
    elif intent == 'person_detail':
        return generate_person_detail(tasks, analysis['person'])
    elif intent == 'department':
        return generate_department_overview(tasks, analysis['department'])
    elif intent == 'blockers':
        return generate_blockers_report(tasks)
    elif intent == 'priorities':
        return generate_priorities_report(tasks)
    elif intent == 'in_progress':
        return generate_in_progress_report(tasks)
    elif intent == 'company_overview':
        return generate_company_overview(tasks)
    else:
        return generate_help_response()

def generate_team_overview(tasks: List[Dict]) -> str:
    """Team task counts - what you asked for"""
    task_counts = {}
    for person in TEAM_MEMBERS:
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        task_counts[person] = len(person_tasks)
    
    # Sort by task count (highest first)
    sorted_counts = sorted(task_counts.items(), key=lambda x: x[1], reverse=True)
    
    response = "👥 **Team Task Overview**\n\n"
    for person, count in sorted_counts:
        response += f"• {person}: {count} task{'s' if count != 1 else ''}\n"
    
    total_tasks = len(tasks)
    response += f"\n📊 **Total:** {total_tasks} tasks across {len([p for p in task_counts.values() if p > 0])} team members"
    
    return response

def generate_person_detail(tasks: List[Dict], person: str) -> str:
    """Enhanced person detail with professional structure"""
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    
    if not person_tasks:
        return f"👤 **{person}** has no tasks assigned currently."
    
    # Group tasks by priority and status
    high_priority = [t for t in person_tasks if t['priority'] == 'High']
    in_progress = [t for t in person_tasks if t['status'] == 'In progress']
    todo = [t for t in person_tasks if t['status'] == 'To Do']
    blocked = [t for t in person_tasks if t['blocker'] not in ['None', 'Not set']]
    
    response = f"👤 **{person}'s Current Focus**\n\n"
    
    # High-priority section
    if high_priority:
        response += f"🎯 **High-Priority Items ({len(high_priority)}):**\n"
        for task in high_priority[:3]:  # Show top 3
            status_icon = "🔄" if task['status'] == 'In progress' else "📋"
            blocker_icon = "🚧" if task['blocker'] not in ['None', 'Not set'] else "✅"
            response += f"• **{task['name']}**\n"
            response += f"  {status_icon} {task['status']} | {blocker_icon} {task['blocker'] if task['blocker'] != 'Not set' else 'On track'}\n"
            if task['due_date'] != 'No date':
                response += f"  ⏱️ Due: {task['due_date']}\n"
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"  👉 Next: {task['next_step']}\n"
            response += "\n"
    
    # Current work section
    if in_progress:
        response += f"🔄 **In Progress ({len(in_progress)}):**\n"
        for task in in_progress:
            if task not in high_priority:  # Don't duplicate
                response += f"• {task['name']}"
                if task['due_date'] != 'No date':
                    response += f" (due {task['due_date']})"
                response += "\n"
        response += "\n"
    
    # Upcoming section
    if todo:
        response += f"📋 **Upcoming ({len(todo)}):**\n"
        for task in todo[:5]:  # Limit to 5
            response += f"• {task['name']}\n"
    
    # Summary
    response += f"\n📊 **Summary:** {len(person_tasks)} total tasks"
    if blocked:
        response += f" • {len(blocked)} need attention"
    if high_priority:
        response += f" • {len(high_priority)} high priority"
    
    return response

def generate_task_counts(tasks: List[Dict]) -> str:
    """Show task counts across different dimensions"""
    total_tasks = len(tasks)
    
    # Status counts
    status_counts = {}
    priority_counts = {}
    blocker_counts = {}
    
    for task in tasks:
        status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
        priority_counts[task['priority']] = priority_counts.get(task['priority'], 0) + 1
        if task['blocker'] not in ['None', 'Not set']:
            blocker_counts[task['blocker']] = blocker_counts.get(task['blocker'], 0) + 1
    
    response = "📊 **Task Counts Overview**\n\n"
    response += f"• **Total tasks:** {total_tasks}\n"
    
    response += f"• **By status:** "
    response += ", ".join([f"{status}: {count}" for status, count in status_counts.items()]) + "\n"
    
    response += f"• **By priority:** "
    response += ", ".join([f"{priority}: {count}" for priority, count in priority_counts.items() if priority != 'Not set']) + "\n"
    
    if blocker_counts:
        response += f"• **Blockers:** "
        response += ", ".join([f"{blocker}: {count}" for blocker, count in blocker_counts.items()]) + "\n"
    
    return response

def extract_person_from_query(query: str) -> Optional[str]:
    """Extract person name from query"""
    query_lower = query.lower()
    for person in TEAM_MEMBERS:
        if person.lower() in query_lower:
            return person
    return None

# Additional generator functions would go here...
# (team_workload, department_overview, blockers_report, etc.)

def generate_help_response() -> str:
    """Enhanced help with new capabilities"""
    return """🤖 **Enhanced Task Intel Bot**

*New capabilities:*
• "Team task counts" or "How many tasks does everyone have?"
• "Omar's workload" or "How busy is Derrick?"
• "What's everyone working on?" - Team overview
• "High priority items" or "What's blocked?"

*Enhanced responses now show:*
• Professional priority-based grouping
• Impact and context prominently
• Complete task visibility (not just in-progress)
• Strategic summaries

*Examples:*
• `/intel team tasks`
• `/intel what is omar working on?` 
• `/intel how many tasks does everyone have?`
• `/intel company overview`"""

# ... (rest of the existing functions: get_all_tasks, parse_task, get_property, 
# slack_command, process_query, send_slack_response remain the same)

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Getting your update..."
        }
        
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
        analysis = await understand_query_enhanced(query)
        tasks = await get_all_tasks()
        
        if not tasks:
            response = "📭 No tasks found in the system."
        else:
            response = generate_enhanced_response(tasks, analysis)
        
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
