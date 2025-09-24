from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import os
import logging
import asyncio
import aiohttp
from datetime import datetime, date, timedelta
from notion_client import Client
import json
import time
from typing import Dict, List, Optional
import cachetools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize with error handling
notion = None
if os.getenv('NOTION_TOKEN'):
    try:
        notion = Client(auth=os.getenv('NOTION_TOKEN'), timeout=10)
    except Exception as e:
        logger.error(f"Notion client init failed: {e}")

# Cache for 30 seconds to avoid hitting Notion too frequently
cache = cachetools.TTLCache(maxsize=100, ttl=30)

DATABASES = {
    'Operations': os.getenv('NOTION_DB_OPS'),
    'Commercial': os.getenv('NOTION_DB_COMM'),
    'Tech': os.getenv('NOTION_DB_TECH'),
    'Finance': os.getenv('NOTION_DB_FIN')
}

# Quick response to satisfy Slack's 3-second timeout
QUICK_RESPONSES = {
    "help": {
        "response_type": "ephemeral",
        "text": "🤖 Task Intel Bot - Gathering your data... I'll respond in a moment!"
    }
}

async def analyze_query_fast(query: str) -> Dict:
    """Fast query analysis without OpenAI to avoid timeouts"""
    query_lower = query.lower()
    
    # Quick keyword matching - much faster than API calls
    if any(word in query_lower for word in ['help', 'assist', 'guide']):
        return {"intent": "help"}
    
    # Person query (look for common patterns)
    if any(phrase in query_lower for phrase in ['what is', 'show me', "what's", 'how is']):
        # Extract potential name after the phrase
        words = query_lower.split()
        for i, word in enumerate(words):
            if word in ['what', 'show', 'how'] and i + 1 < len(words):
                potential_name = words[i + 1]
                if len(potential_name) > 2 and potential_name not in ['is', 'are', 'the', 'me']:
                    return {"intent": "person_query", "person_name": potential_name.title()}
    
    # Department query
    departments = ['operations', 'commercial', 'tech', 'finance']
    for dept in departments:
        if dept in query_lower:
            return {"intent": "department_query", "department": dept.title()}
    
    # Status queries
    if 'progress' in query_lower:
        return {"intent": "status_query", "status": "In progress"}
    elif 'todo' in query_lower or 'to do' in query_lower:
        return {"intent": "status_query", "status": "To Do"}
    elif 'done' in query_lower:
        return {"intent": "status_query", "status": "Done"}
    
    # Priority queries
    if 'high' in query_lower and 'priority' in query_lower:
        return {"intent": "priority_query", "priority": "High"}
    elif 'low' in query_lower and 'priority' in query_lower:
        return {"intent": "priority_query", "priority": "Low"}
    
    # Brief/overview
    if any(word in query_lower for word in ['brief', 'overview', 'summary', 'company']):
        return {"intent": "brief"}
    
    # Default to brief for empty or unclear queries
    return {"intent": "brief"}

async def fetch_notion_database_parallel(db_id: str, dept: str) -> List[Dict]:
    """Fetch a single Notion database asynchronously"""
    if not notion or not db_id:
        return []
    
    try:
        # Use async timeout
        result = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: notion.databases.query(database_id=db_id, page_size=50)
        )
        
        tasks = []
        for page in result.get('results', []):
            task = parse_notion_page_simple(page, dept)
            if task:
                tasks.append(task)
        
        return tasks
        
    except Exception as e:
        logger.error(f"Error fetching {dept} database: {e}")
        return []

