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
    """Understand natural language queries with conversation support"""
    if not query:
        return {"intent": "company_update", "tone": "friendly"}
    
    query_lower = query.lower()
    
    # Greetings and conversational phrases
    if any(word in query_lower for word in ['hi', 'hello', 'hey', 'howdy']):
        return {"intent": "greeting", "tone": "warm"}
    
    if any(word in query_lower for word in ['thanks', 'thank you', 'appreciate']):
        return {"intent": "thanks", "tone": "appreciative"}
    
    if any(word in query_lower for word in ['next steps', 'what next', 'what should', 'recommend']):
        return {"intent": "next_steps", "tone": "helpful"}
    
    # Check for team members
    for person_key, person_name in TEAM_MEMBERS.items():
        if person_key in query_lower:
            return {"intent": "person_update", "person": person_name, "tone": "supportive"}
    
    # Check for other intents
    if any(word in query_lower for word in ['brief', 'overview', 'company', 'status', 'update']):
        return {"intent": "company_update", "tone": "confident"}
    
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem', 'blocker']):
        return {"intent": "blockers_update", "tone": "concerned"}
    
    if any(word in query_lower for word in ['priority', 'important', 'critical', 'urgent']):
        return {"intent": "priorities_update", "tone": "focused"}
    
    if any(word in query_lower for word in ['tech', 'engineering']):
        return {"intent": "department_update", "department": "Tech", "tone": "informative"}
    elif any(word in query_lower for word in ['commercial', 'sales', 'business']):
        return {"intent": "department_update", "department": "Commercial", "tone": "informative"}
    elif any(word in query_lower for word in ['operations', 'ops']):
        return {"intent": "department_update", "department": "Operations", "tone": "informative"}
    elif any(word in query_lower for word in ['finance', 'financial']):
        return {"intent": "department_update", "department": "Finance", "tone": "informative"}
    
    # Default to helpful response
    return {"intent": "help", "tone": "friendly"}

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
    """Generate conversational response with next steps"""
    intent = analysis['intent']
    tone = analysis.get('tone', 'friendly')
    
    if intent == 'greeting':
        return f"👋 Hey there! I'm your Task Intel Bot. I can tell you what everyone's working on, company status, blockers, or priorities. What would you like to know?"
    
    if intent == 'thanks':
        return "🙏 You're welcome! Happy to help. What else can I update you on?"
    
    if intent == 'help':
        return """🤖 *How I can help you:*

• *People:* "What is Omar working on?" or "How's Derrick's workload?"
• *Company:* "Company update" or "How are we doing?"
• *Blockers:* "What's blocked?" or "Any issues?"
• *Priorities:* "High priority items" or "What's urgent?"
• *Departments:* "Tech status" or "Commercial update"

Just ask naturally! I understand conversational language."""

    if intent == 'next_steps':
        # Show tasks with meaningful next steps
        tasks_with_next_steps = [t for t in tasks if t['next_step'] and t['next_step'] not in ['', 'Not specified']]
        
        if not tasks_with_next_steps:
            return "📋 *Next Steps Overview:*\nMost tasks don't have specific next steps defined yet. The team is likely executing on current priorities."
        
        response = "📋 *Here are the key next steps across the company:*\n\n"
        
        for i, task in enumerate(tasks_with_next_steps[:6], 1):
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"*{i}. {task['name']}* ({owners})\n"
            response += f"   👉 *Next:* {task['next_step']}\n"
            if task['due_date'] != 'No date':
                response += f"   📅 Due: {task['due_date']}\n"
            response += "\n"
        
        return response
    
    if intent == 'person_update':
        person = analysis['person']
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        
        if not person_tasks:
            return f"👤 *{person}* doesn't have any tasks assigned right now. They might be between projects or focusing on ad-hoc work."
        
        # Show tasks with next steps prominently
        tasks_with_next_steps = [t for t in person_tasks if t['next_step'] and t['next_step'] not in ['', 'Not specified']]
        in_progress = [t for t in person_tasks if t['status'] == 'In progress']
        
        response = f"👤 *Here's what {person} is working on:*\n\n"
        
        if in_progress:
            response += f"*Currently Working On ({len(in_progress)}):*\n"
            for task in in_progress[:4]:
                response += f"• *{task['name']}*"
                if task['due_date'] != 'No date':
                    response += f" (due {task['due_date']})"
                if task['blocker'] not in ['None', 'Not set']:
                    response += f" 🚧 {task['blocker']} blocker"
                response += "\n"
                
                # Show next step if available
                if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                    response += f"  👉 *Next:* {task['next_step']}\n"
                response += "\n"
        
        if tasks_with_next_steps:
            response += f"*Key Next Steps ({len(tasks_with_next_steps)}):*\n"
            for task in tasks_with_next_steps[:3]:
                response += f"• {task['next_step']}\n"
            response += "\n"
        
        response += f"*Summary:* {len(person_tasks)} total tasks • {len([t for t in person_tasks if t['priority'] == 'High'])} high priority"
        return response
    
    elif intent == 'company_update':
        total_tasks = len(tasks)
        in_progress = len([t for t in tasks if t['status'] == 'In progress'])
        blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
        high_priority = len([t for t in tasks if t['priority'] == 'High'])
        
        response = "🏢 *Company Update*\n\n"
        response += f"We have *{total_tasks} active tasks* across the company:\n"
        response += f"• {in_progress} in progress\n"
        response += f"• {blocked} currently blocked\n" 
        response += f"• {high_priority} high priority items\n\n"
        
        # Show critical blockers
        major_blockers = [t for t in tasks if t['blocker'] == 'Major']
        if major_blockers:
            response += "🚨 *Critical items needing attention:*\n"
            for task in major_blockers[:2]:
                response += f"• {task['name']} ({task['department']})\n"
            response += "\n"
        
        # Show key next steps
        important_next_steps = [t for t in tasks if t['next_step'] and t['priority'] == 'High']
        if important_next_steps:
            response += "🎯 *Key next steps this week:*\n"
            for task in important_next_steps[:3]:
                response += f"• {task['next_step']}\n"
        
        return response
    
    elif intent == 'blockers_update':
        blocked_tasks = [t for t in tasks if t['blocker'] not in ['None', 'Not set']]
        
        if not blocked_tasks:
            return "✅ *No blockers right now!* Everything is moving smoothly across all teams."
        
        response = "⚠️ *Here's what needs attention:*\n\n"
        
        major_blockers = [t for t in blocked_tasks if t['blocker'] == 'Major']
        minor_blockers = [t for t in blocked_tasks if t['blocker'] == 'Minor']
        
        if major_blockers:
            response += "🚨 *Major Blockers:*\n"
            for task in major_blockers[:3]:
                owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
                response += f"• *{task['name']}* ({owners})\n"
                if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                    response += f"  👉 *Action needed:* {task['next_step']}\n"
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
            response += f"*{i}. {task['name']}* ({owners})\n"
            response += f"   📍 {task['department']} • Due: {task['due_date']}\n"
            
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"   👉 *Next:* {task['next_step']}\n"
            
            if task['blocker'] not in ['None', 'Not set']:
                response += f"   🚧 {task['blocker']} blocker\n"
            
            response += "\n"
        
        return response
    
    else:  # department_update
        dept = analysis.get('department', 'All')
        dept_tasks = [t for t in tasks if t['department'] == dept] if dept != 'All' else tasks
        
        response = f"📊 *{dept} Department Update*\n\n"
        response += f"*{len(dept_tasks)} active tasks* in progress:\n\n"
        
        status_counts = {}
        for task in dept_tasks:
            status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
        
        for status, count in status_counts.items():
            response += f"• {status}: {count} tasks\n"
        
        # Show key next steps for the department
        dept_next_steps = [t for t in dept_tasks if t['next_step'] and t['next_step'] not in ['', 'Not specified']]
        if dept_next_steps:
            response += f"\n*Key next steps for {dept}:*\n"
            for task in dept_next_steps[:3]:
                response += f"• {task['next_step']}\n"
        
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
            background_tasks.add_task(process_query, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Hmm, I'm having trouble understanding. Try asking about a team member or company status."
        })

async def process_query(query: str, response_url: str):
    """Process query in background"""
    try:
        analysis = await understand_query(query)
        tasks = await get_all_tasks()
        
        if not tasks:
            response = "📭 I don't see any tasks in the system right now. The team might be between projects."
        else:
            response = generate_response(tasks, analysis)
        
        payload = {"response_type": "in_channel", "text": response}
        await send_slack_response(response_url, payload)
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
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
