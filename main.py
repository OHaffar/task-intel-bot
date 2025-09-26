from fastapi import FastAPI, Request, BackgroundTasks, Form
from fastapi.responses import JSONResponse
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

# Initialize cache with longer TTL
cache = cachetools.TTLCache(maxsize=100, ttl=300)  # 5 minutes instead of 30 seconds

# Add timeout configuration
NOTION_TIMEOUT = 30  # seconds
SLACK_TIMEOUT = 10   # seconds

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

# Initialize Notion client with better error handling
notion = None
try:
    from notion_client import Client
    notion_token = os.getenv('NOTION_TOKEN')
    if notion_token:
        notion = Client(auth=notion_token, timeout_ms=30000)  # Increased timeout
        logger.info("Notion client initialized")
    else:
        logger.warning("NOTION_TOKEN not found in environment variables")
except Exception as e:
    logger.error(f"Notion init failed: {e}")

@app.get("/")
async def home():
    return {"status": "ready", "service": "Enhanced Task Intel"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Add timeout handling function
async def fetch_with_timeout(coroutine, timeout_seconds=NOTION_TIMEOUT):
    """Execute a coroutine with a timeout"""
    try:
        return await asyncio.wait_for(coroutine, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.error(f"Operation timed out after {timeout_seconds} seconds")
        return None
    except Exception as e:
        logger.error(f"Error in fetch_with_timeout: {e}")
        return None

async def understand_query_enhanced(query: str) -> Dict:
    """Enhanced query understanding with more natural language support"""
    if not query:
        return {"intent": "team_overview"}
    
    query_lower = query.lower().strip()
    
    # Greetings and conversational phrases
    if any(word in query_lower for word in ['hi', 'hello', 'hey', 'howdy', 'good morning', 'good afternoon']):
        return {"intent": "greeting"}
    
    if any(word in query_lower for word in ['thanks', 'thank you', 'appreciate it']):
        return {"intent": "thanks"}
    
    if any(word in query_lower for word in ['help', 'what can you do', 'options']):
        return {"intent": "help"}
    
    # NEW: On-time completion queries
    if any(word in query_lower for word in ['on time', 'on-time', 'finishing time', 'timely', 'deadline']):
        if 'people' in query_lower or 'team' in query_lower:
            return {"intent": "on_time_completion"}
    
    # NEW: Past due tasks
    past_due_phrases = ['past due', 'past deadline', 'overdue', 'late', 'missed deadline']
    if any(phrase in query_lower for phrase in past_due_phrases):
        return {"intent": "past_due_tasks"}
    
    # NEW: This week tasks
    if 'this week' in query_lower:
        if 'department' in query_lower:
            # Extract department
            for dept in ['tech', 'commercial', 'operations', 'finance']:
                if dept in query_lower:
                    return {"intent": "department_this_week", "department": dept.capitalize()}
            return {"intent": "department_this_week", "department": "All"}
        
        # Check for specific person
        for person in TEAM_MEMBERS:
            if person.lower() in query_lower:
                return {"intent": "person_this_week", "person": person}
        
        return {"intent": "all_this_week"}
    
    # NEW: Weekly completion queries
    if any(word in query_lower for word in ['finish this week', 'due this week', 'weekly tasks']):
        for person in TEAM_MEMBERS:
            if person.lower() in query_lower:
                return {"intent": "person_this_week", "person": person}
        
        for dept in ['tech', 'commercial', 'operations', 'finance']:
            if dept in query_lower:
                return {"intent": "department_this_week", "department": dept.capitalize()}
        
        return {"intent": "all_this_week"}
    
    # Team and workload queries
    team_phrases = [
        'team', 'everyone', 'workload', 'capacity', 'who', 'whole team',
        'all', 'everybody', 'entire team', 'company', 'organization'
    ]
    if any(phrase in query_lower for phrase in team_phrases):
        if any(word in query_lower for word in ['busy', 'load', 'capacity', 'workload', 'utilization']):
            return {"intent": "team_workload"}
        return {"intent": "team_overview"}
    
    # Task count queries
    count_phrases = ['many', 'much', 'count', 'number', 'how', 'total', 'amount']
    if any(phrase in query_lower for phrase in count_phrases):
        if 'task' in query_lower:
            return {"intent": "task_counts"}
        # Check for specific person task counts
        for person in TEAM_MEMBERS:
            if person.lower() in query_lower:
                return {"intent": "person_task_count", "person": person}
    
    # Individual person queries
    for person in TEAM_MEMBERS:
        if person.lower() in query_lower:
            # Check if it's asking about workload/count specifically
            if any(word in query_lower for word in ['many', 'much', 'count', 'number', 'how']):
                return {"intent": "person_task_count", "person": person}
            return {"intent": "person_detail", "person": person}
    
    # Department queries
    if any(word in query_lower for word in ['tech', 'engineering', 'developers', 'technical']):
        return {"intent": "department", "department": "Tech"}
    elif any(word in query_lower for word in ['commercial', 'sales', 'business', 'revenue']):
        return {"intent": "department", "department": "Commercial"}
    elif any(word in query_lower for word in ['operations', 'ops', 'operational']):
        return {"intent": "department", "department": "Operations"}
    elif any(word in query_lower for word in ['finance', 'financial', 'money', 'budget']):
        return {"intent": "department", "department": "Finance"}
    
    # Status and priority queries
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem', 'blocker', 'impediment']):
        return {"intent": "blockers"}
    elif any(word in query_lower for word in ['priority', 'important', 'critical', 'urgent', 'high priority']):
        return {"intent": "priorities"}
    elif any(word in query_lower for word in ['progress', 'working', 'doing', 'active', 'in progress']):
        return {"intent": "in_progress"}
    elif any(word in query_lower for word in ['todo', 'to do', 'upcoming', 'backlog']):
        return {"intent": "todo"}
    elif any(word in query_lower for word in ['done', 'completed', 'finished']):
        return {"intent": "done"}
    
    # General overview queries
    overview_phrases = ['overview', 'summary', 'brief', 'status', 'update', 'report', 'situation']
    if any(phrase in query_lower for phrase in overview_phrases):
        return {"intent": "company_overview"}
    
    # Default to team overview for ambiguous queries
    return {"intent": "team_overview"}

async def get_all_tasks_fast() -> List[Dict]:
    """Fast task fetching with timeout handling"""
    cache_key = "all_tasks"
    if cache_key in cache:
        return cache[cache_key]
    
    tasks = []
    if not notion:
        logger.error("Notion client not initialized")
        return tasks
    
    fetch_tasks = []
    for dept, db_id in DATABASES.items():
        if db_id:
            # Wrap each fetch in timeout handling
            task = fetch_with_timeout(fetch_database_fast(db_id, dept))
            fetch_tasks.append(task)
    
    if fetch_tasks:
        try:
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, list):
                    tasks.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"Error in task gathering: {result}")
        except Exception as e:
            logger.error(f"Error gathering tasks: {e}")
    
    if tasks:  # Only cache if we got results
        cache[cache_key] = tasks
    return tasks

