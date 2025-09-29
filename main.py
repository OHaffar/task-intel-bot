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

# Initialize cache with longer TTL
cache = cachetools.TTLCache(maxsize=100, ttl=60)

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

# Conversation context storage
LAST_QUERY_CONTEXT = {}

# Initialize Notion client with longer timeout
notion = None
try:
    from notion_client import Client
    notion_token = os.getenv('NOTION_TOKEN')
    if notion_token:
        notion = Client(auth=notion_token, timeout_ms=30000)  # 30 seconds timeout
        logger.info("Notion client initialized with 30s timeout")
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

def cleanup_old_contexts():
    """Remove conversation contexts older than 1 hour"""
    current_time = time.time()
    expired_users = []
    
    for user_id, context in LAST_QUERY_CONTEXT.items():
        if current_time - context['timestamp'] > 3600:  # 1 hour
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del LAST_QUERY_CONTEXT[user_id]

async def understand_query(query: str, user_id: str = None) -> Dict:
    """Understand natural language queries with conversation support"""
    # Clean up old contexts first
    cleanup_old_contexts()
    
    if not query:
        return {"intent": "company_update", "tone": "friendly", "confidence": 1.0}
    
    query_lower = query.lower().strip()
    
    # Conversation flow detection
    follow_up_commands = {
        'pipeline': 'person_pipeline',
        'impact': 'person_impact', 
        'all tasks': 'person_all_tasks',
        'all': 'person_all_tasks',
        'blockers': 'person_blockers',
        'blocker': 'person_blockers',
        'upcoming': 'person_pipeline',
        'what\'s next': 'person_pipeline',
        'next': 'person_pipeline',
        'tasks': 'person_all_tasks',
        'show tasks': 'person_all_tasks',
        'list tasks': 'person_all_tasks'
    }
    
    # Check for follow-up commands if we have context
    if user_id and user_id in LAST_QUERY_CONTEXT:
        # Check for exact matches first
        for cmd, intent in follow_up_commands.items():
            if cmd == query_lower:  # Exact match
                return {
                    "intent": intent,
                    "person": LAST_QUERY_CONTEXT[user_id]['person'],
                    "tone": "helpful", 
                    "confidence": 0.95
                }
        
        # Then check for partial matches
        for cmd, intent in follow_up_commands.items():
            if cmd in query_lower and len(query_lower) < 20:  # Short queries likely follow-ups
                return {
                    "intent": intent,
                    "person": LAST_QUERY_CONTEXT[user_id]['person'],
                    "tone": "helpful",
                    "confidence": 0.9
                }
    
    # Greetings and conversational phrases
    greeting_words = ['hi', 'hello', 'hey', 'howdy', 'hiya', 'yo ']
    if any(word in query_lower for word in greeting_words):
        return {"intent": "greeting", "tone": "warm", "confidence": 1.0}
    
    if any(word in query_lower for word in ['thanks', 'thank you', 'appreciate', 'thx']):
        return {"intent": "thanks", "tone": "appreciative", "confidence": 1.0}
    
    # Next steps with variations
    next_steps_words = ['next steps', 'what next', 'what should', 'recommend', 'suggest', 'advice']
    if any(word in query_lower for word in next_steps_words):
        return {"intent": "next_steps", "tone": "helpful", "confidence": 0.9}
    
    # Deadline and weekly tracking with variations
    this_week_words = ['due this week', 'this week', 'weekly tasks', 'week plan', 'current week', 'upcoming week']
    if any(word in query_lower for word in this_week_words):
        return {"intent": "this_week", "tone": "proactive", "confidence": 0.9}
    
    next_week_words = ['due next week', 'next week', 'following week', 'upcoming week']
    if any(word in query_lower for word in next_week_words):
        return {"intent": "next_week", "tone": "forward_looking", "confidence": 0.9}
    
    # Late tasks with variations
    late_words = ['late', 'overdue', 'past due', 'missed deadline', 'deadlines passed', 'behind schedule']
    if any(word in query_lower for word in late_words):
        return {"intent": "late_tasks", "tone": "urgent", "confidence": 0.9}
    
    # Check for team members with fuzzy matching
    for person_key, person_name in TEAM_MEMBERS.items():
        # Direct match or partial match
        if (person_key in query_lower or 
            person_name.lower() in query_lower or
            any(word in query_lower for word in [person_key, person_name.lower()])):
            
            # Store context for conversation flow
            if user_id:
                LAST_QUERY_CONTEXT[user_id] = {
                    'person': person_name,
                    'timestamp': time.time()
                }
            
            # Check for weekly context
            week_context = any(word in query_lower for word in ['week', 'finish', 'complete', 'due', 'deadline'])
            if week_context:
                return {"intent": "person_weekly", "person": person_name, "tone": "supportive", "confidence": 0.8}
            else:
                return {"intent": "person_update", "person": person_name, "tone": "supportive", "confidence": 0.8}
    
    # Check for departments with variations
    dept_patterns = {
        'Tech': ['tech', 'engineering', 'dev', 'developers', 'technical'],
        'Commercial': ['commercial', 'sales', 'business', 'revenue', 'clients'],
        'Operations': ['operations', 'ops', 'operational', 'process'],
        'Finance': ['finance', 'financial', 'money', 'budget', 'revenue']
    }
    
    for dept, patterns in dept_patterns.items():
        if any(pattern in query_lower for pattern in patterns):
            week_context = any(word in query_lower for word in ['week', 'finish', 'complete', 'due'])
            if week_context:
                return {"intent": "department_weekly", "department": dept, "tone": "informative", "confidence": 0.8}
            else:
                return {"intent": "department_update", "department": dept, "tone": "informative", "confidence": 0.8}
    
    # Check for other intents with variations
    company_words = ['brief', 'overview', 'company', 'status', 'update', 'how are we', 'how we doing']
    if any(word in query_lower for word in company_words):
        return {"intent": "company_update", "tone": "confident", "confidence": 0.8}
    
    blocker_words = ['block', 'stuck', 'issue', 'problem', 'blocker', 'impediment', 'obstacle']
    if any(word in query_lower for word in blocker_words):
        return {"intent": "blockers_update", "tone": "concerned", "confidence": 0.8}
    
    priority_words = ['priority', 'important', 'critical', 'urgent', 'high priority', 'p0', 'p1']
    if any(word in query_lower for word in priority_words):
        return {"intent": "priorities_update", "tone": "focused", "confidence": 0.8}
    
    # Help intent for unclear queries
    help_words = ['help', 'what can you do', 'how to use', 'commands', 'options']
    if any(word in query_lower for word in help_words):
        return {"intent": "help", "tone": "friendly", "confidence": 1.0}
    
    # Default to company update with lower confidence
    return {"intent": "company_update", "tone": "friendly", "confidence": 0.5}

