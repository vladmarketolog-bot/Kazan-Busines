import os
import time
import json
import logging
import requests
from bs4 import BeautifulSoup
import telebot
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# --- Configuration ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
CHANNEL_ID = os.getenv('CHANNEL_ID')
DB_FILE = 'processed_events.json'

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper: Fix Channel ID ---
def get_clean_channel_id(channel_id):
    if not channel_id:
        return None
    channel_id = str(channel_id).strip()
    # Check if it's a numeric ID that might be missing the -100 prefix for supergroups
    if channel_id.isdigit() and channel_id.startswith('100') and len(channel_id) > 10:
        return int(f"-{channel_id}")
    try:
        return int(channel_id)
    except ValueError:
        return channel_id # Return as string (e.g. @channelname)

CHANNEL_ID = get_clean_channel_id(CHANNEL_ID)

# --- Gemini Setup ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    logging.error("GEMINI_API_KEY not found in environment variables.")

# --- Telegram Setup ---
if TELEGRAM_TOKEN:
    bot = telebot.TeleBot(TELEGRAM_TOKEN)
else:
    logging.error("TELEGRAM_TOKEN not found in environment variables.")

def load_processed_events():
    """Loads list of processed event URLs from JSON file."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except json.JSONDecodeError:
            return set()
    return set()

def save_processed_events(processed_events):
    """Saves list of processed event URLs to JSON file."""
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(processed_events), f, ensure_ascii=False, indent=4)

def parse_timepad_events():
    """Scrapes business events from Timepad in Kazan."""
    url = "https://timepad.ru/events/kazan/business/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch Timepad: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    events = []
    
    # Try multiple selectors for robustness
    event_cards = soup.select('.t-card') 
    if not event_cards:
         event_cards = soup.select('.t-search-event-card') # Alternate class

    if not event_cards:
        # Fallback: scan for links
        links = soup.select('a[href^="https://"][href*="timepad.ru/event/"]')
        seen_links = set()
        for link in links:
             href = link.get('href')
             if href not in seen_links:
                 title = link.get_text(strip=True)
                 if title and len(title) > 5:
                     events.append({
                         'url': href,
                         'title': title,
                         'description': '', 
                         'date_str': 'См. по ссылке' 
                     })
                     seen_links.add(href)
    else:
        for card in event_cards[:10]:
            try:
                link_tag = card.select_one('a.t-card__link') or card.select_one('a')
                if not link_tag: continue
                
                url = link_tag.get('href')
                if not url.startswith('http'):
                    url = 'https://timepad.ru' + url
                
                header_tag = card.select_one('.t-card__header') or card.select_one('h3')
                title = header_tag.get_text(strip=True) if header_tag else "Без названия"
                
                desc_tag = card.select_one('.t-card__description') or card.select_one('p')
                desc = desc_tag.get_text(strip=True) if desc_tag else ""
                
                date_tag = card.select_one('.t-card__date')
                date_str = date_tag.get_text(strip=True) if date_tag else ""

                events.append({
                    'url': url,
                    'title': title,
                    'description': desc,
                    'date_str': date_str
                })
            except Exception as e:
                logging.error(f"Error parsing card: {e}")

    return events

def generate_post_content(event):
    """Generates Telegram post content using Gemini."""
    if not GEMINI_API_KEY:
        return None

    prompt = f"""
    Ты — опытный SMM-менеджер бизнес-сообщества. Твоя задача — превратить сырой анонс в пост для Telegram.
    
    Входящие данные:
    Название: {event['title']}
    Описание: {event['description']}
    Дата/Время: {event['date_str']}
    Ссылка: {event['url']}

    Инструкция:
    1. Если мероприятие явно НЕ относится к бизнесу, нетворкингу, саморазвитию или карьере в Казани (например, концерты, детские праздники), ответь строго одним словом: 'IGNORE'.
    2. Если подходит, создай пост в формате:
       ЗАГОЛОВОК (Короткий, цепляющий, КАПСОМ)
       
       🗓 Дата и время: [Дата из анонса или "Уточняйте по ссылке"]
       📍 Место: [Если есть в описании, иначе "См. по ссылке"]
       
       [3-4 ключевых тезиса с эмодзи ⚫, почему стоит пойти]
       
       🔗 Регистрация: {event['url']}
       
       #бизнесКазань
    """
    
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        # Clean up possible markdown code blocks if AI adds them
        if text.startswith('```') and text.endswith('```'):
            text = text[3:-3]
            if text.startswith('markdown'): # remove language identifier
                 text = text[8:]
        return text.strip()
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return None

def main():
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        logging.error("Telegram credentials missing or incomplete. Set TELEGRAM_TOKEN and CHANNEL_ID.")
        return 

    processed_events = load_processed_events()
    logging.info(f"Loaded {len(processed_events)} processed events.")
    
    events = parse_timepad_events()
    logging.info(f"Found {len(events)} events on Timepad (approx).")
    
    new_events_count = 0
    
    for event in events:
        if event['url'] in processed_events:
            continue
            
        logging.info(f"Processing candidate: {event['title']}")
        
        post_content = generate_post_content(event)
        
        if not post_content:
            logging.warning("Gemini returned empty content.")
            continue
            
        if post_content == 'IGNORE':
            logging.info(f"Event ignored by AI filtering: {event['title']}")
            processed_events.add(event['url']) 
            continue
            
        # Send to Telegram
        try:
            # Check length, split if needed (basic check)
            if len(post_content) > 4096:
                post_content = post_content[:4093] + "..."
            
            # Using Markdown parse mode requires escaping, or use None/HTML.
            # Gemini output might contain markdown-like syntax. safest is no parse_mode or careful escaping.
            # Trying without parse_mode first to ensure delivery, or verify markdown.
            # Let's use None to be safe from markdown errors, or 'Markdown' if we trust Gemini.
            # Better: strip markdown if it fails?
            # Let's try sending as plain text to ensure it works, the emoji will still work.
            bot.send_message(CHANNEL_ID, post_content)
            
            logging.info(f"✅ Posted: {event['title']}")
            processed_events.add(event['url'])
            new_events_count += 1
            
            # Sleep to respect rate limits
            time.sleep(3) 
            
        except Exception as e:
            logging.error(f"Telegram send error: {e}")
            # If error is about chat not found, maybe ID is wrong.
            if "chat not found" in str(e).lower():
                logging.error("Check CHANNEL_ID. Ensure the bot is an Admin in the channel.")
    
    # Save updated list
    save_processed_events(processed_events)
    logging.info(f"Run complete. {new_events_count} new posts sent.")

if __name__ == "__main__":
    main()