async def fetch_database_fast(db_id: str, dept: str) -> List[Dict]:
    """Fast database fetching with better error handling"""
    try:
        # Use run_in_executor with timeout
        result = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: notion.databases.query(
                database_id=db_id, 
                page_size=100  # Increased page size
            )
        )
        
        tasks = []
        for page in result.get('results', []):
            task = parse_task_enhanced(page, dept)  # Use enhanced parser
            if task:
                tasks.append(task)
        
        logger.info(f"Fetched {len(tasks)} tasks from {dept} department")
        return tasks
    except Exception as e:
        logger.error(f"Error fetching {dept} database {db_id}: {e}")
        return []

def parse_task_enhanced(page: Dict, department: str) -> Optional[Dict]:
    """Enhanced task parsing with due date handling"""
    try:
        props = page.get('properties', {})
        
        # Name extraction
        name_field = props.get('Task Name', {}) or props.get('Name', {})
        titles = name_field.get('title', [])
        name = titles[0].get('plain_text', '') if titles else ''
        if not name:
            return None
        
        # Owner extraction
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id and user_id in USER_ID_TO_NAME:
                owners.append(USER_ID_TO_NAME[user_id])
        
        # Enhanced date handling
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        due_date = None
        is_past_due = False
        is_due_this_week = False
        
        if due_date_raw:
            try:
                # Handle both date and datetime formats
                if 'T' in due_date_raw:
                    due_date = datetime.fromisoformat(due_date_raw.replace('Z', '+00:00')).date()
                else:
                    due_date = datetime.fromisoformat(due_date_raw).date()
                
                today = date.today()
                is_past_due = due_date < today
                
                # Check if due this week (Monday to Sunday)
                start_of_week = today - timedelta(days=today.weekday())
                end_of_week = start_of_week + timedelta(days=6)
                is_due_this_week = start_of_week <= due_date <= end_of_week
                
                due_date_str = due_date.isoformat()
            except Exception as e:
                logger.warning(f"Error parsing date {due_date_raw}: {e}")
                due_date_str = due_date_raw
                is_past_due = False
                is_due_this_week = False
        else:
            due_date_str = 'No date'
        
        # Status with completion tracking
        status = props.get('Status', {}).get('select', {}).get('name', 'Not set')
        is_completed = status.lower() in ['done', 'completed', 'finished']
        
        return {
            'name': name,
            'owners': owners,
            'status': status,
            'due_date': due_date_str,
            'actual_due_date': due_date,  # For date comparisons
            'is_past_due': is_past_due,
            'is_due_this_week': is_due_this_week,
            'is_completed': is_completed,
            'priority': props.get('Priority', {}).get('select', {}).get('name', 'Not set'),
            'blocker': props.get('Blocker', {}).get('select', {}).get('name', 'Not set'),
            'department': department,
        }
    except Exception as e:
        logger.error(f"Error parsing task: {e}")
        return None

