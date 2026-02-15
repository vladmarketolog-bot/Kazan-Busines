import os
import time
import json
import logging
import requests
import difflib
from bs4 import BeautifulSoup
import telebot
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv

# --- Selenium Setup ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Load environment variables
load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')
DB_FILE = 'processed_events.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Logic ---
def get_clean_channel_id(channel_id):
    if not channel_id: return None
    channel_id = str(channel_id).strip()
    if channel_id.isdigit() and channel_id.startswith('100') and len(channel_id) > 10:
        return int(f"-{channel_id}")
    try:
        return int(channel_id)
    except ValueError:
        return channel_id

CHANNEL_ID = get_clean_channel_id(CHANNEL_ID)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    pass

if TELEGRAM_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_TOKEN)

def load_processed_events():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_processed_events(processed_events):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(processed_events), f, ensure_ascii=False, indent=4)

def save_event_to_db(event_data):
    """Saves structured event data to events_db.json"""
    db_file = 'events_db.json'
    events = []
    if os.path.exists(db_file):
        try:
            with open(db_file, 'r', encoding='utf-8') as f:
                events = json.load(f)
        except:
            events = []
    
    # Check if event already exists (by URL)
    for e in events:
        if e['url'] == event_data['url']:
            return # Already exists
            
    events.append(event_data)
    with open(db_file, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=4)

def is_similar(title1, title2, threshold=0.85):
    """Checks if two titles are similar using SequenceMatcher."""
    return difflib.SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > threshold

# --- Selenium Driver Factory ---
def create_driver():
    options = ChromeOptions()
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222") 
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    logging.info("Starting Selenium driver...")
    return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)

# --- Parsers ---

def scrape_timepad(driver):
    url = "https://afisha.timepad.ru/kazan/categories/biznes"
    logging.info(f"Scraping Timepad: {url}")
    events = []
    
    try:
        driver.get(url)
        time.sleep(5)
        
        # Generic wait
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except:
            pass
            
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Find all event links
        links = soup.find_all('a', href=True)
        
        # DEBUG: Log first 10 links to understand structure
        debug_links = [l.get('href') for l in links[:15]]
        logging.info(f"DEBUG: Sample links found: {debug_links}")
        
        seen_links = set()
        
        for link in links:
            href = link.get('href')
            full_url = href
            
            # Flexible matching for Afisha and classic Timepad
            # Matches /event/, /events/, /kazan/events/ etc.
            if '/event' in href:
                if href.startswith('/'):
                    if 'afisha.timepad.ru' in url: # If we are on afisha, relative links are likely afisha
                        full_url = 'https://afisha.timepad.ru' + href
                    else:
                        full_url = 'https://timepad.ru' + href
                elif href.startswith('http'):
                    full_url = href
            else:
                 continue
                
            if full_url in seen_links: continue

            title = link.get_text(strip=True)
            if not title: title = link.get('title') or link.get('aria-label')
            
            if title and len(title) > 5 and "регистрация" not in title.lower():
                events.append({
                    'url': full_url,
                    'title': title,
                    'source': 'timepad',
                    'date_str': 'См. по ссылке'
                })
                seen_links.add(full_url)
                
    except Exception as e:
        logging.error(f"Timepad scraper error: {e}")
        
    logging.info(f"Found {len(events)} events on Timepad.")
    return events

def scrape_gorodzovet(driver):
    url = "https://gorodzovet.ru/kazan/biz/"
    logging.info(f"Scraping GorodZovet: {url}")
    events = []
    
    try:
        driver.get(url)
        time.sleep(5)
        
        try:
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "a")))
        except:
            pass

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # GorodZovet structure often uses blocks with links
        # Looking for event titles inside H3 or generic links in content area
        # Heuristic: links inside typical list structures
        
        # Try to find elements that look like event blocks
        # Usually they have a date and a title.
        
        # Generic approach: Find all links, filtering for internal event paths
        links = soup.find_all('a', href=True)
        seen_links = set()

        for link in links:
            href = link.get('href')
            # Gorodzovet event links usually look like /kazan/eventname/ or /cid/
            # But we must avoid categories/tags.
            # Best filter: links that have a date block nearby or specific classes. 
            # Let's try broad: all links that are not known tech links
            
            if not href.startswith('/'): continue # Relative links mostly
            if len(href) < 5: continue
            if any(x in href for x in ['/cat/', '/day/', '/add/', '/user/', '/login/']): continue
            if '-event' not in href: continue
            
            full_url = 'https://gorodzovet.ru' + href

            if full_url in seen_links: continue
            
            title = link.get_text(strip=True)
            if not title: title = link.get('title')
            
            # Additional check: title length and maybe parent context
            if title and len(title) > 10:
                events.append({
                    'url': full_url,
                    'title': title,
                    'source': 'gorodzovet',
                    'date_str': 'См. по ссылке'
                })
                seen_links.add(full_url)

    except Exception as e:
        logging.error(f"GorodZovet scraper error: {e}")
        
    logging.info(f"Found {len(events)} events on GorodZovet.")
    return events

