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

app = FastAPI(title="Task Intel Bot", docs_url="/docs", redoc_url=None)

# Initialize cache
cache = cachetools.TTLCache(maxsize=100, ttl=30)

# Database configuration
DATABASES = {
    'Operations': os.getenv('NOTION_DB_OPS', ''),
    'Commercial': os.getenv('NOTION_DB_COMM', ''),
    'Tech': os.getenv('NOTION_DB_TECH', ''),
    'Finance': os.getenv('NOTION_DB_FIN', '')
}

# Your company team members (hardcoded for reliability)
COMPANY_TEAM = ['Omar', 'Derrick', 'Bhavya', 'Nishanth', 'Chethan', 'Deema', 'Brazil']

# Try to import Notion client
notion = None
try:
    from notion_client import Client
    notion_token = os.getenv('NOTION_TOKEN')
    if notion_token:
        notion = Client(auth=notion_token, timeout_ms=10000)
        logger.info("Notion client initialized successfully")
    else:
        logger.warning("NOTION_TOKEN not set - Notion features disabled")
except ImportError:
    logger.warning("notion-client package not installed")
except Exception as e:
    logger.error(f"Notion client init failed: {e}")

@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <html>
        <head>
            <title>Task Intel Bot</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .status {{ background: #f0f8ff; padding: 20px; border-radius: 8px; }}
            </style>
        </head>
        <body>
            <div class="status">
                <h1>🤖 Task Intel Bot</h1>
                <p>Status: <strong>Ready</strong></p>
                <p>This service powers Slack commands for task management queries.</p>
                <p>Environment: {'Production' if os.getenv('RENDER', False) else 'Development'}</p>
                <p>Team Members: {", ".join(COMPANY_TEAM)}</p>
                <p>Databases configured: {sum(1 for db_id in DATABASES.values() if db_id)}</p>
                <p><a href="/health">Health Check</a> | <a href="/docs">API Docs</a></p>
            </div>
        </body>
    </html>
    """

@app.get("/health")
async def health_check():
    status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "team": COMPANY_TEAM,
        "services": {
            "notion": "available" if notion else "disabled",
            "databases_configured": sum(1 for db_id in DATABASES.values() if db_id),
            "team_members": len(COMPANY_TEAM)
        }
    }
    return status

@app.get("/debug/tasks")
async def debug_tasks():
    """Debug endpoint to see actual tasks"""
    try:
        tasks = await get_all_tasks()
        all_owners = set()
        for task in tasks:
            all_owners.update(task['owners'])
        
        return {
            "total_tasks": len(tasks),
            "all_owners": sorted(list(all_owners)),
            "sample_tasks": tasks[:3] if tasks else "No tasks"
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/notion")
async def debug_notion():
    """Debug Notion connection"""
    try:
        if not notion:
            return {"error": "Notion client not initialized"}
        
        results = {}
        for dept, db_id in DATABASES.items():
            if db_id:
                try:
                    result = notion.databases.query(database_id=db_id, page_size=5)
                    results[dept] = {
                        "pages_found": len(result.get('results', [])),
                        "sample_page": result.get('results', [])[0] if result.get('results') else None
                    }
                except Exception as e:
                    results[dept] = {"error": str(e)}
        
        return results
    except Exception as e:
        return {"error": str(e)}

async def analyze_query_fast(query: str) -> Dict:
    """Fast query analysis with company-specific team member detection"""
    if not query or query.strip() == "":
        return {"intent": "brief"}
    
    query_lower = query.lower().strip()
    
    # Help queries
    if any(word in query_lower for word in ['help', 'assist', 'guide', 'how to']):
        return {"intent": "help"}
    
    # Check for team member names in the query
    for team_member in COMPANY_TEAM:
        if team_member.lower() in query_lower:
            return {"intent": "person_query", "person_name": team_member}
    
    # Department queries
    departments = ['operations', 'commercial', 'tech', 'finance', 'engineering', 'sales', 'marketing']
    for dept in departments:
        if dept in query_lower:
            dept_map = {
                'engineering': 'Tech',
                'sales': 'Commercial', 
                'marketing': 'Commercial'
            }
            return {"intent": "department_query", "department": dept_map.get(dept, dept.title())}
    
    # Status queries
    status_map = {
        'progress': 'In progress',
        'in progress': 'In progress',
        'todo': 'To Do',
        'to do': 'To Do',
        'done': 'Done',
        'completed': 'Done'
    }
    
    for keyword, status in status_map.items():
        if keyword in query_lower:
            return {"intent": "status_query", "status": status}
    
    # Priority queries
    if 'high priority' in query_lower or 'high-priority' in query_lower:
        return {"intent": "priority_query", "priority": "High"}
    elif 'low priority' in query_lower or 'low-priority' in query_lower:
        return {"intent": "priority_query", "priority": "Low"}
    elif 'medium priority' in query_lower or 'medium-priority' in query_lower:
        return {"intent": "priority_query", "priority": "Medium"}
    elif 'priority' in query_lower:
        return {"intent": "priority_query", "priority": "High"}
    
    # Time-based queries
    if 'this week' in query_lower or 'week' in query_lower:
        return {"intent": "time_query", "timeframe": "this_week"}
    elif 'today' in query_lower:
        return {"intent": "time_query", "timeframe": "today"}
    elif 'overdue' in query_lower:
        return {"intent": "time_query", "timeframe": "overdue"}
    
    # Brief/overview queries
    if any(word in query_lower for word in ['brief', 'overview', 'summary', 'company', 'status', 'update']):
        return {"intent": "brief"}
    
    # If query contains question words but no specific intent, default to brief
    question_words = ['what', 'how', 'who', 'when', 'where', 'show', 'tell']
    if any(word in query_lower for word in question_words):
        return {"intent": "brief"}
    
    # Default to brief for unclear queries
    return {"intent": "brief"}

async def fetch_notion_database(db_id: str, dept: str) -> List[Dict]:
    """Fetch tasks from a Notion database"""
    if not notion or not db_id:
        return []
    
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: notion.databases.query(
                database_id=db_id, 
                page_size=100
            )
        )
        
        tasks = []
        for page in result.get('results', []):
            task = parse_notion_page(page, dept)
            if task:
                tasks.append(task)
        
        logger.info(f"Fetched {len(tasks)} tasks from {dept}")
        return tasks
        
    except Exception as e:
        logger.error(f"Error fetching {dept} database: {e}")
        return []

def parse_notion_page(page: Dict, department: str) -> Optional[Dict]:
    """Parse a Notion page into a task dictionary"""
    try:
        props = page.get('properties', {})
        
        # Extract owner names safely
        owners = []
        owner_prop = props.get('Owner', {})
        people_data = owner_prop.get('people', [])
        
        for person in people_data:
            name = person.get('name')
            if name:
                # FLEXIBLE NAME MATCHING - Improved version
                name_lower = name.lower()
                
                # First try exact match
                exact_match = False
                for team_member in COMPANY_TEAM:
                    if team_member.lower() == name_lower:
                        owners.append(team_member)
                        exact_match = True
                        break
                
                if not exact_match:
                    # Try partial match (e.g., "Omar Smith" contains "Omar")
                    for team_member in COMPANY_TEAM:
                        if team_member.lower() in name_lower:
                            owners.append(team_member)
                            break
                    else:
                        # If no match, keep the original name
                        owners.append(name)
            elif person.get('id'):
                owners.append(f"user_{person['id'][-6:]}")
        
        # Get due date
        due_date_prop = props.get('Due Date', {})
        due_date_raw = due_date_prop.get('date', {}).get('start') if due_date_prop else None
        
        task = {
            'name': get_property_safe(props, 'Task Name', 'title'),
            'owners': owners,
            'status': get_property_safe(props, 'Status', 'select'),
            'due_date': format_due_date(due_date_raw),
            'next_step': get_property_safe(props, 'Next Steps', 'rich_text'),
            'blocker': get_property_safe(props, 'Blocker', 'select'),
            'impact': get_property_safe(props, 'Impact', 'rich_text'),
            'priority': get_property_safe(props, 'Priority', 'select'),
            'department': department,
            'due_date_raw': due_date_raw,
        }
        
        # Only return tasks with a name
        if task['name'] and task['name'] != 'No name':
            return task
        return None
        
    except Exception as e:
        logger.error(f"Error parsing Notion page: {e}")
        return None

def get_property_safe(props: Dict, field_name: str, field_type: str) -> str:
    """Safely extract property value with error handling"""
    try:
        field = props.get(field_name, {})
        
        if field_type == 'title':
            titles = field.get('title', [])
            return titles[0].get('plain_text', 'No name') if titles else 'No name'
        elif field_type == 'select':
            select = field.get('select', {})
            return select.get('name', 'Not set')
        elif field_type == 'date':
            date_obj = field.get('date', {})
            return date_obj.get('start', 'No date')
        elif field_type == 'rich_text':
            rich_text = field.get('rich_text', [])
            return rich_text[0].get('plain_text', 'Not specified') if rich_text else 'Not specified'
        
        return 'Not set'
    except Exception:
        return 'Error'

def format_due_date(due_date_raw: Optional[str]) -> str:
    """Format due date for display"""
    if not due_date_raw:
        return 'No date'
    
    try:
        if 'T' in due_date_raw:
            date_part = due_date_raw.split('T')[0]
            return date_part
        return due_date_raw
    except:
        return due_date_raw

async def get_all_tasks() -> List[Dict]:
    """Get all tasks from all databases with caching"""
    cache_key = "all_tasks"
    
    if cache_key in cache:
        logger.info("Returning cached tasks")
        return cache[cache_key]
    
    tasks = []
    
    if not notion:
        logger.warning("Notion client not available - returning empty task list")
        return tasks
    
    fetch_tasks = []
    for dept, db_id in DATABASES.items():
        if db_id and db_id.strip():
            fetch_tasks.append(fetch_notion_database(db_id, dept))
        else:
            logger.warning(f"No database ID for {dept}")
    
    if fetch_tasks:
        try:
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    tasks.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"Database fetch error: {result}")
        except Exception as e:
            logger.error(f"Error gathering database results: {e}")
    
    cache[cache_key] = tasks
    logger.info(f"Fetched {len(tasks)} total tasks")
    return tasks

async def send_slack_response(response_url: str, payload: Dict):
    """Send response to Slack"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(response_url, json=payload) as response:
                if response.status == 200:
                    logger.info("Successfully sent Slack response")
                else:
                    logger.error(f"Slack response failed: {response.status} - {await response.text()}")
    except Exception as e:
        logger.error(f"Failed to send Slack response: {e}")

@app.post("/slack/command")
async def slack_command(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str = Form(None),
    team_id: str = Form(None),
    channel_id: str = Form(None),
    user_id: str = Form(None),
    command: str = Form(None),
    text: str = Form(""),
    response_url: str = Form(None)
):
    """Handle Slack slash commands"""
    try:
        logger.info(f"Slack command received: user={user_id}, text='{text}'")
        
        analysis = await analyze_query_fast(text)
        immediate_response = create_immediate_response(analysis)
        
        if response_url:
            background_tasks.add_task(
                process_complete_query,
                text,
                analysis,
                response_url
            )
            logger.info("Background task queued for processing")
        else:
            logger.warning("No response_url provided - cannot process in background")
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral",
            "text": "❌ Sorry, I encountered an error processing your command. Please try again."
        })

