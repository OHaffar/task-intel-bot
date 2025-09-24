from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import logging
from datetime import datetime
from notion_client import Client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Task Intel Bot")

# Initialize Notion
notion = Client(auth=os.getenv('NOTION_TOKEN')) if os.getenv('NOTION_TOKEN') else None

@app.get("/")
async def home():
    return {"status": "ready"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

def get_all_tasks():
    if not notion:
        return []
    
    tasks = []
    databases = {
        'Operations': os.getenv('NOTION_DB_OPS'),
        'Commercial': os.getenv('NOTION_DB_COMM'),
        'Tech': os.getenv('NOTION_DB_TECH'),
        'Finance': os.getenv('NOTION_DB_FIN')
    }
    
    for dept, db_id in databases.items():
        if not db_id:
            continue
            
        try:
            result = notion.databases.query(database_id=db_id)
            
            for page in result.get('results', []):
                props = page.get('properties', {})
                
                # FIX 1: Proper owner name resolution
                owners = []
                people_data = props.get('Owner', {}).get('people', [])
                for person in people_data:
                    # Try to get name directly, fallback to ID resolution
                    name = person.get('name')
                    if not name and person.get('id'):
                        # If only ID exists, try to get user info
                        try:
                            user_info = notion.users.retrieve(user_id=person['id'])
                            name = user_info.get('name', f"User_{person['id'][:8]}")
                        except:
                            name = f"User_{person['id'][:8]}"
                    if name:
                        owners.append(name)
                
                # FIX 2: Correct property names (capital S)
                task = {
                    'name': get_property(props, 'Task Name', 'title'),
                    'owners': owners,  # Now properly resolved
                    'status': get_property(props, 'Status', 'select'),
                    'due_date': get_property(props, 'Due Date', 'date'),
                    'next_step': get_property(props, 'Next Steps', 'rich_text'),  # FIXED: Capital S
                    'blocker': get_property(props, 'Blocker', 'select'),
                    'impact': get_property(props, 'Impact', 'rich_text'),
                    'priority': get_property(props, 'Priority', 'select'),
                    'dept': dept,
                    'raw_due_date': props.get('Due Date', {}).get('date', {}).get('start')  # For sorting
                }
                tasks.append(task)
                
        except Exception as e:
            logger.error(f"Error in {dept}: {e}")
            continue
    
    return tasks

def get_property(props, field_name, field_type):
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

def find_person_tasks(tasks, person_name):
    # FIX 3: Flexible name matching (not hardcoded)
    person_lower = person_name.lower()
    return [t for t in tasks if any(person_lower in owner.lower() for owner in t['owners'])]

def sort_tasks_by_date(tasks):
    # FIX 4: Proper date sorting
    def get_sort_key(task):
        raw_date = task.get('raw_due_date')
        if not raw_date or raw_date == 'No date':
            return datetime.max  # Put no-date tasks at the end
        try:
            return datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
        except:
            return datetime.max
    return sorted(tasks, key=get_sort_key)

@app.post("/slack/command")
async def slack_command(request: Request):
    try:
        form_data = await request.form()
        query = form_data.get("text", "").strip()
        
        # If empty query, show help
        if not query:
            return show_help()
        
        query_lower = query.lower()
        tasks = get_all_tasks()
        
        if not tasks:
            return JSONResponse(content={
                "response_type": "in_channel",
                "text": "📊 No tasks found in Notion databases"
            })
        
        # FIX 3: Natural language detection (not hardcoded list)
        if any(word in query_lower for word in ['what', 'how', 'who', 'where', 'when', 'show', 'tell']):
            # Extract potential person name from query
            words = query_lower.split()
            potential_names = [word for word in words if len(word) > 2 and word not in 
                             ['what', 'how', 'who', 'where', 'when', 'show', 'tell', 'about', 'working', 'tasks']]
            
            for name in potential_names:
                person_tasks = find_person_tasks(tasks, name)
                if person_tasks:
                    # FIX 4: Proper date sorting
                    sorted_tasks = sort_tasks_by_date(person_tasks)
                    return show_person_tasks(sorted_tasks, name.title())
            
            # If no person found, show brief
            return show_brief(tasks)
        
        # FIX 2: Only trigger brief for specific keywords (no empty string)
        elif any(word in query_lower for word in ['brief', 'status', 'company', 'overview', 'summary']):
            return show_brief(tasks)
        
        else:
            return show_help(tasks)
        
    except Exception as e:
        logger.error(f"Command error: {e}")
        return JSONResponse(content={
            "text": "⚡ Task Intel Bot - Try `/intel what is brazil working on?` or `/intel brief`"
        })

def show_person_tasks(tasks, person_name):
    response = f"👤 **{person_name}'s Tasks**\n\n"
    
    for i, task in enumerate(tasks[:6], 1):
        response += f"**{i}. {task['name']}**\n"
        response += f"   Status: {task['status']} | Due: {task['due_date']}\n"
        response += f"   Next: {task['next_step']}\n"
        response += f"   Blocker: {task['blocker']} | Priority: {task['priority']}\n\n"
    
    dept_counts = {}
    for task in tasks:
        dept_counts[task['dept']] = dept_counts.get(task['dept'], 0) + 1
    
    dept_summary = " • ".join([f"{k}: {v}" for k, v in dept_counts.items()])
    response += f"📊 **Summary:** {len(tasks)} tasks ({dept_summary})"
    
    return JSONResponse(content={"response_type": "in_channel", "text": response})

def show_brief(tasks):
    dept_counts = {}
    status_counts = {}
    
    for task in tasks:
        dept_counts[task['dept']] = dept_counts.get(task['dept'], 0) + 1
        status_counts[task['status']] = status_counts.get(task['status'], 0) + 1
    
    response = "🏢 **Company Brief**\n\n"
    response += "📈 **By Department:**\n"
    for dept, count in sorted(dept_counts.items()):
        response += f"• {dept}: {count} tasks\n"
    
    response += "\n🔄 **By Status:**\n"
    for status, count in sorted(status_counts.items()):
        response += f"• {status}: {count} tasks\n"
    
    # Show people with tasks
    people_with_tasks = set()
    for task in tasks:
        people_with_tasks.update(task['owners'])
    
    if people_with_tasks:
        response += f"\n👥 **Team Members with Tasks:** {', '.join(sorted(people_with_tasks))}"
    
    response += f"\n\n📊 **Total:** {len(tasks)} tasks"
    
    return JSONResponse(content={"response_type": "in_channel", "text": response})

def show_help(tasks=None):
    task_count = len(tasks) if tasks else 0
    response = (
        "🤖 **Task Intel Bot**\n\n"
        "**Natural Language Queries:**\n"
        "• `what is brazil working on?`\n"
        "• `show me omar's tasks`\n" 
        "• `what are the top priorities?`\n"
        "• `company status`\n\n"
        "**Quick Commands:**\n"
        "• `/intel brief` - Company overview\n"
        "• `/intel help` - This message\n\n"
        f"📈 Live data: {task_count} tasks tracked"
    )
    return JSONResponse(content={"response_type": "in_channel", "text": response})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