def generate_enhanced_response(tasks: List[Dict], analysis: Dict) -> str:
    """Enhanced response generation with new features"""
    intent = analysis.get('intent', 'team_overview')
    
    if intent == 'greeting':
        return generate_greeting_response(tasks)
    elif intent == 'thanks':
        return "🙏 You're welcome! Happy to help. What else would you like to know?"
    elif intent == 'help':
        return generate_help_response()
    elif intent == 'team_overview':
        return generate_team_overview(tasks)
    elif intent == 'team_workload':
        return generate_team_workload(tasks)
    elif intent == 'task_counts':
        return generate_task_counts(tasks)
    elif intent == 'person_detail':
        return generate_person_detail(tasks, analysis['person'])
    elif intent == 'person_task_count':
        return generate_person_task_count(tasks, analysis['person'])
    elif intent == 'department':
        return generate_department_overview(tasks, analysis['department'])
    elif intent == 'blockers':
        return generate_blockers_report(tasks)
    elif intent == 'priorities':
        return generate_priorities_report(tasks)
    elif intent == 'in_progress':
        return generate_in_progress_report(tasks)
    elif intent == 'todo':
        return generate_todo_report(tasks)
    elif intent == 'done':
        return generate_done_report(tasks)
    elif intent == 'company_overview':
        return generate_company_overview(tasks)
    # NEW INTENTS ADDED HERE
    elif intent == "on_time_completion":
        return generate_on_time_completion(tasks)
    elif intent == "past_due_tasks":
        return generate_past_due_tasks(tasks)
    elif intent == "department_this_week":
        return generate_department_this_week(tasks, analysis.get('department', 'All'))
    elif intent == "person_this_week":
        return generate_person_this_week(tasks, analysis['person'])
    elif intent == "all_this_week":
        return generate_all_this_week(tasks)
    else:
        return generate_team_overview(tasks)

# NEW RESPONSE GENERATOR FUNCTIONS
def generate_on_time_completion(tasks: List[Dict]) -> str:
    """Generate on-time completion report"""
    completed_tasks = [t for t in tasks if t['is_completed'] and t['actual_due_date']]
    on_time_tasks = []
    late_tasks = []
    
    for task in completed_tasks:
        if task['actual_due_date']:
            completion_date = datetime.now().date()
            if completion_date <= task['actual_due_date']:
                on_time_tasks.append(task)
            else:
                late_tasks.append(task)
    
    total_completed_with_dates = len(completed_tasks)
    on_time_rate = (len(on_time_tasks) / total_completed_with_dates * 100) if total_completed_with_dates > 0 else 0
    
    response = "⏰ **On-Time Completion Report**\n\n"
    response += f"• **On-time completion rate:** {on_time_rate:.1f}%\n"
    response += f"• **Tasks completed on time:** {len(on_time_tasks)}\n"
    response += f"• **Tasks completed late:** {len(late_tasks)}\n"
    response += f"• **Total completed tasks with deadlines:** {total_completed_with_dates}\n"
    
    if late_tasks:
        response += "\n**Recently late completions:**\n"
        for task in late_tasks[:3]:  # Show top 3
            response += f"• {task['name']} ({task['owners'][0] if task['owners'] else 'Unassigned'})\n"
    
    return response

