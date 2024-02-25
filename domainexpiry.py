import os
import datetime
import requests
import subprocess
import sqlite3
import logging
from dotenv import load_dotenv
from collections import defaultdict
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

load_dotenv()

# logs
logging.basicConfig(filename='/tmp/expire.log',
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# Variables
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID") 
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Webhook URL for Google Chats
DB_FILE = os.getenv("DB_FILE")
RANGE_NAME = 'tabname!AD123456:AD654321'  # Location of the domains you want to check in the spreadsheet
WEEKS_AHEAD = 1  # Select how many weeks before you want to get the notification

# Creates database and table if not existing
def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notifications_sent
    (domain TEXT, expiry_date DATE, notified_date DATE, PRIMARY KEY (domain, expiry_date))
    ''')
    conn.commit()
    conn.close()

# Check if notification should be sent
def should_send_notification(domain, expiry_date):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT 1 FROM notifications_sent WHERE domain = ? AND expiry_date = ?
    ''', (domain, expiry_date))
    result = cursor.fetchone()
    conn.close()
    return result is None

# Sends notification for domains expiring on the same date
def send_notifications(expiring_domains_by_date):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    for expiry_date, domains in expiring_domains_by_date.items():
        domains_to_notify = [domain for domain in domains if should_send_notification(domain, expiry_date)]
        if not domains_to_notify:
            continue  

        message = f"Domains expiring on {expiry_date}: {', '.join(domains_to_notify)}"
        response = requests.post(WEBHOOK_URL, json={"text": message})
        logging.info(f"Notification sent for domains expiring on {expiry_date}, response status: {response.status_code}")

        for domain in domains_to_notify:
            notified_date = datetime.datetime.now().date()
            cursor.execute('''
            INSERT INTO notifications_sent (domain, expiry_date, notified_date) VALUES (?, ?, ?)
            ''', (domain, expiry_date, notified_date))
            conn.commit()

    conn.close()


def main():
    setup_database()

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    service = build('sheets', 'v4', credentials=credentials)

    result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
    domains = result.get('values', [])

    now = datetime.datetime.now().date()
    target_date_from_now = now + datetime.timedelta(weeks=WEEKS_AHEAD)
    expiring_domains_by_date = defaultdict(list)

    if domains:
        for row in domains:
            domain = row[0].strip()
            try:
                result = subprocess.run(["whois", domain], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                output = result.stdout

                for line in output.splitlines():
                    if "Expiration Date" in line or "Registry Expiry Date" in line:
                        expiry_str = line.split(":")[1].strip().split('T')[0]
                        expiry_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d").date()
                        if now <= expiry_date <= target_date_from_now:
                            expiring_domains_by_date[expiry_str].append(domain)
                        break
            except Exception as e:
                logging.error(f"Error processing {domain}: {e}")

    if expiring_domains_by_date:
        send_notifications(expiring_domains_by_date)
    else:
        logging.info("No domains expiring within the specified period.")

if __name__ == "__main__":
    main()