def create_immediate_response(analysis: Dict) -> Dict:
    """Create immediate response to satisfy Slack timeout"""
    intent = analysis.get('intent', 'brief')
    
    if intent == 'help':
        return {
            "response_type": "ephemeral",
            "text": "🤖 Task Intel Bot - Gathering help information..."
        }
    
    person = analysis.get('person_name', '')
    dept = analysis.get('department', '')
    timeframe = analysis.get('timeframe', '')
    
    if person:
        message = f"🔍 Looking up tasks for {person}..."
    elif dept:
        message = f"📊 Gathering {dept} department overview..."
    elif timeframe:
        message = f"📅 Checking {timeframe.replace('_', ' ')} tasks..."
    else:
        message = "🏢 Compiling company brief..."
    
    return {
        "response_type": "ephemeral",
        "text": f"🤖 {message} I'll post the complete results here in a moment."
    }

async def process_complete_query(query: str, analysis: Dict, response_url: str):
    """Process the complete query in background"""
    try:
        logger.info(f"Processing query in background: '{query}'")
        start_time = time.time()
        
        tasks = await get_all_tasks()
        logger.info(f"Retrieved {len(tasks)} tasks in {time.time() - start_time:.2f}s")
        
        if not tasks:
            payload = {
                "response_type": "in_channel",
                "text": "📭 No tasks found in the connected Notion databases. Please check your configuration."
            }
        else:
            if analysis.get('intent') == 'help':
                payload = format_help_with_people(tasks)
            else:
                filtered_tasks = filter_tasks_based_on_analysis(tasks, analysis)
                payload = format_final_response(filtered_tasks, analysis, query)
        
        await send_slack_response(response_url, payload)
        logger.info(f"Query processing completed in {time.time() - start_time:.2f}s")
        
    except Exception as e:
        logger.error(f"Background processing error: {e}")
        error_payload = {
            "response_type": "in_channel",
            "text": "❌ Sorry, I encountered an error while processing your request. Please try again later."
        }
        await send_slack_response(response_url, error_payload)