def generate_past_due_tasks(tasks: List[Dict]) -> str:
    """Generate past due tasks report"""
    past_due = [t for t in tasks if t['is_past_due'] and not t['is_completed']]
    
    if not past_due:
        return "✅ **No overdue tasks!** Everything is up to date."
    
    response = "🔴 **Overdue Tasks**\n\n"
    
    # Group by department
    dept_groups = {}
    for task in past_due:
        dept = task['department']
        if dept not in dept_groups:
            dept_groups[dept] = []
        dept_groups[dept].append(task)
    
    for dept, dept_tasks in dept_groups.items():
        response += f"**{dept}** ({len(dept_tasks)} overdue):\n"
        for task in dept_tasks[:5]:  # Limit to 5 per department
            owners = ", ".join(task['owners']) if task['owners'] else 'Unassigned'
            response += f"• {task['name']} ({owners})\n"
        response += "\n"
    
    response += f"**Total overdue:** {len(past_due)} tasks"
    return response

def generate_department_this_week(tasks: List[Dict], department: str) -> str:
    """Generate department's weekly tasks"""
    if department == "All":
        dept_tasks = [t for t in tasks if t['is_due_this_week']]
        dept_name = "All Departments"
    else:
        dept_tasks = [t for t in tasks if t['department'] == department and t['is_due_this_week']]
        dept_name = f"{department} Department"
    
    if not dept_tasks:
        return f"📅 **{dept_name}:** No tasks due this week."
    
    # Separate completed and pending
    completed = [t for t in dept_tasks if t['is_completed']]
    pending = [t for t in dept_tasks if not t['is_completed']]
    
    response = f"📅 **{dept_name} - This Week**\n\n"
    response += f"• **Total due this week:** {len(dept_tasks)}\n"
    response += f"• **Completed:** {len(completed)}\n"
    response += f"• **Pending:** {len(pending)}\n\n"
    
    if pending:
        response += "**Pending tasks:**\n"
        for task in pending[:8]:  # Limit display
            owners = ", ".join(task['owners']) if task['owners'] else 'Unassigned'
            status_icon = "🔴" if task['is_past_due'] else "🟡"
            response += f"• {status_icon} {task['name']} ({owners})\n"
    
    return response

def generate_person_this_week(tasks: List[Dict], person: str) -> str:
    """Generate person's weekly tasks"""
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners']) and t['is_due_this_week']]
    
    if not person_tasks:
        return f"📅 **{person}:** No tasks due this week."
    
    completed = [t for t in person_tasks if t['is_completed']]
    pending = [t for t in person_tasks if not t['is_completed']]
    
    response = f"📅 **{person}'s Week**\n\n"
    response += f"• **Total due this week:** {len(person_tasks)}\n"
    response += f"• **Completed:** {len(completed)}\n"
    response += f"• **Pending:** {len(pending)}\n\n"
    
    if pending:
        response += "**Pending tasks:**\n"
        for task in pending:
            status_icon = "🔴" if task['is_past_due'] else "🟡"
            response += f"• {status_icon} {task['name']} - {task['department']}\n"
    
    if completed:
        response += "\n**Completed tasks:**\n"
        for task in completed[:3]:  # Show only recent completions
            response += f"• ✅ {task['name']}\n"
    
    return response

def generate_all_this_week(tasks: List[Dict]) -> str:
    """Generate all tasks due this week"""
    week_tasks = [t for t in tasks if t['is_due_this_week']]
    
    if not week_tasks:
        return "📅 **This Week:** No tasks due this week."
    
    completed = len([t for t in week_tasks if t['is_completed']])
    pending = len([t for t in week_tasks if not t['is_completed']])
    overdue = len([t for t in week_tasks if t['is_past_due'] and not t['is_completed']])
    
    response = "📅 **Company Overview - This Week**\n\n"
    response += f"• **Total due this week:** {len(week_tasks)}\n"
    response += f"• **Completed:** {completed}\n"
    response += f"• **Pending:** {pending}\n"
    response += f"• **Overdue:** {overdue}\n\n"
    
    # Show departments breakdown
    dept_summary = {}
    for task in week_tasks:
        dept = task['department']
        if dept not in dept_summary:
            dept_summary[dept] = {'total': 0, 'completed': 0}
        dept_summary[dept]['total'] += 1
        if task['is_completed']:
            dept_summary[dept]['completed'] += 1
    
    response += "**By Department:**\n"
    for dept, stats in dept_summary.items():
        completion_pct = (stats['completed'] / stats['total'] * 100) if stats['total'] > 0 else 0
        response += f"• {dept}: {stats['completed']}/{stats['total']} ({completion_pct:.0f}%)\n"
    
    return response