async def get_all_tasks() -> List[Dict]:
    """Get all tasks with caching and timeout protection"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        logger.error("Notion client not initialized")
        return tasks
    
    # Fetch all databases with timeout protection
    for dept, db_id in DATABASES.items():
        if not db_id:
            continue
            
        try:
            # Add timeout protection for each database query
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
            logger.warning(f"Timeout fetching {dept} database - skipping")
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
        days_late = 0
        if due_date:
            try:
                due_datetime = datetime.strptime(due_date, '%Y-%m-%d')
                today = datetime.now().date()
                if due_datetime.date() < today:
                    is_late = True
                    days_late = (today - due_datetime.date()).days
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
            'days_late': days_late,
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
    
    if intent == 'greeting':
        return f"""👋 Hey there! I'm your Task Intel Bot. 

I can help you with:
• People updates: "What's Omar working on?"
• Weekly plans: "What's due this week?" 
• Deadlines: "What tasks are late?"
• Team status: "Tech department update"
• Priorities: "High priority items"
• Blockers: "What's stuck?"

Just ask naturally! I understand conversational language."""

    if intent == 'thanks':
        return "🙏 You're welcome! Happy to help. What else can I update you on?"
    
    if intent == 'help':
        return """🤖 *How I can help you:*

*People & Work:*
• "What is [person] working on?"
• "What should [person] finish this week?"
• "Show me [person]'s tasks"

*Time & Deadlines:*
• "What's due this week?"
• "What's due next week?" 
• "What tasks are late?"
• "Show me overdue tasks"

*Teams & Departments:*
• "Tech status" or "Engineering update"
• "Commercial team update" 
• "Operations overview"
• "Finance department"

*Focus Areas:*
• "High priority items"
• "What's urgent?"
• "Current blockers"
• "What's stuck?"
• "Next steps"