async def get_all_tasks_parallel() -> List[Dict]:
    """Fetch all databases in parallel"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    
    # Create all async tasks
    fetch_tasks = []
    for dept, db_id in DATABASES.items():
        if db_id:
            fetch_tasks.append(fetch_notion_database_parallel(db_id, dept))
    
    # Run all fetches in parallel
    if fetch_tasks:
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                tasks.extend(result)
    
    # Cache the result
    cache[cache_key] = tasks
    return tasks

def parse_notion_page_simple(page: Dict, department: str) -> Optional[Dict]:
    """Fast parsing with minimal processing"""
    try:
        props = page.get('properties', {})
        
        # Quick owner extraction
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            name = person.get('name')
            if name:
                owners.append(name)
            elif person.get('id'):
                # Just use ID for speed - we can resolve names later if needed
                owners.append(f"user_{person['id'][-6:]}")
        
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        
        return {
            'name': get_property_fast(props, 'Task Name', 'title'),
            'owners': owners,
            'status': get_property_fast(props, 'Status', 'select'),
            'due_date': due_date_raw.split('T')[0] if due_date_raw else 'No date',
            'next_step': get_property_fast(props, 'Next Steps', 'rich_text'),
            'blocker': get_property_fast(props, 'Blocker', 'select'),
            'impact': get_property_fast(props, 'Impact', 'rich_text'),
            'priority': get_property_fast(props, 'Priority', 'select'),
            'department': department,
            'due_date_raw': due_date_raw,
        }
    except Exception as e:
        logger.error(f"Error parsing page: {e}")
        return None

def get_property_fast(props, field_name: str, field_type: str) -> str:
    """Fast property extraction"""
    try:
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
    except:
        return ''

async def send_delayed_response(response_url: str, payload: Dict):
    """Send delayed response to Slack"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(response_url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Slack response failed: {await resp.text()}")
    except Exception as e:
        logger.error(f"Failed to send delayed response: {e}")

def create_quick_response(analysis: Dict) -> Dict:
    """Create immediate response to satisfy Slack timeout"""
    intent = analysis.get('intent', 'brief')
    
    if intent == 'help':
        return {
            "response_type": "ephemeral",
            "text": "🤖 Task Intel Bot - I'm gathering your data now. One moment..."
        }
    
    person = analysis.get('person_name', '')
    dept = analysis.get('department', '')
    
    if person:
        message = f"🔍 Looking up tasks for {person}..."
    elif dept:
        message = f"📊 Gathering {dept} department overview..."
    else:
        message = "🏢 Compiling company brief..."
    
    return {
        "response_type": "ephemeral",
        "text": f"🤖 {message} I'll post the results here shortly."
    }

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack slash commands with immediate response"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        logger.info(f"Received query: '{query}'")
        
        # IMMEDIATE response to satisfy Slack's 3-second timeout
        analysis = await analyze_query_fast(query)
        immediate_response = create_quick_response(analysis)
        
        # Process the actual request in background
        if response_url:
            background_tasks.add_task(
                process_slack_query, 
                query, 
                analysis, 
                response_url
            )
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral",
            "text": "❌ Sorry, I encountered an error. Please try again in a moment."
        })

async def process_slack_query(query: str, analysis: Dict, response_url: str):
    """Process the actual query in background and send delayed response"""
    try:
        start_time = time.time()
        
        # Get tasks (cached and parallel)
        tasks = await get_all_tasks_parallel()
        logger.info(f"Fetched {len(tasks)} tasks in {time.time() - start_time:.2f}s")
        
        if not tasks:
            payload = {
                "response_type": "in_channel",
                "text": "❌ No tasks found in Notion databases. Please check your configuration."
            }
        else:
            # Filter and format based on analysis
            filtered_tasks = filter_tasks_fast(tasks, analysis)
            payload = await format_slack_response(filtered_tasks, analysis, query)
        
        # Send the actual response
        await send_delayed_response(response_url, payload)
        logger.info(f"Total processing time: {time.time() - start_time:.2f}s")
        
    except Exception as e:
        logger.error(f"Background processing error: {e}")
        error_payload = {
            "response_type": "in_channel",
            "text": "❌ Sorry, I encountered an error processing your request. Please try again."
        }
        await send_delayed_response(response_url, error_payload)

def filter_tasks_fast(tasks: List[Dict], analysis: Dict) -> List[Dict]:
    """Fast task filtering"""
    filtered = tasks
    
    if analysis.get('intent') == 'person_query' and analysis.get('person_name'):
        person_lower = analysis['person_name'].lower()
        filtered = [t for t in tasks if any(
            person_lower in owner.lower() for owner in t['owners']
        )]
    
    elif analysis.get('intent') == 'department_query' and analysis.get('department'):
        filtered = [t for t in tasks if t['department'] == analysis['department']]
    
    elif analysis.get('intent') == 'status_query' and analysis.get('status'):
        filtered = [t for t in tasks if t['status'] == analysis['status']]
    
    elif analysis.get('intent') == 'priority_query' and analysis.get('priority'):
        filtered = [t for t in tasks if t['priority'] == analysis['priority']]
    
    return filtered