# YOUR ORIGINAL RESPONSE GENERATOR FUNCTIONS (KEPT INTACT)
def generate_greeting_response(tasks: List[Dict]) -> str:
    """Friendly greeting response"""
    total_tasks = len(tasks)
    in_progress = len([t for t in tasks if t['status'] == 'In progress'])
    
    return f"""👋 Hello! I'm your Task Intel Bot. 

Here's a quick overview:
• {total_tasks} total tasks across the company
• {in_progress} items currently in progress

You can ask me things like:
• "How's the team doing?"
• "What is Omar working on?"
• "Any blockers right now?"
• "High priority items"

What would you like to know?"""

def generate_help_response() -> str:
    """Comprehensive help response with new features"""
    return """🤖 **Task Intel Bot - Available Commands**

**NEW - Timing & Deadlines:**
• "Are we finishing tasks on time?"
• "What tasks are overdue?" or "Late tasks"
• "What's due this week?" 
• "What should Tech finish this week?"
• "What is Omar meant to finish this week?"

**Team & People:**
• "Team overview" or "How's everyone doing?"
• "How many tasks does Omar have?"
• "What is Derrick working on?"
• "Team workload" or "Who's busy?"

**Status & Priorities:**
• "What's blocked?" or "Any issues?"
• "High priority items" or "What's urgent?"
• "What's in progress?" or "Active work"
• "Upcoming tasks" or "To do items"

**Departments:**
• "Tech team status" or "Engineering update"
• "Commercial department" or "Sales tasks"
• "Operations" or "Finance"

**General:**
• "Company overview" or "Brief status"
• "Hi" or "Hello" for a friendly greeting

**Examples:**
• `/intel hi`
• `/intel how many tasks does everyone have?`
• `/intel what's blocked right now?`
• `/intel tech team update`"""

def generate_team_overview(tasks: List[Dict]) -> str:
    """Team task overview"""
    task_counts = {}
    for person in TEAM_MEMBERS:
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        task_counts[person] = len(person_tasks)
    
    active_members = [(p, c) for p, c in task_counts.items() if c > 0]
    active_members.sort(key=lambda x: x[1], reverse=True)
    
    response = "👥 **Team Task Overview**\n\n"
    for person, count in active_members:
        response += f"• {person}: {count} tasks\n"
    
    response += f"\n📊 **Total:** {len(tasks)} tasks across {len(active_members)} team members"
    return response

