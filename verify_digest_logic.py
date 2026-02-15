import unittest
from unittest.mock import MagicMock, patch
import json
import os
from datetime import datetime, timedelta
import sys

# Add current dir to path to import digest
sys.path.append(os.getcwd())

import digest

class TestDigest(unittest.TestCase):
    
    def setUp(self):
        self.test_db = 'test_events_db.json'
        digest.DB_FILE = self.test_db
        
    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    @patch('digest.telebot.TeleBot')
    @patch('digest.CHANNEL_ID', '12345')
    @patch('digest.TELEGRAM_TOKEN', 'fake_token')
    def test_digest_generation(self, MockBot):
        # Mock bot
        mock_bot_instance = MockBot.return_value
        
        # Create dummy events
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        
        # Event 1: This week (Monday)
        date1 = start_of_week
        # Event 2: This week (Wednesday)
        date2 = start_of_week + timedelta(days=2)
        # Event 3: Next week (Monday) - Should be excluded
        date3 = start_of_week + timedelta(days=7)
        
        events = [
            {
                "url": "http://example.com/1",
                "title": "Event 1 (This Week)",
                "date": date1.strftime("%Y-%m-%d"),
                "source": "test"
            },
            {
                "url": "http://example.com/2",
                "title": "Event 2 (This Week)",
                "date": date2.strftime("%Y-%m-%d"),
                "source": "test"
            },
            {
                "url": "http://example.com/3",
                "title": "Event 3 (Next Week)",
                "date": date3.strftime("%Y-%m-%d"),
                "source": "test"
            }
        ]
        
        with open(self.test_db, 'w', encoding='utf-8') as f:
            json.dump(events, f)
            
        # Run main
        digest.main()
        
        # Verify send_message was called
        self.assertTrue(mock_bot_instance.send_message.called)
        
        # Verify content
        args, _ = mock_bot_instance.send_message.call_args
        message = args[1]
        
        print("\nGenerated Message:")
        print(message)
        
        self.assertIn("Event 1", message)
        self.assertIn("Event 2", message)
        self.assertNotIn("Event 3", message)
        self.assertIn("#дайджест", message)

if __name__ == '__main__':
    unittest.main()
