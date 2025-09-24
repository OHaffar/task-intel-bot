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

# MANUAL USER ID MAPPING - SOLVES THE PROBLEM!
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
        "user_mapping_configured": len(USER_ID_TO_NAME)
    }

async def understand_ceo_query(query: str) -> Dict:
    """Understand what the CEO/COO is asking in natural language"""
    if not query:
        return {"intent": "company_update", "tone": "confident"}
    
    query_lower = query.lower()
    
    # Check if asking about a specific person
    for person_key, person_name in TEAM_MEMBERS.items():
        if person_key in query_lower:
            return {
                "intent": "person_update", 
                "person": person_name,
                "tone": "supportive"
            }
    
    # Check for specific types of updates
    if any(word in query_lower for word in ['how are we', 'how things', 'company', 'brief', 'overview']):
        return {"intent": "company_update", "tone": "confident"}
    
    if any(word in query_lower for word in ['block', 'stuck', 'issue', 'problem']):
        return {"intent": "blockers_update", "tone": "concerned"}
    
    if any(word in query_lower for word in ['priority', 'important', 'critical']):
        return {"intent": "priorities_update", "tone": "focused"}
    
    if any(word in query_lower for word in ['tech', 'engineering', 'commercial', 'operations', 'finance']):
        dept = next((d for d in ['tech', 'commercial', 'operations', 'finance'] if d in query_lower), 'company')
        return {"intent": "department_update", "department": dept.title(), "tone": "informative"}
    
    # Default to company update
    return {"intent": "company_update", "tone": "confident"}

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
                    task = parse_task_with_fixed_mapping(page, dept)
                    if task:
                        tasks.append(task)
                        
            except Exception as e:
                logger.error(f"Error fetching {dept}: {e}")
    
    cache[cache_key] = tasks
    logger.info(f"Loaded {len(tasks)} tasks with proper name mapping")
    return tasks

def parse_task_with_fixed_mapping(page: Dict, department: str) -> Optional[Dict]:
    """Parse task using our manual user ID mapping"""
    try:
        props = page.get('properties', {})
        
        # Get task name
        name = get_property(props, 'Task Name', 'title')
        if not name or name == 'No name':
            return None
        
        # FIXED: Use manual mapping to convert user IDs to names
        owners = []
        people_data = props.get('Owner', {}).get('people', [])
        for person in people_data:
            user_id = person.get('id')
            if user_id and user_id in USER_ID_TO_NAME:
                # Use our manual mapping
                owners.append(USER_ID_TO_NAME[user_id])
            elif person.get('name'):
                # Fallback to name if provided
                owners.append(person.get('name'))
            elif user_id:
                # Fallback to user ID if no mapping
                owners.append(f"user_{user_id[-6:]}")
        
        due_date_raw = props.get('Due Date', {}).get('date', {}).get('start')
        
        return {
            'name': name,
            'owners': owners,
            'status': get_property(props, 'Status', 'select'),
            'due_date': due_date_raw.split('T')[0] if due_date_raw else 'Not scheduled',
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
        return select.get('
