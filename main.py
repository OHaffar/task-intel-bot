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

# Initialize cache with longer TTL for better performance
cache = cachetools.TTLCache(maxsize=100, ttl=60)  # Increased from 30 to 60 seconds

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

# Initialize Notion client with better timeout handling
notion = None
try:
    from notion_client import Client
    notion_token = os.getenv('NOTION_TOKEN')
    if notion_token:
        notion = Client(auth=notion_token, timeout_ms=30000)  # Increased timeout
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
    
    # Check for deadline-related queries
    if any(word in query_lower for word in ['late', 'overdue', 'past due', 'past deadline', 'missed deadline']):
        return {"intent": "late_tasks", "tone": "urgent"}
    
    if any(word in query_lower for word in ['finish this week', 'due this week', 'weekly tasks', 'this week']):
        return {"intent": "weekly_tasks", "tone": "proactive"}
    
    if any(word in query_lower for word in ['on time', 'timely', 'deadline performance', 'finishing on time']):
        return {"intent": "timeliness", "tone": "analytical"}
    
    # Check for team members
    for person_key, person_name in TEAM_MEMBERS.items():
        if person_key in query_lower:
            # Check if it's about weekly tasks for a person
            if 'week' in query_lower or 'finish' in query_lower:
                return {"intent": "person_weekly", "person": person_name, "tone": "supportive"}
            return {"intent": "person_update", "person": person_name, "tone": "supportive"}
    
    # Check for departments with weekly focus
    if any(word in query_lower for word in ['tech', 'engineering']):
        if 'week' in query_lower or 'finish' in query_lower:
            return {"intent": "department_weekly", "department": "Tech", "tone": "informative"}
        return {"intent": "department_update", "department": "Tech", "tone": "informative"}
    elif any(word in query_lower for word in ['commercial', 'sales', 'business']):
        if 'week' in query_lower or 'finish' in query_lower:
            return {"intent": "department_weekly", "department": "Commercial", "tone": "informative"}
        return {"intent": "department_update", "department": "Commercial", "tone": "informative"}
    elif any(word in query_lower for word in ['operations', 'ops']):
        if 'week' in query_lower or 'finish' in query_lower:
            return {"intent": "department_weekly", "department": "Operations", "tone": "informative"}
        return {"intent": "department_update", "department": "Operations", "tone": "informative"}
    elif any(word in query_lower for word in ['finance', 'financial']):
        if 'week' in query_lower or 'finish' in query_lower:
            return {"intent": "department_weekly", "department": "Finance", "tone": "informative"}
        return {"intent": "department_update", "department": "Finance", "tone": "informative"}
    
    # Check for other intents
    if any(word in query_lower for word in ['brief', 'overview', 'company', 'status', 'update']):
        return {"intent": "company_update", "tone": "confident"}
    
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem', 'blocker']):
        return {"intent": "blockers_update", "tone": "concerned"}
    
    if any(word in query_lower for word in ['priority', 'important', 'critical', 'urgent']):
        return {"intent": "priorities_update", "tone": "focused"}
    
    # Default to helpful response
    return {"intent": "help", "tone": "friendly"}

async def get_all_tasks() -> List[Dict]:
    """Get all tasks with caching and timeout handling"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        logger.error("Notion client not initialized")
        return tasks
    
    # Fetch all databases with timeout protection
    for dept, db_id in DATABASES.items():
        if db_id:
            try:
                # Use asyncio timeout to prevent hanging
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, 
                        lambda: notion.databases.query(database_id=db_id, page_size=100)
                    ),
                    timeout=25.0  # 25 second timeout per database
                )
                
                for page in result.get('results', []):
                    task = parse_task(page, dept)
                    if task:
                        tasks.append(task)
                        
            except asyncio.TimeoutError:
                logger.warning(f"Timeout fetching {dept} database")
                continue
            except Exception as e:
                logger.error(f"Error fetching {dept}: {e}")
                continue
    
    cache[cache_key] = tasks
    return tasks

def parse_task(page: Dict, department: str) -> Optional[Dict]:
    """Parse task using manual user ID mapping with due date analysis"""
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
        due_date = due_date_raw.split('T')[0] if due_date_raw else None
        
        # Calculate if task is late
        is_late = False
        if due_date:
            try:
                due_datetime = datetime.strptime(due_date, '%Y-%m-%d')
                is_late = due_datetime.date() < datetime.now().date()
            except ValueError:
                pass
        
        status = get_property(props, 'Status', 'select')
        
        return {
            'name': name,
            'owners': owners,
            'status': status,
            'due_date': due_date if due_date else 'No date',
            'next_step': get_property(props, 'Next Steps', 'rich_text'),
            'blocker': get_property(props, 'Blocker', 'select'),
            'impact': get_property(props, 'Impact', 'rich_text'),
            'priority': get_property(props, 'Priority', 'select'),
            'department': department,
            'is_late': is_late,
            'is_completed': status.lower() in ['done', 'completed', 'finished']
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
        return f"👋 Hey there! I'm your Task Intel Bot. I can tell you what everyone's working on, deadlines, late tasks, and weekly priorities. What would you like to know?"
    
    if intent == 'thanks':
        return "🙏 You're welcome! Happy to help. What else can I update you on?"
    
    if intent == 'help':
        return """🤖 *How I can help you:*

