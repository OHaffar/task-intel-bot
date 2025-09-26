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
    return {"status": "ready", "service": "Fast Task Intel"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

async def understand_query_fast(query: str) -> Dict:
    """Fast query understanding - minimal processing"""
    if not query:
        return {"intent": "team_overview"}
    
    query_lower = query.lower()
    
    # Quick keyword matching - fastest possible
    words = set(query_lower.split())
    
    # Team queries
    if any(word in words for word in ['team', 'everyone', 'workload', 'capacity']):
        return {"intent": "team_overview"}
    
    # Task count queries
    if any(word in words for word in ['many', 'count', 'number', 'how']):
        return {"intent": "task_counts"}
    
    # Individual person queries
    for person in TEAM_MEMBERS:
        if person.lower() in query_lower:
            return {"intent": "person_detail", "person": person}
    
    # Quick department queries
    if any(word in query_lower for word in ['tech', 'engineering']):
        return {"intent": "department", "department": "Tech"}
    elif any(word in query_lower for word in ['commercial', 'sales']):
        return {"intent": "department", "department": "Commercial"}
    
    # Status queries
    if any(word in words for word in ['block', 'stuck', 'blocker']):
        return {"intent": "blockers"}
    elif any(word in words for word in ['priority', 'important']):
        return {"intent": "priorities"}
    
    # Default fallbacks
    if any(word in words for word in ['overview', 'summary', 'brief']):
        return {"intent": "company_overview"}
    
    return {"intent": "team_overview"}

async def get_all_tasks_fast() -> List[Dict]:
    """Fast task fetching with minimal processing"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        return tasks
    
    # Fast parallel fetching
    fetch_tasks = []
    for dept, db_id in DATABASES.items():
        if db_id:
            fetch_tasks.append(fetch_database_fast(db_id, dept))
    
    if fetch_tasks:
        try:
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    tasks.extend(result)
        except Exception as e:
            logger.error(f"Error gathering tasks: {e}")
    
    cache[cache_key] = tasks
    return tasks

async def fetch_database_fast(db_id: str, dept: str) -> List[Dict]:
    """Fast database fetching"""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: notion.databases.query(database_id=db_id, page_size=50)  # Smaller page size for speed
        )
        
        tasks = []
        for page in result.get('results', []):
            task = parse_task_fast(page, dept)
            if task:
                tasks.append(task)
        
        return tasks
    except Exception as e:
        logger.error(f"Error fetching {dept}: {e}")
        return []

def parse_task_fast(page: Dict, department: str) -> Optional[Dict]:
    """Fast task parsing - minimal processing"""
    try:
        props = page.get('properties', {})
        
        # Fast name extraction
        name_field = props.get('Task Name', {})
        titles = name_field.get('title', [])
        name = titles[0].get('plain_text', '') if titles else ''
        if not name:
            return None
        
        # Fast owner extraction
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id and user_id in USER_ID_TO_NAME:
                owners.append(USER_ID_TO_NAME[user_id])
        
        # Fast date extraction
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        due_date = due_date_raw.split('T')[0] if due_date_raw else 'No date'
        
        return {
            'name': name,
            'owners': owners,
            'status': props.get('Status', {}).get('select', {}).get('name', 'Not set'),
            'due_date': due_date,
            'priority': props.get('Priority', {}).get('select', {}).get('name', 'Not set'),
            'department': department,
        }
    except Exception:
        return None

def generate_response_fast(tasks: List[Dict], analysis: Dict) -> str:
    """Fast response generation"""
    intent = analysis.get('intent', 'team_overview')
    
    if intent == 'team_overview':
        return generate_team_overview_fast(tasks)
    elif intent == 'person_detail':
        return generate_person_detail_fast(tasks, analysis['person'])
    elif intent == 'task_counts':
        return generate_task_counts_fast(tasks)
    elif intent == 'blockers':
        return generate_blockers_fast(tasks)
    elif intent == 'priorities':
        return generate_priorities_fast(tasks)
    else:
        return generate_team_overview_fast(tasks)

def generate_team_overview_fast(tasks: List[Dict]) -> str:
    """Fast team overview"""
    task_counts = {}
    for person in TEAM_MEMBERS:
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        task_counts[person] = len(person_tasks)
    
    # Fast sorting and formatting
    active_members = [(p, c) for p, c in task_counts.items() if c > 0]
    active_members.sort(key=lambda x: x[1], reverse=True)
    
    response = "👥 **Team Task Overview**\n\n"
    for person, count in active_members:
        response += f"• {person}: {count} tasks\n"
    
    response += f"\n📊 **Total:** {len(tasks)} tasks"
    return response

def generate_person_detail_fast(tasks: List[Dict], person: str) -> str:
    """Fast person detail"""
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    
    if not person_tasks:
        return f"👤 **{person}** has no tasks assigned."
    
    # Fast grouping
    in_progress = [t for t in person_tasks if t['status'] == 'In progress']
    high_priority = [t for t in person_tasks if t['priority'] == 'High']
    
    response = f"👤 **{person}'s Tasks** ({len(person_tasks)} total)\n\n"
    
    if in_progress:
        response += f"🔄 **In Progress ({len(in_progress)}):**\n"
        for task in in_progress[:3]:  # Limit for speed
            response += f"• {task['name']}"
            if task['due_date'] != 'No date':
                response += f" (due {task['due_date']})"
            response += "\n"
        response += "\n"
    
    if high_priority:
        response += f"🎯 **High Priority ({len(high_priority)}):**\n"
        for task in high_priority[:2]:
            response += f"• {task['name']}\n"
    
    return response

def generate_task_counts_fast(tasks: List[Dict]) -> str:
    """Fast task counts"""
    total = len(tasks)
    in_progress = len([t for t in tasks if t['status'] == 'In progress'])
    high_priority = len([t for t in tasks if t['priority'] == 'High'])
    
    return f"📊 **Task Counts:** {total} total, {in_progress} in progress, {high_priority} high priority"

def generate_blockers_fast(tasks: List[Dict]) -> str:
    """Fast blockers report"""
    blocked = [t for t in tasks if t['status'] == 'In progress']  # Simplified
    return f"🚧 **Active Work:** {len(blocked)} items in progress"

def generate_priorities_fast(tasks: List[Dict]) -> str:
    """Fast priorities report"""
    high_priority = [t for t in tasks if t['priority'] == 'High']
    return f"🎯 **High Priority:** {len(high_priority)} critical tasks"

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands with immediate response"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        # IMMEDIATE response to prevent timeout
        immediate_response = {
            "response_type": "ephemeral",
            "text": "🤖 Gathering your task info... (this takes a few seconds)"
        }
        
        # Process in background
        if response_url:
            background_tasks.add_task(process_query_fast, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Error - please try again"
        })

async def process_query_fast(query: str, response_url: str):
    """Process query in background"""
    try:
        start_time = time.time()
        
        # Fast analysis
        analysis = await understand_query_fast(query)
        
        # Fast task fetching
        tasks = await get_all_tasks_fast()
        
        # Fast response generation
        if not tasks:
            response = "📭 No tasks found."
        else:
            response = generate_response_fast(tasks, analysis)
        
        # Send delayed response
        payload = {"response_type": "in_channel", "text": response}
        await send_slack_response(response_url, payload)
        
        logger.info(f"Query processed in {time.time() - start_time:.2f}s")
        
    except Exception as e:
        logger.error(f"Background error: {e}")
        error_msg = "❌ Sorry, I'm having trouble. Please try again."
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