def generate_team_workload(tasks: List[Dict]) -> str:
    """Team workload analysis"""
    task_counts = {}
    for person in TEAM_MEMBERS:
        person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
        task_counts[person] = len(person_tasks)
    
    avg_tasks = sum(task_counts.values()) / len([v for v in task_counts.values() if v > 0]) if any(task_counts.values()) else 0
    
    response = "👥 **Team Workload Analysis**\n\n"
    
    for person, count in sorted(task_counts.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            if count > avg_tasks * 1.5:
                status = "🟡 Moderate load"
            elif count > avg_tasks * 2:
                status = "🔴 High load"
            else:
                status = "🟢 Balanced"
            response += f"• {person}: {count} tasks - {status}\n"
    
    response += f"\n📈 **Average:** {avg_tasks:.1f} tasks per person"
    return response

def generate_person_detail(tasks: List[Dict], person: str) -> str:
    """Person detail view"""
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    
    if not person_tasks:
        return f"👤 **{person}** has no tasks assigned currently."
    
    in_progress = [t for t in person_tasks if t['status'] == 'In progress']
    high_priority = [t for t in person_tasks if t['priority'] == 'High']
    blocked = [t for t in person_tasks if t['blocker'] not in ['None', 'Not set']]
    
    response = f"👤 **{person}'s Current Work** ({len(person_tasks)} tasks)\n\n"
    
    if in_progress:
        response += f"🔄 **In Progress ({len(in_progress)}):**\n"
        for task in in_progress[:3]:
            response += f"• {task['name']}"
            if task['due_date'] != 'No date':
                response += f" (due {task['due_date']})"
            if task['blocker'] not in ['None', 'Not set']:
                response += f" 🚧 {task['blocker']} blocker"
            response += "\n"
        response += "\n"
    
    if high_priority:
        response += f"🎯 **High Priority ({len(high_priority)}):**\n"
        for task in high_priority[:2]:
            response += f"• {task['name']}\n"
        response += "\n"
    
    response += f"📊 **Summary:** {len(person_tasks)} total • {len(blocked)} blocked • {len(high_priority)} high priority"
    return response

def generate_person_task_count(tasks: List[Dict], person: str) -> str:
    """Person task count only"""
    person_tasks = [t for t in tasks if any(person.lower() in owner.lower() for owner in t['owners'])]
    count = len(person_tasks)
    
    if count == 0:
        return f"👤 **{person}** has no tasks assigned."
    
    status_counts = {}
    for task in person_tasks:
        status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
    
    response = f"👤 **{person}** has {count} task{'s' if count != 1 else ''}:\n"
    for status, status_count in status_counts.items():
        response += f"• {status}: {status_count}\n"
    
    high_priority = len([t for t in person_tasks if t['priority'] == 'High'])
    if high_priority > 0:
        response += f"• High priority: {high_priority}\n"
    
    return response

def generate_task_counts(tasks: List[Dict]) -> str:
    """Task counts overview"""
    total = len(tasks)
    in_progress = len([t for t in tasks if t['status'] == 'In progress'])
    high_priority = len([t for t in tasks if t['priority'] == 'High'])
    blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
    
    return f"""📊 **Task Counts Overview**

• **Total tasks:** {total}
• **In progress:** {in_progress}
• **High priority:** {high_priority}
• **Blocked items:** {blocked}"""

def generate_department_overview(tasks: List[Dict], department: str) -> str:
    dept_tasks = [t for t in tasks if t['department'] == department]
    in_progress = len([t for t in dept_tasks if t['status'] == 'In progress'])
    return f"🏢 **{department} Department:** {len(dept_tasks)} tasks ({in_progress} in progress)"

def generate_blockers_report(tasks: List[Dict]) -> str:
    blocked = [t for t in tasks if t['blocker'] not in ['None', 'Not set']]
    return f"🚧 **Blocked Items:** {len(blocked)} tasks need attention"

def generate_priorities_report(tasks: List[Dict]) -> str:
    high_priority = [t for t in tasks if t['priority'] == 'High']
    return f"🎯 **High Priority:** {len(high_priority)} critical tasks"

def generate_in_progress_report(tasks: List[Dict]) -> str:
    in_progress = [t for t in tasks if t['status'] == 'In progress']
    return f"🔄 **In Progress:** {len(in_progress)} tasks being worked on"

def generate_todo_report(tasks: List[Dict]) -> str:
    todo = [t for t in tasks if t['status'] == 'To Do']
    return f"📋 **To Do:** {len(todo)} upcoming tasks"

def generate_done_report(tasks: List[Dict]) -> str:
    done = [t for t in tasks if t['status'] == 'Done']
    return f"✅ **Completed:** {len(done)} tasks finished"

def generate_company_overview(tasks: List[Dict]) -> str:
    total = len(tasks)
    in_progress = len([t for t in tasks if t['status'] == 'In progress'])
    blocked = len([t for t in tasks if t['blocker'] not in ['None', 'Not set']])
    high_priority = len([t for t in tasks if t['priority'] == 'High'])
    
    return f"""🏢 **Company Overview**

• **Total tasks:** {total}
• **In progress:** {in_progress} 
• **High priority:** {high_priority}
• **Blocked:** {blocked}
• **Active team members:** {len(TEAM_MEMBERS)}"""

@app.post("/slack/command")
async def slack_command(request: Request, background_tasks: BackgroundTasks):
    """Handle Slack commands"""
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        response_url = form_data.get("response_url")
        
        immediate_response = {
            "response_type": "ephemeral",
            "text": "💭 Gathering your task info..."
        }
        
        if response_url:
            background_tasks.add_task(process_query_enhanced, query, response_url)
        
        return JSONResponse(content=immediate_response)
        
    except Exception as e:
        logger.error(f"Slack command error: {e}")
        return JSONResponse(content={
            "response_type": "ephemeral", 
            "text": "❌ Error processing command"
        })

async def process_query_enhanced(query: str, response_url: str):
    """Process query in background"""
    try:
        analysis = await understand_query_enhanced(query)
        tasks = await get_all_tasks_fast()
        
        if not tasks:
            response = "📭 No tasks found in the system."
        else:
            response = generate_enhanced_response(tasks, analysis)
        
        payload = {"response_type": "in_channel", "text": response}
        await send_slack_response(response_url, payload)
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        error_msg = "❌ Sorry, I'm having trouble. Please try again."