# --- AI & Main ---

def generate_post_content(event):
    if not GEMINI_API_KEY: return None

    # Limit text to ~5000 chars to fit in context window and avoid noise
    full_text_snippet = event.get('full_text', '')[:5000]

    prompt = f"""
    Ты — опытный SMM-менеджер бизнес-сообщества.
    
    Входящие данные:
    Источник: {event.get('source')}
    Название: {event['title']}
    Ссылка: {event['url']}
    Текст со страницы мероприятия: 
    {full_text_snippet}

    Инструкция:
    1. Проанализируй текст. Если мероприятие явно НЕ относится к бизнесу, нетворкингу, IT, маркетингу, саморазвитию или карьере в Казани (или онлайн), верни JSON с полем "action": "IGNORE".
    2. Если подходит, создай пост и верни JSON со следующими полями:
       - "action": "POST"
       - "post_text": "Текст поста... (ЗАГОЛОВОК, Дата и время, Место, Тезисы, Ссылка, #бизнесКазань)"
       - "event_date": "YYYY-MM-DD" (Найди дату начала. Если не нашел или это 'каждый день', верни null. Если диапазон, верни дату начала)
       - "is_online": true/false
       
    ОБЯЗАТЕЛЬНО верни валидный JSON. Не используй Markdown formatting (```json) вокруг ответа.
    """
    
    # List of models to try in order of preference (updated based on user logs)
    # Using newer 2.0 models which are available for this API key
    models_to_try = ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-flash-latest']
    
    for model_name in models_to_try:
        try:
            model = genai.GenerativeModel(model_name, generation_config={"response_mime_type": "application/json"})
            response = model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith('```'): text = text.strip('`').replace('json','').strip()
            return json.loads(text)
        except Exception as e:
            logging.warning(f"Model {model_name} failed: {e}")
            continue
            
    logging.error("All AI models failed.")
    # Debug: List available models to see what we CAN use
    try:
        logging.info("Listing available models:")
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                logging.info(f"- {m.name}")
    except Exception as e:
        logging.error(f"Could not list models: {e}")
        
    return None

def main():
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        logging.error("Telegram credentials missing.")
        return

    processed_events = load_processed_events()
    logging.info(f"Loaded {len(processed_events)} processed events.")
    
    driver = None
    try:
        driver = create_driver()
        
        # 1. Scrape Timepad (Priority)
        tp_events = scrape_timepad(driver)
        
        # 2. Scrape GorodZovet
        gz_events = scrape_gorodzovet(driver)
        
        # Combine and Deduplicate
        final_events = []
        
        # Add all new Timepad events first
        for e in tp_events:
            if e['url'] not in processed_events:
                final_events.append(e)
                
        # Add GorodZovet events ONLY if not similar to ANY Timepad event (even if Timepad event was already processed)
        for gz in gz_events:
            if gz['url'] in processed_events: continue
            
            is_duplicate = False
            # Check against ALL current Timepad events, not just the new ones in final_events
            for tp in tp_events:
                if is_similar(gz['title'], tp['title']):
                    logging.info(f"Skipping GorodZovet duplicate (found in Timepad): {gz['title']}")
                    processed_events.add(gz['url']) # Mark as processed so we don't re-check
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                final_events.append(gz)
                
        logging.info(f"After deduplication: {len(final_events)} events to process.")
        
        # Process with AI
        new_posts = 0
        for event in final_events:
            logging.info(f"Enriching & Processing: {event['title']}")
            
            # Enrich with full page body for better AI context (Date, Place, etc.)
            try:
                driver.get(event['url'])
                time.sleep(2) # Be polite
                # Wait for body
                try:
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                except: pass
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                # Clean text: remove scripts, styles
                text = soup.get_text(separator='\n', strip=True)
                event['full_text'] = text
                
            except Exception as e:
                logging.error(f"Failed to fetch details for {event['url']}: {e}")
                event['full_text'] = ""

            ai_response = generate_post_content(event)
            
            if not ai_response: continue
            
            if ai_response.get('action') == 'IGNORE':
                logging.info(f"Ignored: {event['title']}")
                processed_events.add(event['url'])
                continue
                
            content = ai_response.get('post_text')
            if not content:
                logging.error(f"No post text in AI response for {event['title']}")
                continue

            try:
                if len(content) > 4096: content = content[:4093] + "..."
                bot.send_message(CHANNEL_ID, content)
                logging.info(f"✅ Posted: {event['title']}")
                
                # Save to full DB
                event_record = {
                    'url': event['url'],
                    'title': event['title'],
                    'date': ai_response.get('event_date'),
                    'source': event['source'],
                    'created_at': datetime.now().isoformat(),
                    'post_text': content
                }
                save_event_to_db(event_record)
                
                processed_events.add(event['url'])
                new_posts += 1
                time.sleep(3)
            except Exception as e:
                logging.error(f"Telegram error: {e}")

        save_processed_events(processed_events)
        logging.info(f"Done. Sent {new_posts} posts.")

    except Exception as e:
        logging.error(f"Global scraper error: {e}")
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    main()