Just ask naturally! I understand many variations."""

    # Conversation flow intents
    if intent == 'person_pipeline':
        person = analysis['person']
        return generate_person_pipeline(tasks, person)
    
    if intent == 'person_impact':
        person = analysis['person']
        return generate_person_impact(tasks, person)
    
    if intent == 'person_all_tasks':
        person = analysis['person']
        return generate_person_all_tasks(tasks, person)
    
    if intent == 'person_blockers':
        person = analysis['person']
        return generate_person_blockers(tasks, person)

    # Weekly tasks
    if intent == 'this_week':
        return generate_weekly_tasks(tasks, "this_week")
    
    if intent == 'next_week':
        return generate_weekly_tasks(tasks, "next_week")
    
    # Late tasks
    if intent == 'late_tasks':
        return generate_late_tasks(tasks)
    
    # Person's weekly tasks
    if intent == 'person_weekly':
        person = analysis['person']
        return generate_person_weekly_tasks(tasks, person)
    
    # Department's weekly tasks
    if intent == 'department_weekly':
        dept = analysis.get('department', 'All')
        return generate_department_weekly_tasks(tasks, dept)

    if intent == 'next_steps':
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
            return f"👤 *{person}* doesn't have any tasks assigned right now."
        
        # Comprehensive analysis
        in_progress = [t for t in person_tasks if t['status'] == 'In progress']
        not_started = [t for t in person_tasks if t['status'] == 'Not started']
        completed = [t for t in person_tasks if t['is_completed']]
        high_priority = len([t for t in person_tasks if t['priority'] == 'High'])
        late_tasks = len([t for t in person_tasks if t['is_late'] and not t['is_completed']])
        tasks_with_impact = [t for t in person_tasks if t.get('impact') and t['impact'] not in ['', 'Not specified']]
        
        response = f"👤 *{person}'s Work Status:*\n\n"
        
        # Clear status message
        if not in_progress and not_started:
            response += f"📋 *No active tasks right now* - {person} hasn't started any of their {len(not_started)} assigned tasks yet.\n\n"
        elif not in_progress and completed:
            response += f"✅ *All tasks completed!* {person} has finished all assigned work.\n\n"
        elif not in_progress:
            response += f"⏸️ *No tasks in progress* - {person} is currently between active work.\n\n"
        
        # Show current work if available
        if in_progress:
            response += f"🚀 *Currently Working On ({len(in_progress)}):*\n"
            for task in in_progress:
                response += f"• {task['name']}"
                if task['due_date'] != 'No date':
                    response += f" (due {task['due_date']})"
                response += "\n"
                
                if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                    response += f"  👉 Next: {task['next_step']}\n"
                response += "\n"
        
        # Show upcoming tasks if no active work
        if not in_progress and not_started:
            response += f"📅 *Ready to Start ({len(not_started)} tasks):*\n"
            # Show overdue and high priority first
            priority_tasks = [t for t in not_started if t['is_late'] or t['priority'] == 'High']
            other_tasks = [t for t in not_started if not t['is_late'] and t['priority'] != 'High']
            
            for task in priority_tasks[:3]:
                response += f"• {task['name']}"
                if task['due_date'] != 'No date':
                    response += f" (due {task['due_date']})"
                if task['is_late']:
                    response += f" - {task['days_late']} days overdue"
                if task['priority'] == 'High':
                    response += " 🚨 High Priority"
                response += "\n"
            
            # Fill with other tasks if we have space
            remaining_slots = 3 - len(priority_tasks)
            if remaining_slots > 0 and other_tasks:
                for task in other_tasks[:remaining_slots]:
                    response += f"• {task['name']}"
                    if task['due_date'] != 'No date':
                        response += f" (due {task['due_date']})"
                    response += "\n"
            
            if len(not_started) > 3:
                response += f"... and {len(not_started) - 3} more tasks\n"
            response += "\n"
        
        # Summary
        response += f"📊 *Summary:* {len(person_tasks)} total tasks"
        if in_progress:
            response += f" • {len(in_progress)} in progress"
        if not_started:
            response += f" • {len(not_started)} not started"
        if completed:
            response += f" • {len(completed)} completed"
        if high_priority > 0:
            response += f" • {high_priority} high priority"
        if late_tasks > 0:
            response += f" • {late_tasks} overdue"
        response += "\n\n"
        
        # Smart follow-ups
        follow_ups = []
        if not_started:
            follow_ups.append("'pipeline' to see all upcoming tasks")
        if tasks_with_impact:
            follow_ups.append("'impact' to see business impact")
        if len(person_tasks) > 0:
            follow_ups.append("'all tasks' for complete breakdown")
        if any(t['blocker'] not in ['None', 'Not set'] for t in person_tasks):
            follow_ups.append("'blockers' to see any impediments")
        
        if follow_ups:
            response += "💡 *Want more details?* Reply with:\n"
            for option in follow_ups:
                response += f"• {option}\n"
        
        return response
    
    elif intent == 'company_update':
        total_tasks = len(tasks)
        in_progress = len([t for t in tasks if t['status'] == 'In progress'])
        blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
        high_priority = len([t for t in tasks if t['priority'] == 'High'])
        late_tasks = len([t for t in tasks if t['is_late'] and not t['is_completed']])
        
        response = "🏢 *Company Update*\n\n"
        response += f"We have *{total_tasks} active tasks* across the company:\n"
        response += f"• {in_progress} in progress\n"
        response += f"• {blocked} currently blocked\n" 
        response += f"• {high_priority} high priority items\n"
        response += f"• {late_tasks} overdue tasks\n\n"
        
        major_blockers = [t for t in tasks if t['blocker'] == 'Major']
        if major_blockers:
            response += "🚨 *Critical items needing attention:*\n"
            for task in major_blockers[:2]:
                response += f"• {task['name']} ({task['department']})\n"
            response += "\n"
        
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
        
        dept_next_steps = [t for t in dept_tasks if t['next_step'] and t['next_step'] not in ['', 'Not specified']]
        if dept_next_steps:
            response += f"\n*Key next steps for {dept}:*\n"
            for task in dept_next_steps[:3]:
                response += f"• {task['next_step']}\n"
        
        return response

def generate_weekly_tasks(tasks: List[Dict], week_type: str) -> str:
    """Generate weekly tasks overview"""
    today = datetime.now().date()
    
    if week_type == "this_week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
        title = "This Week"
    else:  # next_week
        start_date = today + timedelta(days=(7 - today.weekday()))
        end_date = start_date + timedelta(days=6)
        title = "Next Week"
    
    weekly_tasks = []
    for task in tasks:
        if task['due_date'] != 'No date' and not task['is_completed']:
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_date <= due_date <= end_date:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    if not weekly_tasks:
        return f"📅 *{title}'s Tasks ({start_date} to {end_date}):*\nNo tasks due {title.lower()}. The team may be working on ongoing projects."
    
    response = f"📅 *{title}'s Deadlines ({start_date} to {end_date}):*\n\n"
    
    dept_groups = {}
    for task in weekly_tasks:
        dept = task['department']
        if dept not in dept_groups:
            dept_groups[dept] = []
        dept_groups[dept].append(task)
    
    for dept, dept_tasks in dept_groups.items():
        response += f"*{dept} Department ({len(dept_tasks)} tasks):*\n"
        for task in dept_tasks[:5]:
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"• {task['name']} ({owners}) - Due: {task['due_date']}\n"
            if task['priority'] == 'High':
                response += "  🚨 High Priority\n"
        response += "\n"
    
    return response

def generate_late_tasks(tasks: List[Dict]) -> str:
    """Generate late tasks report"""
    late_tasks = [t for t in tasks if t['is_late'] and not t['is_completed']]
    
    if not late_tasks:
        return "✅ *No late tasks!* Everything is on schedule. Great work team! 🎉"
    
    response = "⚠️ *Overdue Tasks - Needs Attention:*\n\n"
    
    late_tasks.sort(key=lambda x: x['days_late'], reverse=True)
    
    for i, task in enumerate(late_tasks[:10], 1):
        owners = ', '.join(task['owners']) if task['owners'] else 'Unassigned'
        
        response += f"*{i}. {task['name']}*\n"
        response += f"   👤 {owners} • 📍 {task['department']}\n"
        response += f"   📅 Due: {task['due_date']} ({task['days_late']} day{'s' if task['days_late'] != 1 else ''} late)\n"
        
        if task['priority'] == 'High':
            response += "   🚨 High Priority\n"
        
        if task['blocker'] not in ['None', 'Not set']:
            response += f"   🚧 Blocker: {task['blocker']}\n"
        
        if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
            response += f"   👉 Next: {task['next_step']}\n"
        
        response += "\n"
    
    if len(late_tasks) > 10:
        response += f"... and {len(late_tasks) - 10} more overdue tasks\n"
    
    return response

def generate_person_weekly_tasks(tasks: List[Dict], person: str) -> str:
    """Generate weekly tasks for a specific person"""
    today = datetime.now().date()
    start_date = today - timedelta(days=today.weekday())
    end_date = start_date + timedelta(days=6)
    
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    weekly_tasks = []
    
    for task in person_tasks:
        if task['due_date'] != 'No date' and not task['is_completed']:
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_date <= due_date <= end_date:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    response = f"👤 *{person}'s Week Ahead ({start_date} to {end_date}):*\n\n"
    
    if not weekly_tasks:
        response += f"No specific tasks due this week. {person} may be working on ongoing projects."
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

def generate_department_weekly_tasks(tasks: List[Dict], department: str) -> str:
    """Generate weekly tasks for a specific department"""
    today = datetime.now().date()
    start_date = today - timedelta(days=today.weekday())
    end_date = start_date + timedelta(days=6)
    
    dept_tasks = [t for t in tasks if t['department'] == department]
    weekly_tasks = []
    
    for task in dept_tasks:
        if task['due_date'] != 'No date' and not task['is_completed']:
            try:
                due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
                if start_date <= due_date <= end_date:
                    weekly_tasks.append(task)
            except ValueError:
                continue
    
    response = f"📊 *{department} Department - This Week ({start_date} to {end_date}):*\n\n"
    
    if not weekly_tasks:
        response += f"No specific tasks due this week. The {department} team may be working on ongoing projects."
        return response
    
    response += f"*{len(weekly_tasks)} tasks due this week:*\n\n"
    
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
        for task in other_priority[:8]:
            owners = ', '.join(task['owners']) if task['owners'] else 'Team'
            response += f"• {task['name']} ({owners}) - Due: {task['due_date']}\n"
    
    return response

# Conversation flow functions
def generate_person_pipeline(tasks: List[Dict], person: str) -> str:
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    not_started = [t for t in person_tasks if t['status'] == 'Not started']
    
    response = f"📋 *{person}'s Pipeline - Upcoming Tasks:*\n\n"
    
    if not_started:
        for task in not_started:
            response += f"• **{task['name']}**\n"
            if task['due_date'] != 'No date':
                response += f"  📅 Due: {task['due_date']}"
                if task['is_late']:
                    response += f" ({task['days_late']} days overdue!)"
                response += "\n"
            if task['priority'] == 'High':
                response += f"  🚨 High Priority\n"
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"  👉 Next: {task['next_step']}\n"
            response += "\n"
    else:
        response += f"✨ {person} has no upcoming tasks. Everything is in progress or completed!\n"
    
    return response

def generate_person_impact(tasks: List[Dict], person: str) -> str:
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    tasks_with_impact = [t for t in person_tasks if t.get('impact') and t['impact'] not in ['', 'Not specified']]
    
    response = f"📈 *Business Impact - {person}'s Tasks:*\n\n"
    
    if tasks_with_impact:
        for task in tasks_with_impact:
            response += f"• **{task['name']}**\n"
            response += f"  🎯 Impact: {task['impact']}\n\n"
    else:
        response += f"📝 No impact descriptions available for {person}'s tasks yet.\n"
    
    return response

def generate_person_all_tasks(tasks: List[Dict], person: str) -> str:
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    
    if not person_tasks:
        return f"📭 *{person} has no tasks assigned.*"
    
    # Categorize tasks by status
    in_progress = [t for t in person_tasks if t['status'] == 'In progress']
    not_started = [t for t in person_tasks if t['status'] == 'Not started'] 
    completed = [t for t in person_tasks if t['is_completed']]
    
    response = f"📊 *All Tasks - {person}:*\n\n"
    
    # Current active work - SHOW THE ACTUAL TASKS
    if in_progress:
        response += f"🚀 *In Progress ({len(in_progress)}):*\n"
        for task in in_progress:
            response += f"• **{task['name']}**\n"
            if task['due_date'] != 'No date':
                response += f"  📅 Due: {task['due_date']}"
                if task['is_late']:
                    response += f" ({task['days_late']} days overdue!)"
                response += "\n"
            if task['priority'] == 'High':
                response += f"  🚨 High Priority\n"
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"  👉 Next: {task['next_step']}\n"
            if task['blocker'] not in ['None', 'Not set']:
                response += f"  🚧 Blocker: {task['blocker']}\n"
            response += "\n"
    
    # Upcoming tasks - SHOW THE ACTUAL TASKS
    if not_started:
        response += f"📋 *Not Started ({len(not_started)}):*\n"
        
        # Show all not started tasks with details
        for task in not_started:
            response += f"• **{task['name']}**\n"
            if task['due_date'] != 'No date':
                response += f"  📅 Due: {task['due_date']}"
                if task['is_late']:
                    response += f" ({task['days_late']} days overdue!)"
                response += "\n"
            if task['priority'] == 'High':
                response += f"  🚨 High Priority\n"
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"  👉 Next: {task['next_step']}\n"
            if task['blocker'] not in ['None', 'Not set']:
                response += f"  🚧 Blocker: {task['blocker']}\n"
            response += "\n"
    
    # Completed work - SHOW THE ACTUAL TASKS
    if completed:
        response += f"✅ *Completed ({len(completed)}):*\n"
        for task in completed:
            response += f"• {task['name']}\n"
        response += "\n"
    
    # Data summary
    response += "📈 *Task Status Summary:*\n"
    response += f"• Total assigned: {len(person_tasks)}\n"
    if in_progress:
        response += f"• In progress: {len(in_progress)}\n"
    if not_started:
        response += f"• Not started: {len(not_started)}\n"
    if completed:
        response += f"• Completed: {len(completed)}\n"
    
    late_tasks = [t for t in person_tasks if t['is_late'] and not t['is_completed']]
    high_priority_active = [t for t in person_tasks if t['priority'] == 'High' and not t['is_completed']]
    
    if late_tasks:
        response += f"• Overdue: {len(late_tasks)}\n"
    if high_priority_active:
        response += f"• High priority: {len(high_priority_active)}\n"
    
    return response

def generate_person_blockers(tasks: List[Dict], person: str) -> str:
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    blocked_tasks = [t for t in person_tasks if t['blocker'] not in ['None', 'Not set']]
    
    response = f"🚧 *Blockers - {person}:*\n\n"
    
    if blocked_tasks:
        for task in blocked_tasks:
            response += f"• **{task['name']}**\n"
            response += f"  🚧 **Blocker:** {task['blocker']}\n"
            if task['due_date'] != 'No date':
                response += f"  📅 Due: {task['due_date']}"
                if task['is_late']:
                    response += f" ({task['days_late']} days overdue!)"
                response += "\n"
            if task['priority'] == 'High':
                response += f"  🚨 High Priority\n"
            if task['next_step'] and task['next_step'] not in ['', 'Not specified']:
                response += f"  👉 Next: {task['next_step']}\n"
            response += "\n"
    else:
        response += f"✅ No blockers for {person}! Everything is moving smoothly.\n"
    
    return response

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands with conversation context"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        user_id = form_data.get("user_id")
        
        logger.info(f"User {user_id} asked: '{query}'")
        
        # Immediate response with helpful message for cold starts
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Gathering your task info... (This might take 20-30 seconds if I was sleeping 😴)"
        }
        
        # Process in background with user context
        if response_url:
            background_tasks.add_task(process_query_with_context, query, response_url, user_id)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ I'm having trouble right now. Try again in 30 seconds."
        })

async def process_query_with_context(query: str, response_url: str, user_id: str):
    """Process query in background with conversation context"""
    try:
        analysis = await understand_query(query, user_id)
        tasks = await get_all_tasks()
        
        if not tasks:
            response = "📭 I'm having trouble connecting to the task database right now. This often happens when I'm waking up. Try again in 30 seconds!"
        else:
            response = generate_response(tasks, analysis)
        
        payload = {"response_type": "in_channel", "text": response}
        await send_slack_response(response_url, payload)
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        error_msg = "❌ Sorry, I'm having trouble pulling the latest updates. Try again in a moment."
        await send_slack_response(response_url, {"response_type": "in_channel", "text": error_msg})

async def process_query(query: str, response_url: str):
    """Legacy function - keep for compatibility"""
    await process_query_with_context(query, response_url, "default_user")

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