• *People:* "What is Omar working on?" or "What should Derrick finish this week?"
• *Deadlines:* "What tasks are late?" or "Are we on time with deadlines?"
• *Weekly Focus:* "What should Tech finish this week?" or "Weekly priorities"
• *Company:* "Company update" or "How are we doing?"
• *Blockers:* "What's blocked?" or "Any issues?"
• *Priorities:* "High priority items" or "What's urgent?"
• *Departments:* "Tech status" or "Commercial update"

Just ask naturally! I understand conversational language."""

    if intent == 'late_tasks':
        late_tasks = [t for t in tasks if t['is_late'] and not t['is_completed']]
        
        if not late_tasks:
            return "✅ *No late tasks!* Everything is on schedule. Great work team! 🎉"
        
        response = "⚠️ *Late Tasks - Needs Attention:*\n\n"
        
        for i, task in enumerate(late_tasks[:8], 1):
            owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
            days_late = (datetime.now().date() - datetime.strptime(task['due_date'], '%Y-%m-%d').date()).days
            
            response += f"*{i}. {task['name']}*\n"
            response += f"   👤 {owners} • 📍 {task['department']}\n"
            response += f"   📅 Due: {task['due_date']} ({days_late} day{'s' if days_late != 1 else ''} late)\n"
            
            if task['blocker'] not in ['None', 'Not set']:
                response += f"   🚧 Blocker: {task['blocker']}\n"
            
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"   👉 Next: {task['next_step']}\n"
            
            response += "\n"
        
        if len(late_tasks) > 8:
            response += f"... and {len(late_tasks) - 8} more late tasks\n"
        
        return response

    if intent == 'timeliness':
        # Analyze on-time completion
        tasks_with_dates = [t for t in tasks if t['due_date'] != 'No date']
        completed_tasks = [t for t in tasks_with_dates if t['is_completed']]
        on_time_tasks = [t for t in completed_tasks if not t['is_late']]
        late_tasks = [t for t in tasks_with_dates if t['is_late'] and not t['is_completed']]
        
        if not tasks_with_dates:
            return "📊 *Timeliness Analysis:*\nNot enough data with due dates to analyze deadline performance."
        
        on_time_rate = len(on_time_tasks) / len(completed_tasks) * 100 if completed_tasks else 0
        
        response = "📊 *Timeliness Report:*\n\n"
        response += f"• {len(completed_tasks)}/{len(tasks_with_dates)} tasks with due dates completed\n"
        response += f"• {len(on_time_tasks)} completed on time ({on_time_rate:.1f}% on-time rate)\n"
        response += f"• {len(late_tasks)} currently overdue\n\n"
        
        if on_time_rate > 80:
            response += "🎉 *Excellent timing!* The team is doing great with deadlines.\n"
        elif on_time_rate > 60:
            response += "👍 *Good progress!* Most tasks are completed on time.\n"
        else:
            response += "💡 *Opportunity for improvement* in meeting deadlines.\n"
        
        # Show individuals with most late tasks
        person_late_count = {}
        for task in late_tasks:
            for owner in task['owners']:
                person_late_count[owner] = person_late_count.get(owner, 0) + 1
        
        if person_late_count:
            response += "\n*Most overdue tasks by person:*\n"
            for person, count in sorted(person_late_count.items(), key=lambda x: x[1], reverse=True)[:3]:
                response += f"• {person}: {count} late tasks\n"
        
        return response

    if intent == 'weekly_tasks':
        return generate_weekly_overview(tasks)

    if intent == 'person_weekly':
        person = analysis['person']
        return generate_person_weekly(tasks, person)

    if intent == 'department_weekly':
        dept = analysis.get('department', 'All')
        return generate_department_weekly(tasks, dept)

    # ... (rest of your existing intent handlers remain the same)
    # [Keep all your existing intent handlers like next_steps, person_update, company_update, etc.]

def generate_weekly_overview(tasks: List[Dict]) -> str:
    """Generate weekly tasks overview"""
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    weekly_tasks = []
    for task in tasks:
        if task['due_date'] != 'No date':
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_of_week <= due_date <= end_of_week and not task['is_completed']:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    if not weekly_tasks:
        return "📅 *This Week's Tasks:*\nNo specific tasks due this week. The team may be working on ongoing projects."
    
    response = f"📅 *This Week's Focus ({start_of_week} to {end_of_week}):*\n\n"
    
    # Group by department
    dept_groups = {}
    for task in weekly_tasks:
        dept = task['department']
        if dept not in dept_groups:
            dept_groups[dept] = []
        dept_groups[dept].append(task)
    
    for dept, dept_tasks in dept_groups.items():
        response += f"*{dept} Department ({len(dept_tasks)} tasks):*\n"
        for task in dept_tasks[:5]:  # Limit to 5 per department
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"• {task['name']} ({owners}) - Due: {task['due_date']}\n"
        response += "\n"
    
    return response

def generate_person_weekly(tasks: List[Dict], person: str) -> str:
    """Generate weekly tasks for a specific person"""
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    weekly_tasks = []
    
    for task in person_tasks:
        if task['due_date'] != 'No date':
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_of_week <= due_date <= end_of_week and not task['is_completed']:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    response = f"👤 *{person}'s Week Ahead ({start_of_week} to {end_of_week}):*\n\n"
    
    if not weekly_tasks:
        response += f"No specific tasks due this week. {person} may be working on ongoing projects or tasks without specific deadlines."
        return response
    
    response += f"*{len(weekly_tasks)} tasks due this week:*\n\n"
    
    for i, task in enumerate(weekly_tasks, 1):
        response += f"*{i}. {task['name']}*\n"
        response += f"   📍 {task['department']} • 📅 Due: {task['due_date']}\n"
        response += f"   🎯 Priority: {task['priority']}\n"
        
        if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
            response += f"   👉 Next: {task['next_step']}\n"
        
        response += "\n"
    
    return response

def generate_department_weekly(tasks: List[Dict], department: str) -> str:
    """Generate weekly tasks for a specific department"""
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)
    
    dept_tasks = [t for t in tasks if t['department'] == department]
    weekly_tasks = []
    
    for task in dept_tasks:
        if task['due_date'] != 'No date':
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_of_week <= due_date <= end_of_week and not task['is_completed']:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    response = f"📊 *{department} Department - This Week ({start_of_week} to {end_of_week}):*\n\n"
    
    if not weekly_tasks:
        response += f"No specific tasks due this week. The {department} team may be working on ongoing projects."
        return response
    
    response += f"*{len(weekly_tasks)} tasks due this week:*\n\n"
    
    # Group by priority
    high_priority = [t for t in weekly_tasks if t['priority'] == 'High']
    other_priority = [t for t in weekly_tasks if t['priority'] != 'High']
    
    if high_priority:
        response += "🚨 *High Priority:*\n"
        for task in high_priority:
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"• {task['name']} ({owners}) - Due: {task['due_date']}\n"
        response += "\n"
    
    if other_priority:
        response += "📋 *Other Tasks:*\n"
        for task in other_priority[:8]:  # Limit display
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"• {task['name']} ({owners})\n"
    
    return response

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands with conversational responses and timeout protection"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        # Immediate response
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Let me check on that for you..."
        }
        
        # Process in background with timeout
        if response_url:
            background_tasks.add_task(process_query_with_timeout, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Hmm, I'm having trouble understanding. Try asking about a team member or company status."
        })

async def process_query_with_timeout(query: str, response_url: str):
    """Process query with timeout protection"""
    try:
        # Set a timeout for the entire processing
        async with asyncio.timeout(28.0):  # 28 second overall timeout
            analysis = await understand_query(query)
            tasks = await get_all_tasks()
            
            if not tasks:
                response = "📭 I don't see any tasks in the system right now. The team might be between projects."
            else:
                response = generate_response(tasks, analysis)
            
            payload = {"response_type": "in_channel", "text": response}
            await send_slack_response(response_url, payload)
            
    except asyncio.TimeoutError:
        logger.error("Processing timeout exceeded")
        error_msg = "⏰ Sorry, I'm taking too long to respond. The system might be busy. Try again in a moment with a more specific question."
        await send_slack_response(response_url, {"response_type": "in_channel", "text": error_msg})
    except Exception as e:
        logger.error(f"Processing error: {e}")
        error_msg = "❌ Sorry, I'm having trouble pulling the latest updates. Try again in a moment."
        await send_slack_response(response_url, {"response_type": "in_channel", "text": error_msg})

async def send_slack_response(response_url: str, payload: Dict):
    """Send response to Slack with retry logic"""
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(response_url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        return
                    else:
                        logger.warning(f"Slack response attempt {attempt + 1} failed: {await resp.text()}")
                        
            if attempt < max_retries:
                await asyncio.sleep(1)  # Wait before retry
                
        except Exception as e:
            logger.error(f"Failed to send to Slack (attempt {attempt + 1}): {e}")
            if attempt == max_retries:
                logger.error("All attempts to send to Slack failed")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