def filter_tasks_based_on_analysis(tasks: List[Dict], analysis: Dict) -> List[Dict]:
    """Filter tasks based on query analysis"""
    filtered = tasks
    
    intent = analysis.get('intent')
    if intent == 'person_query':
        person_name = analysis.get('person_name', '')
        if person_name:
            # More flexible person matching
            filtered = []
            for task in tasks:
                for owner in task['owners']:
                    if person_name.lower() in owner.lower():
                        filtered.append(task)
                        break
    
    elif intent == 'department_query':
        department = analysis.get('department')
        if department:
            filtered = [t for t in tasks if t['department'] == department]
    
    elif intent == 'status_query':
        status = analysis.get('status')
        if status:
            filtered = [t for t in tasks if t['status'] == status]
    
    elif intent == 'priority_query':
        priority = analysis.get('priority')
        if priority:
            filtered = [t for t in tasks if t['priority'] == priority]
    
    elif intent == 'time_query':
        timeframe = analysis.get('timeframe')
        if timeframe:
            filtered = filter_tasks_by_timeframe(tasks, timeframe)
    
    return filtered

def filter_tasks_by_timeframe(tasks: List[Dict], timeframe: str) -> List[Dict]:
    """Filter tasks by timeframe"""
    today = datetime.now().date()
    filtered = []
    
    for task in tasks:
        due_date_str = task.get('due_date_raw')
        if not due_date_str or due_date_str == 'No date':
            continue
            
        try:
            # Parse date from string
            if 'T' in due_date_str:
                due_date = datetime.fromisoformat(due_date_str.replace('Z', '+00:00')).date()
            else:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            
            if timeframe == 'today' and due_date == today:
                filtered.append(task)
            elif timeframe == 'this_week':
                start_of_week = today - timedelta(days=today.weekday())
                end_of_week = start_of_week + timedelta(days=6)
                if start_of_week <= due_date <= end_of_week:
                    filtered.append(task)
            elif timeframe == 'overdue' and due_date < today:
                filtered.append(task)
                
        except ValueError as e:
            logger.warning(f"Could not parse due date {due_date_str}: {e}")
            continue
    
    return filtered

