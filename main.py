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

# --- Selenium Setup ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def parse_timepad_events():
    """Scrapes business events from Timepad in Kazan using Selenium."""
    # Updated URL from user
    url = "https://afisha.timepad.ru/kazan/categories/biznes"
    
    options = ChromeOptions()
    options.add_argument("--headless=new") 
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222") 
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    events = []
    driver = None
    
    try:
        logging.info(f"Starting Selenium driver for {url}...")
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        driver.get(url)
        time.sleep(5) 
        
        # DEBUG: Snapshot
        driver.save_screenshot("debug.png")
        logging.info(f"Page Title: {driver.title}")
        
        # Wait for content (generic body or react root)
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
        except:
            pass
            
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Debug: Print snippet
        clean_text = soup.get_text(separator=' ', strip=True)[:500]
        logging.info(f"Page Content Snippet: {clean_text}")
        
        # Universal Scraper for Afisha & Classic Timepad
        # 1. Find all links that look like events
        links = soup.find_all('a', href=True)
        seen_links = set()
        
        for link in links:
            href = link.get('href')
            
            # Filter relevant links
            if '/event/' in href and 'timepad.ru' in href:
                full_url = href
            elif href.startswith('/event/'):
                full_url = 'https://afisha.timepad.ru' + href
            elif href.startswith('https://timepad.ru/event/'):
                 full_url = href
            else:
                continue
            
            # Skip if already processed in this run
            if full_url in seen_links:
                continue

            # Try to get title from the link itself or its children
            title = link.get_text(strip=True)
            
            # If link has no text (e.g. image wrapper), try finding a sibling or parent card title
            if not title:
                # Naive attempt: check for 'aria-label' or 'title' attribute
                title = link.get('title') or link.get('aria-label')
                
            # Valid title check
            if title and len(title) > 5 and "регистрация" not in title.lower():
                # Try to find date
                # In Afisha, dates are often in separate divs, hard to map generically without specific classes.
                # We will let AI figure it out from the Description (which we leave empty for now, 
                # or maybe fetch individual pages if needed, but that's slow).
                # New plan: Use "См. по ссылке" for date, AI scrapes details if it can? 
                # No, standard is AI generates post. We put "См. по ссылке" if date missing.
                
                events.append({
                    'url': full_url,
                    'title': title,
                    'description': '', 
                    'date_str': 'См. по ссылке' 
                })
                seen_links.add(full_url)
                
    except Exception as e:
        logging.error(f"Selenium error: {e}")
    finally:
        if driver:
            driver.quit()

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
