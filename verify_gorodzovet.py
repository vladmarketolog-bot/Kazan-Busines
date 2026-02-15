import logging
import sys
import os

# Ensure we can import main
sys.path.append(os.getcwd())

from main import create_driver, scrape_gorodzovet

# Configure logging to print to console
logging.basicConfig(level=logging.INFO, format='%(message)s')

def verify():
    print("Starting verification of GorodZovet scraper...")
    driver = create_driver()
    try:
        events = scrape_gorodzovet(driver)
        print(f"\nFound {len(events)} events.")
        
        print("\n--- Events Found ---")
        for event in events:
            print(f"Title: {event['title']}")
            print(f"URL:   {event['url']}")
            if '-event' not in event['url']:
                print("⚠️  WARNING: This looks like a SECTION link, not an event!")
            else:
                print("✅  Valid Event Link")
            print("-" * 20)
            
    except Exception as e:
        logging.error(f"Error: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    verify()
