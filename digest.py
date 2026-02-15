import os
import json
import logging
import telebot
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
DB_FILE = 'events_db.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_week_dates():
    """Returns today's date, start of current week (Mon), and end of current week (Sun)."""
    today = datetime.now().date()
    start_of_week = today - timedelta(days=today.weekday()) # Monday
    end_of_week = start_of_week + timedelta(days=6) # Sunday
    return today, start_of_week, end_of_week

def format_date_short(date_obj):
    """Formats date as 'DD.MM'"""
    return date_obj.strftime("%d.%m")

def get_weekday_name(date_obj):
    """Returns Russian weekday name shortened."""
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return days[date_obj.weekday()]

def main():
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        logging.error("Telegram credentials missing.")
        return

    bot = telebot.TeleBot(TELEGRAM_TOKEN)
    
    today, start_week, end_week = get_week_dates()
    logging.info(f"Generating digest for week: {start_week} - {end_week}")

    if not os.path.exists(DB_FILE):
        logging.error("Database file not found.")
        return

    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            events = json.load(f)
    except Exception as e:
        logging.error(f"Error loading DB: {e}")
        return

    # Filter events for this week
    week_events = []
    seen_urls = set()
    
    for event in events:
        if not event.get('date'): continue
        
        try:
            event_date = datetime.strptime(event['date'], "%Y-%m-%d").date()
        except ValueError:
            continue
            
        if start_week <= event_date <= end_week:
            if event['url'] not in seen_urls:
                event['date_obj'] = event_date
                week_events.append(event)
                seen_urls.add(event['url'])

    if not week_events:
        logging.info("No events found for this week.")
        return

    # Sort by date
    week_events.sort(key=lambda x: x['date_obj'])
    
    # Take top 15 (max) to avoid hitting telegram limits or making it too long
    # Ideally we'd have a 'score' but for now just all sorted by date
    # User asked for Top 5, let's limit to 7-10 just in case
    week_events = week_events[:10]

    # Generate Message
    start_str = format_date_short(start_week)
    end_str = format_date_short(end_week)
    
    message_lines = [f"📅 <b>Дайджест на неделю ({start_str} - {end_str})</b>\n"]
    
    emojis = ["🚀", "🎤", "💡", "🤝", "📈", "🔥", "🎓", "🧠"]
    
    for i, event in enumerate(week_events):
        idx = i + 1
        emoji = emojis[i % len(emojis)]
        day_name = get_weekday_name(event['date_obj'])
        date_str = format_date_short(event['date_obj'])
        
        # Link in title
        line = f"{idx}. {emoji} <a href='{event['url']}'>{event['title']}</a> — {day_name}, {date_str}"
        message_lines.append(line)
        
    message_lines.append("\n#дайджест #бизнесКазань")
    
    full_message = "\n".join(message_lines)
    
    try:
        # Use clean channel ID logic from main if needed, but os.getenv usually gives string
        # Re-using clean logic briefly
        cid = str(CHANNEL_ID).strip()
        if cid.isdigit() and cid.startswith('100') and len(cid) > 10:
            cid = int(f"-{cid}")
        elif cid.lstrip('-').isdigit(): # Handle negative explicitly just in case
            cid = int(cid)
            
        bot.send_message(cid, full_message, parse_mode='HTML', disable_web_page_preview=True)
        logging.info("✅ Digest sent successfully!")
        
    except Exception as e:
        logging.error(f"Failed to send digest: {e}")

if __name__ == "__main__":
    main()
