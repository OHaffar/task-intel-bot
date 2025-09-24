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

# Team member keywords for flexible matching
TEAM_KEYWORDS = ['omar', 'derrick', 'bhavya', 'nishanth', 'chethan', 'deema', 'brazil']

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
    return {"status": "ready", "service": "Task Intel Bot"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy", 
        "timestamp": datetime.utcnow().isoformat(),
        "team": TEAM_KEYWORDS
    }

@app.get("/debug/tasks")
async def debug_tasks():
    """Debug endpoint to see actual tasks and all columns"""
    try:
        tasks = await get_all_tasks()
        return {
            "total_tasks": len(tasks),
            "sample_task": tasks[0] if tasks else "No tasks found"
        }
    except Exception as e:
        return {"error": str(e)}

async def analyze_query_smart(query: str) -> Dict:
    """Flexible query analysis - understands natural language"""
    if not query:
        return {"intent": "brief"}
    
    query_lower = query.lower()
    
    # Check for team members first
    for person in TEAM_KEYWORDS:
        if person in query_lower:
            return {"intent": "person_query", "person_name": person.title()}
    
    # Check for status queries
    if any(word in query_lower for word in ['progress', 'working on', 'in progress']):
        return {"intent": "status_query", "status": "In progress"}
    elif any(word in query_lower for word in ['todo', 'to do', 'upcoming']):
        return {"intent": "status_query", "status": "To Do"}
    elif any(word in query_lower for word in ['done', 'complete', 'finished']):
        return {"intent": "status_query", "status": "Done"}
    
    # Check for priority queries
    if any(word in query_lower for word in ['high priority', 'important', 'critical', 'high']):
        return {"intent": "priority_query", "priority": "High"}
    elif any(word in query_lower for word in ['medium priority', 'medium']):
        return {"intent": "priority_query", "priority": "Medium"}
    elif any(word in query_lower for word in ['low priority', 'low']):
        return {"intent": "priority_query", "priority": "Low"}
    
    # Check for blocker queries
    if any(word in query_lower for word in ['blocked', 'blocker', 'stuck', 'major']):
        return {"intent": "blocker_query", "blocker": "Major"}
    elif any(word in query_lower for word in ['minor blocker', 'minor']):
        return {"intent": "blocker_query", "blocker": "Minor"}
    
    # Check for departments
    if any(word in query_lower for word in ['tech', 'engineering']):
        return {"intent": "department_query", "department": "Tech"}
    elif any(word in query_lower for word in ['commercial', 'sales']):
        return {"intent": "department_query", "department": "Commercial"}
    elif any(word in query_lower for word in ['operations', 'ops']):
        return {"intent": "department_query", "department": "Operations"}
    elif any(word in query_lower for word in ['finance']):
        return {"intent": "department_query", "department": "Finance"}
    
    # Check for overview/brief
    if any(word in query_lower for word in ['brief', 'overview', 'company', 'status']):
        return {"intent": "brief"}
    
    # Help
    if any(word in query_lower for word in ['help', 'how to']):
        return {"intent": "help"}
    
    return {"intent": "brief"}

async def fetch_notion_database(db_id: str, dept: str) -> List[Dict]:
    """Fetch tasks from a Notion database"""
    if not notion or not db_id:
        return []
    
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: notion.databases.query(database_id=db_id, page_size=100)
        )
        
        tasks = []
        for page in result.get('results', []):
            task = parse_notion_page(page, dept)
            if task:
                tasks.append(task)
        
        return tasks
        
    except Exception as e:
        logger.error(f"Error fetching {dept}: {e}")
        return []

def parse_notion_page(page: Dict, department: str) -> Optional[Dict]:
    """Parse Notion page - handles all 8 columns correctly"""
    try:
        props = page.get('properties', {})
        owners = []
        
        # Resolve user IDs to names for Owner column
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id:
                try:
                    user_info = notion.users.retrieve(user_id=user_id)
                    actual_name = user_info.get('name')
                    if actual_name:
                        # Match against team keywords
                        name_lower = actual_name.lower()
                        for team_member in TEAM_KEYWORDS:
                            if team_member in name_lower:
                                owners.append(team_member.title())
                                break
                        else:
                            owners.append(actual_name)
                except:
                    owners.append(f"user_{user_id[-6:]}")
        
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        
        # Extract all 8 columns according to your Notion setup
        task = {
            'name': get_property(props, 'Task Name', 'title'),
            'owners': owners,
            'status': get_property(props, 'Status', 'select'),
            'due_date': due_date_raw.split('T')[0] if due_date_raw else 'No date',
            'next_step': get_property(props, 'Next Steps', 'rich_text'),
            'blocker': get_property(props, 'Blocker', 'select'),
            'impact': get_property(props, 'Impact', 'rich_text'),
            'priority': get_property(props, 'Priority', 'select'),
            'department': department,
            'due_date_raw': due_date_raw,
        }
        
        if task['name'] and task['name'] != 'No name':
            return task
        return None
        
    except Exception as e:
        logger.error(f"Error parsing page: {e}")
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