def format_final_response(tasks: List[Dict], analysis: Dict, original_query: str) -> Dict:
    """Format the final response for Slack"""
    if not tasks:
        return {
            "response_type": "in_channel",
            "text": f"🔍 No tasks found matching: '{original_query}'\n\nTry: /intel what is [name] working on? or /intel brief"
        }
    
    intent = analysis.get('intent', 'brief')
    
    if intent == 'person_query':
        person_name = analysis.get('person_name', 'Someone')
        text = format_person_response(tasks, person_name)
    elif intent == 'department_query':
        department = analysis.get('department', 'Unknown Department')
        text = format_department_response(tasks, department)
    elif intent == 'brief':
        text = format_brief_response(tasks)
    elif intent == 'status_query':
        status = analysis.get('status', 'Unknown Status')
        text = format_status_response(tasks, status)
    elif intent == 'priority_query':
        priority = analysis.get('priority', 'Unknown Priority')
        text = format_priority_response(tasks, priority)
    elif intent == 'time_query':
        timeframe = analysis.get('timeframe', 'Unknown Timeframe')
        text = format_timeframe_response(tasks, timeframe)
    else:
        text = format_general_response(tasks, analysis)
    
    return {"response_type": "in_channel", "text": text}

def format_person_response(tasks: List[Dict], person_name: str) -> str:
    """Format response for person queries"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    
    response = f"👤 *{person_name}'s Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:6], 1):
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Department:_ {task['department']} | _Status:_ {task['status']}\n"
        response += f"   _Due:_ {task['due_date']} | _Priority:_ {task['priority']}\n"
        if task['blocker'] != 'Not set' and task['blocker'] != 'None':
            response += f"   _Blocker:_ {task['blocker']}\n"
        if task['next_step'] and task['next_step'] != 'Not specified':
            response += f"   _Next:_ {task['next_step']}\n"
        response += "\n"
    
    dept_summary = ", ".join([f"{dept}: {count}" for dept, count in get_department_counts(tasks).items()])
    response += f"📊 *Summary:* {len(tasks)} tasks ({dept_summary})"
    
    return response

def format_brief_response(tasks: List[Dict]) -> str:
    """Format company brief response"""
    dept_counts = get_department_counts(tasks)
    status_counts = get_status_counts(tasks)
    priority_counts = get_priority_counts(tasks)
    
    response = "🏢 *Company Brief*\n\n"
    
    response += "📈 *By Department:*\n"
    for dept, count in sorted(dept_counts.items()):
        response += f"• {dept}: {count} tasks\n"
    
    response += "\n🔄 *By Status:*\n"
    for status, count in sorted(status_counts.items()):
        response += f"• {status}: {count} tasks\n"
    
    response += "\n🎯 *By Priority:*\n"
    for priority, count in sorted(priority_counts.items()):
        response += f"• {priority}: {count} tasks\n"
    
    active_members = set()
    for task in tasks:
        if task['status'] in ['In progress', 'To Do']:
            active_members.update(task['owners'])
    
    if active_members:
        response += f"\n👥 *Active Team Members:* {', '.join(sorted(active_members))}"
    
    response += f"\n\n📊 *Total Tasks:* {len(tasks)}"
    
    return response

def format_department_response(tasks: List[Dict], department: str) -> str:
    """Format department-specific response"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    
    response = f"🏢 *{department} Department* ({len(tasks)} tasks)\n\n"
    
    for i, task in enumerate(sorted_tasks[:8], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Owner:_ {owners} | _Status:_ {task['status']}\n"
        response += f"   _Due:_ {task['due_date']} | _Priority:_ {task['priority']}\n\n"
    
    status_summary = ", ".join([f"{status}: {count}" for status, count in get_status_counts(tasks).items()])
    response += f"📈 *Status Summary:* {status_summary}"
    
    return response

def format_status_response(tasks: List[Dict], status: str) -> str:
    """Format status-specific response"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    
    response = f"🔄 *{status} Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:6], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Department:_ {task['department']} | _Owner:_ {owners}\n"
        response += f"   _Due:_ {task['due_date']} | _Priority:_ {task['priority']}\n\n"
    
    return response

def format_priority_response(tasks: List[Dict], priority: str) -> str:
    """Format priority-specific response"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    
    response = f"🎯 *{priority} Priority Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:6], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Department:_ {task['department']} | _Owner:_ {owners}\n"
        response += f"   _Status:_ {task['status']} | _Due:_ {task['due_date']}\n\n"
    
    return response

def format_timeframe_response(tasks: List[Dict], timeframe: str) -> str:
    """Format timeframe-specific response"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    timeframe_display = timeframe.replace('_', ' ').title()
    
    response = f"📅 *{timeframe_display} Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:6], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Department:_ {task['department']} | _Owner:_ {owners}\n"
        response += f"   _Status:_ {task['status']} | _Priority:_ {task['priority']}\n\n"
    
    return response

def format_general_response(tasks: List[Dict], analysis: Dict) -> str:
    """Format general response for other query types"""
    sorted_tasks = sort_tasks_by_priority(tasks)
    
    intent_description = analysis.get('intent', 'Results').replace('_', ' ').title()
    response = f"🔍 *{intent_description}* ({len(tasks)} tasks found)\n\n"
    
    for i, task in enumerate(sorted_tasks[:5], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   _Dept:_ {task['department']} | _Owner:_ {owners}\n"
        response += f"   _Status:_ {task['status']} | _Due:_ {task['due_date']}\n\n"
    
    return response

def format_help_with_people(tasks: List[Dict]) -> Dict:
    """Enhanced help with team members"""
    available_people = get_available_people(tasks)
    
    help_text = f"""🤖 *Task Intel Bot - Company Wide Updates*

*Natural Language Queries:*
• `What is [name] working on?`
• `Show me [department] tasks` 
• `What's in progress?`
• `High priority items`
• `Company overview`
• `Tasks due this week`

*Team Members:* {", ".join(COMPANY_TEAM)}
*Departments:* {", ".join(DATABASES.keys())}

*Examples:*
• `/intel what is Omar working on?`
• `/intel show me Tech department tasks`
• `/intel what's in progress?`
• `/intel high priority items`
• `/intel tasks due this week`
• `/intel company brief`

📊 *Live data:* {len(tasks)} tasks tracked | 👥 {len(available_people)} people with tasks"""
    
    return {
        "response_type": "in_channel",
        "text": help_text
    }

def get_available_people(tasks: List[Dict]) -> List[str]:
    """Get list of all people with tasks"""
    people = set()
    for task in tasks:
        people.update(task['owners'])
    return sorted(people)

def sort_tasks_by_priority(tasks: List[Dict]) -> List[Dict]:
    """Sort tasks by priority and due date"""
    def priority_score(task):
        priority_order = {'High': 0, 'Medium': 1, 'Low': 2, 'Not set': 3}
        return priority_order.get(task.get('priority', 'Not set'), 3)
    
    def due_date_score(task):
        due_date = task.get('due_date_raw')
        if due_date and due_date != 'No date':
            try:
                return datetime.fromisoformat(due_date.replace('Z', '+00:00'))
            except:
                pass
        return datetime.max
    
    return sorted(tasks, key=lambda x: (priority_score(x), due_date_score(x)))

def get_department_counts(tasks: List[Dict]) -> Dict[str, int]:
    counts = {}
    for task in tasks:
        dept = task['department']
        counts[dept] = counts.get(dept, 0) + 1
    return counts

def get_status_counts(tasks: List[Dict]) -> Dict[str, int]:
    counts = {}
    for task in tasks:
        status = task['status']
        counts[status] = counts.get(status, 0) + 1
    return counts

def get_priority_counts(tasks: List[Dict]) -> Dict[str, int]:
    counts = {}
    for task in tasks:
        priority = task['priority']
        counts[priority] = counts.get(priority, 0) + 1
    return counts

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0"
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(
        app, 
        host=host, 
        port=port,
        log_config=None
    )