async def format_slack_response(tasks: List[Dict], analysis: Dict, original_query: str) -> Dict:
    """Format the final Slack response"""
    if not tasks:
        return {
            "response_type": "in_channel",
            "text": f"❌ No tasks found matching: '{original_query}'"
        }
    
    # Simple text response for speed (avoid blocks for now)
    intent = analysis.get('intent', 'brief')
    
    if intent == 'person_query':
        person = analysis.get('person_name', 'Unknown')
        response_text = format_person_text(tasks, person)
    elif intent == 'department_query':
        dept = analysis.get('department', 'Unknown')
        response_text = format_department_text(tasks, dept)
    elif intent == 'brief':
        response_text = format_brief_text(tasks)
    else:
        response_text = format_general_text(tasks, analysis)
    
    return {
        "response_type": "in_channel",
        "text": response_text
    }

def format_person_text(tasks: List[Dict], person: str) -> str:
    """Format person-specific response as text"""
    sorted_tasks = sort_tasks_fast(tasks)
    
    response = f"👤 *{person}'s Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:6], 1):
        response += f"*{i}. {task['name']}*\n"
        response += f"   _{task['department']}_ • {task['status']} • Due: {task['due_date']}\n"
        response += f"   Priority: {task['priority']} • Blocker: {task['blocker']}\n"
        if task['next_step']:
            response += f"   Next: {task['next_step']}\n"
        response += "\n"
    
    # Add summary
    dept_counts = {}
    for task in tasks:
        dept_counts[task['department']] = dept_counts.get(task['department'], 0) + 1
    
    dept_summary = " • ".join([f"{k}: {v}" for k, v in dept_counts.items()])
    response += f"📊 *Summary:* {len(tasks)} tasks ({dept_summary})"
    
    return response

def format_brief_text(tasks: List[Dict]) -> str:
    """Format company brief as text"""
    dept_counts = get_department_counts(tasks)
    status_counts = get_status_counts(tasks)
    
    response = "🏢 *Company Brief*\n\n"
    
    response += "📈 *By Department:*\n"
    for dept, count in sorted(dept_counts.items()):
        response += f"• {dept}: {count} tasks\n"
    
    response += "\n🔄 *By Status:*\n"
    for status, count in sorted(status_counts.items()):
        response += f"• {status}: {count} tasks\n"
    
    response += f"\n📊 *Total:* {len(tasks)} tasks"
    
    return response

def format_department_text(tasks: List[Dict], department: str) -> str:
    """Format department-specific response"""
    sorted_tasks = sort_tasks_fast(tasks)
    
    response = f"🏢 *{department} Department Tasks* ({len(tasks)} total)\n\n"
    
    for i, task in enumerate(sorted_tasks[:8], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   {owners} • {task['status']} • Due: {task['due_date']}\n"
        response += f"   Priority: {task['priority']} • Blocker: {task['blocker']}\n\n"
    
    return response

def format_general_text(tasks: List[Dict], analysis: Dict) -> str:
    """Format general response"""
    sorted_tasks = sort_tasks_fast(tasks)
    
    intent_desc = analysis.get('intent', 'Results').replace('_', ' ').title()
    response = f"🔍 *{intent_desc}* ({len(tasks)} tasks)\n\n"
    
    for i, task in enumerate(sorted_tasks[:5], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        response += f"*{i}. {task['name']}*\n"
        response += f"   {task['department']} • {owners}\n"
        response += f"   {task['status']} • Due: {task['due_date']} • {task['priority']} priority\n\n"
    
    return response

def sort_tasks_fast(tasks: List[Dict]) -> List[Dict]:
    """Fast task sorting"""
    def sort_key(task):
        priority_order = {'High': 0, 'Medium': 1, 'Low': 2, 'Not set': 3}
        priority_score = priority_order.get(task.get('priority', 'Not set'), 3)
        
        due_date = task.get('due_date_raw', '')
        if due_date and due_date != 'No date':
            try:
                # Just use string comparison for speed
                return (priority_score, due_date)
            except:
                pass
        
        return (priority_score, '9999-12-31')  # Far future date
    
    return sorted(tasks, key=sort_key)

def get_department_counts(tasks: List[Dict]) -> Dict:
    counts = {}
    for task in tasks:
        counts[task['department']] = counts.get(task['department'], 0) + 1
    return counts

def get_status_counts(tasks: List[Dict]) -> Dict:
    counts = {}
    for task in tasks:
        counts[task['status']] = counts.get(task['status'], 0) + 1
    return counts

@app.get("/")
async def home():
    return {"status": "ready", "message": "Task Intel Bot is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, timeout_keep_alive=5)