async def get_all_tasks() -> List[Dict]:
    """Get all tasks with caching"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        return tasks
    
    # Fetch all databases in parallel
    fetch_tasks = []
    for dept, db_id in DATABASES.items():
        if db_id:
            fetch_tasks.append(fetch_notion_database(db_id, dept))
    
    if fetch_tasks:
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                tasks.extend(result)
    
    cache[cache_key] = tasks
    return tasks

async def send_slack_response(response_url: str, payload: Dict):
    """Send delayed response to Slack"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(response_url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Slack response failed: {await resp.text()}")
    except Exception as e:
        logger.error(f"Failed to send to Slack: {e}")

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        # Immediate response
        analysis = await analyze_query_smart(query)
        immediate_response = {
            "response_type": "ephemeral",
            "text": "🤖 Gathering your task information..."
        }
        
        # Process in background
        if response_url:
            background_tasks.add_task(process_query_background, query, analysis, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Error processing command"
        })

async def process_query_background(query: str, analysis: Dict, response_url: str):
    """Process query in background"""
    try:
        tasks = await get_all_tasks()
        
        if not tasks:
            payload = {"response_type": "in_channel", "text": "📭 No tasks found in Notion"}
        else:
            # Filter tasks based on analysis
            filtered_tasks = tasks
            intent = analysis['intent']
            
            if intent == 'person_query':
                person = analysis.get('person_name', '').lower()
                filtered_tasks = [t for t in tasks if any(person in owner.lower() for owner in t['owners'])]
            elif intent == 'department_query':
                dept = analysis.get('department')
                filtered_tasks = [t for t in tasks if t['department'] == dept]
            elif intent == 'status_query':
                status = analysis.get('status')
                filtered_tasks = [t for t in tasks if t['status'] == status]
            elif intent == 'priority_query':
                priority = analysis.get('priority')
                filtered_tasks = [t for t in tasks if t['priority'] == priority]
            elif intent == 'blocker_query':
                blocker = analysis.get('blocker')
                filtered_tasks = [t for t in tasks if t['blocker'] == blocker]
            
            # Format response
            if intent == 'help':
                payload = format_help_response(tasks)
            elif intent == 'person_query':
                payload = format_person_response(filtered_tasks, analysis['person_name'])
            elif intent == 'brief':
                payload = format_brief_response(tasks)
            else:
                payload = format_general_response(filtered_tasks, analysis)
        
        await send_slack_response(response_url, payload)
        
    except Exception as e:
        logger.error(f"Background error: {e}")
        error_payload = {"response_type": "in_channel", "text": "❌ Error processing request"}
        await send_slack_response(response_url, error_payload)

def format_person_response(tasks: List[Dict], person_name: str) -> Dict:
    """Format person-specific response with all relevant columns"""
    if not tasks:
        return {"response_type": "in_channel", "text": f"👤 No tasks found for {person_name}"}
    
    response = f"👤 *{person_name}'s Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(tasks[:6], 1):
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Status:_ {task['status']} | _Due:_ {task['due_date']} | _Priority:_ {task['priority']}\n"
        
        # Show blocker if it's not "None"
        if task['blocker'] != 'None' and task['blocker'] != 'Not set':
            response += f"   _Blocker:_ {task['blocker']}\n"
        
        # Show next steps if available
        if task['next_step'] and task['next_step'] != 'Not specified':
            response += f"   _Next:_ {task['next_step'][:100]}...\n"
        
        response += f"   _Dept:_ {task['department']}\n\n"
    
    return {"response_type": "in_channel", "text": response}

def format_brief_response(tasks: List[Dict]) -> Dict:
    """Format company brief showing overview of all columns"""
    dept_counts = {}
    status_counts = {}
    priority_counts = {}
    blocker_counts = {}
    
    for task in tasks:
        dept_counts[task['department']] = dept_counts.get(task['department'], 0) + 1
        status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
        priority_counts[task['priority']] = priority_counts.get(task['priority'], 0) + 1
        blocker_counts[task['blocker']] = blocker_counts.get(task['blocker'], 0) + 1
    
    response = "🏢 *Company Brief*\n\n"
    response += "📈 *By Department:*\n"
    for dept, count in dept_counts.items():
        response += f"• {dept}: {count} tasks\n"
    
    response += "\n🔄 *By Status:*\n"
    for status, count in status_counts.items():
        response += f"• {status}: {count} tasks\n"
    
    response += "\n🎯 *By Priority:*\n"
    for priority, count in priority_counts.items():
        response += f"• {priority}: {count} tasks\n"
    
    response += f"\n📊 *Total:* {len(tasks)} tasks"
    return {"response_type": "in_channel", "text": response}

def format_help_response(tasks: List[Dict]) -> Dict:
    """Format help response"""
    help_text = f"""🤖 *Task Intel Bot*

*Ask anything naturally:*
• "what is omar working on?"
• "show me high priority tasks" 
• "what's blocked?"
• "tech department update"
• "company brief"

*All columns supported:*
• Task Name, Owner, Status, Due Date
• Next Steps, Blocker, Impact, Priority

*Examples:*
• `/intel what is omar working on?`
• `/intel high priority tasks`
• `/intel what's in progress?`
• `/intel brief`

📊 *Live data:* {len(tasks)} tasks tracked"""
    
    return {"response_type": "in_channel", "text": help_text}

def format_general_response(tasks: List[Dict], analysis: Dict) -> Dict:
    """Format general response for other query types"""
    if not tasks:
        return {"response_type": "in_channel", "text": "🔍 No matching tasks found"}
    
    intent = analysis.get('intent', 'results').replace('_', ' ').title()
    response = f"📋 *{intent}* ({len(tasks)} tasks)\n\n"
    
    for i, task in enumerate(tasks[:4], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   {owners} | {task['status']} | {task['priority']} | Due: {task['due_date']}\n\n"
    
    return {"response_type": "in_channel", "text": response}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
