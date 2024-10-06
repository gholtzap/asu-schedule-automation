import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
from flask import Flask, request, redirect, url_for, render_template, session, flash
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pytesseract
from PIL import Image
import os
import datetime
from datetime import datetime, timedelta, time
import re
import cv2
import numpy as np
import pytz
import logging
import uuid
from pytz import timezone
import requests
from io import BytesIO
import base64
import json
from oauthlib.oauth2.rfc6749.errors import MissingCodeError
from dotenv import load_dotenv
import random
import string

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

SCOPES = ['https://www.googleapis.com/auth/calendar']
load_dotenv()

app.secret_key = os.getenv('FLASK_SECRET_KEY')

if not app.secret_key:
    raise ValueError("No SECRET_KEY set for Flask application")

CLIENT_SECRETS_FILE = os.getenv('GOOGLE_CREDENTIALS_FILENAME')

if not CLIENT_SECRETS_FILE:
    raise ValueError("No CLIENT_SECRETS_FILE found in the .env file")

def get_calendar_service(credentials):
    service = build('calendar', 'v3', credentials=credentials)
    return service

def extract_text_from_image(image_path):
    
    img = cv2.imread(image_path)
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    
    kernel = np.ones((1, 1), np.uint8)
    processed_img = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    
    custom_config = r'--oem 3 --psm 6'
    
    text = pytesseract.image_to_string(processed_img, config=custom_config)
    return text

def parse_schedule(text):
    events = []
    lines = text.splitlines()
    combined_lines = []
    buffer = ''
    for line in lines:
        stripped_line = line.strip()
        if re.match(r'^\d{5,6}', stripped_line):
            if buffer:
                combined_lines.append(buffer)
            buffer = stripped_line
        else:
            buffer += ' ' + stripped_line
    if buffer:
        combined_lines.append(buffer)
    for line in combined_lines:
        if not line.strip():
            continue
        if 'icourse' in line.lower():
            continue
        try:
            event = parse_line(line)
            if not event:
                continue
            
            day_mapping = {
                'M': 'MO',
                'TU': 'TU',
                'W': 'WE',
                'TH': 'TH',
                'F': 'FR',
                'SA': 'SA',
                'SU': 'SU',
                'MW': ['MO', 'WE'],
                'MWF': ['MO', 'WE', 'FR'],
                'TUTH': ['TU', 'TH'],
                'MTWTHF': ['MO', 'TU', 'WE', 'TH', 'FR'],
                'TBA': [],
                'ARRANGED': []
            }
            days_list = event['days_str'].upper().replace(',', '').split()
            days_combined = ''.join(days_list)
            if days_combined in day_mapping:
                mapped_days = day_mapping[days_combined]
                event_days = mapped_days if isinstance(mapped_days, list) else [mapped_days]
            else:
                
                event_days = []
                for day in days_list:
                    day = day.strip().upper()
                    mapped_day = day_mapping.get(day)
                    if mapped_day:
                        if isinstance(mapped_day, list):
                            event_days.extend(mapped_day)
                        else:
                            event_days.append(mapped_day)
                    else:
                        logging.warning(f"Unrecognized day: {day}")
            event['days_of_week'] = event_days
            
            time_range_match = re.search(
                r'(\d{1,2}:\d{2}\s?(AM|PM))\s*[-–—]\s*(\d{1,2}:\d{2}\s?(AM|PM))',
                event['time_str'],
                re.IGNORECASE
            )
            if time_range_match:
                event['start_time_str'] = normalize_time_format(time_range_match.group(1))
                event['end_time_str'] = normalize_time_format(time_range_match.group(3))
            else:
                event['start_time_str'] = None
                event['end_time_str'] = None
            
            date_range_match = re.search(
                r'(\d{1,2}/\d{1,2}/\d{2})\s*[-–—]\s*(\d{1,2}/\d{1,2}/\d{2})',
                event['date_str']
            )
            if date_range_match:
                event['start_date_str'] = date_range_match.group(1)
                event['end_date_str'] = date_range_match.group(2)
            else:
                event['start_date_str'] = None
                event['end_date_str'] = None
            tz = pytz.timezone('America/Phoenix')  
            
            if 'start_date_str' in event and event['start_date_str'] and event['start_time_str']:
                start_datetime_str = f"{event['start_date_str']} {event['start_time_str']}"
                naive_start = datetime.strptime(start_datetime_str, '%m/%d/%y %I:%M %p')
                event['start'] = tz.localize(naive_start)
                logging.info(f"Localized start time: {event['start']}")
            else:
                event['start'] = None
            
            if 'start_date_str' in event and event['start_date_str'] and event['end_time_str']:
                end_datetime_str = f"{event['start_date_str']} {event['end_time_str']}"
                naive_end = datetime.strptime(end_datetime_str, '%m/%d/%y %I:%M %p')
                event['end'] = tz.localize(naive_end)
                logging.info(f"Localized end time: {event['end']}")
            else:
                event['end'] = None
            if 'end_date_str' in event and event['end_date_str']:
                event['end_date'] = datetime.strptime(event['end_date_str'], '%m/%d/%y')
            else:
                event['end_date'] = None
            if event['start'] and event['end'] and event['days_of_week']:
                
                event['id'] = str(uuid.uuid4())
                event.pop('start', None)
                event.pop('end', None)
                event.pop('end_date', None)
                events.append(event)
            logging.info(f"Parsing event: {event['summary']}")
            logging.info(f"Extracted start time: {event.get('start_time_str')}")
            logging.info(f"Extracted end time: {event.get('end_time_str')}")
            logging.info(f"Days of week for event: {event.get('days_of_week')}")
        except Exception as e:
            logging.error(f"Error parsing line: {line}")
            logging.error(f"Exception: {e}")
            continue
    return events
def normalize_time_format(time_str):
    """Ensure there is a space between the time and AM/PM."""
    return re.sub(r'(\d)(AM|PM)', r'\1 \2', time_str)
def parse_line(line):
    tokens = line.strip().split()
    index = 0
    
    if index < len(tokens) and re.match(r'^\d{5,6}$', tokens[index]):
        class_num = tokens[index]
        index +=1
    else:
        logging.error("No class number found")
        return None
    
    if index+1 < len(tokens):
        course_code = tokens[index] + ' ' + tokens[index+1]
        if re.match(r'^[A-Z]{2,4}\s?\d{3}$', course_code):
            index +=2
        else:
            
            course_code = tokens[index]
            if re.match(r'^[A-Z]{2,4}\d{3}$', course_code):
                index +=1
            else:
                logging.error("No course code found")
                return None
    else:
        logging.error("No course code found")
        return None
    
    title_tokens = []
    while index < len(tokens) and not re.match(r'^\d+\.\d+$', tokens[index]):
        title_tokens.append(tokens[index])
        index +=1
    title = ' '.join(title_tokens)
    
    if index < len(tokens) and re.match(r'^\d+\.\d+$', tokens[index]):
        units = tokens[index]
        index +=1
    else:
        logging.error("No units found")
        return None
    
    day_abbreviations = ['M','Tu','W','Th','F','Sa','Su','MW','MF','MWF','TBA','Arranged']
    instructor_tokens = []
    while index < len(tokens) and tokens[index] not in day_abbreviations:
        instructor_tokens.append(tokens[index])
        index +=1
    instructors = ' '.join(instructor_tokens)
    
    days_tokens = []
    while index < len(tokens) and tokens[index] in day_abbreviations:
        days_tokens.append(tokens[index])
        index +=1
    days_str = ' '.join(days_tokens)
    
    time_tokens = []
    while index < len(tokens) and not re.match(r'^\d{1,2}/\d{1,2}/\d{2}$', tokens[index]):
        time_tokens.append(tokens[index])
        index +=1
    time_str = ' '.join(time_tokens)
    
    date_tokens = []
    while index < len(tokens) and not tokens[index].startswith('Tempe'):
        date_tokens.append(tokens[index])
        index +=1
    date_str = ' '.join(date_tokens)
    
    location_tokens = tokens[index:]
    location = ' '.join(location_tokens)
    
    event = {}
    event['summary'] = f"{course_code} - {title}"
    event['class_num'] = class_num
    event['units'] = units
    event['instructors'] = instructors
    event['days_str'] = days_str
    event['time_str'] = time_str
    event['date_str'] = date_str
    event['location'] = location
    return event
def add_events_to_calendar(events, credentials):
    service = get_calendar_service(credentials)
    calendar_id = 'primary'
    for event in events:
        
        
        if event['end_date']:
            tz = pytz.timezone('America/Phoenix')
            until_datetime = tz.localize(datetime.combine(event['end_date'], datetime.max.time()))
            until_datetime_utc = until_datetime.astimezone(pytz.utc)
            until_date_str = until_datetime_utc.strftime('%Y%m%dT%H%M%SZ')
        else:
            until_date_str = None
        if event['days_of_week'] and until_date_str:
            recurrence_rule = f"RRULE:FREQ=WEEKLY;BYDAY={','.join(event['days_of_week'])};UNTIL={until_date_str}"
            recurrence = [recurrence_rule]
        else:
            recurrence = None
        event_body = {
            'summary': event['summary'],
            'location': event['location'],
            'start': {
                'dateTime': event['start'].isoformat(),
                'timeZone': 'America/Phoenix'
            },
            'end': {
                'dateTime': event['end'].isoformat(),
                'timeZone': 'America/Phoenix'
            },
            'recurrence': recurrence
        }
        logging.info(f"Start datetime ISO format: {event['start'].isoformat()}")
        logging.info(f"End datetime ISO format: {event['end'].isoformat()}")
        logging.info(f"Adding event: {event_body}")
        service.events().insert(calendarId=calendar_id, body=event_body).execute()
        logging.info(f"Added event: {event['summary']}")

@app.route('/authorize')
def authorize():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    session['state'] = state
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    logging.info(f"Authorization Response URL: {authorization_response}")

    try:
        flow.fetch_token(authorization_response=authorization_response)
    except MissingCodeError as e:
        logging.error(f"Missing authorization code in the response: {e}")
        flash("Authorization failed. Missing code parameter in the response.")
        return redirect(url_for('upload_image'))

    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    flash('Authentication successful! Please confirm your events.')
    return redirect(url_for('confirm_events'))


def credentials_to_dict(credentials):
    return {'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes}

@app.route('/', methods=['GET', 'POST'])
def upload_image():
    if request.method == 'POST':
        image_path = None
        if 'image' in request.files and request.files['image'].filename != '':
            
            file = request.files['image']
            image_path = 'uploaded_image.png'
            file.save(image_path)
        elif 'image_url' in request.form and request.form['image_url']:
            
            image_url = request.form['image_url']
            try:
                response = requests.get(image_url)
                img = Image.open(BytesIO(response.content))
                image_path = 'uploaded_image_from_url.png'
                img.save(image_path)
            except Exception as e:
                flash('Invalid image URL.')
                return redirect(request.url)
        elif 'paste_data' in request.form and request.form['paste_data']:
            
            paste_data = request.form['paste_data']
            if paste_data.startswith('data:image/'):
                image_data = base64.b64decode(paste_data.split(',')[1])
                image_path = 'pasted_image.png'
                with open(image_path, 'wb') as f:
                    f.write(image_data)
            else:
                flash('Invalid pasted image data.')
                return redirect(request.url)
        else:
            flash('No image source provided.')
            return redirect(request.url)
        
        if image_path:
            
            schedule_text = extract_text_from_image(image_path)
            events = parse_schedule(schedule_text)
            if not events:
                flash('No events found in the image.')
                return redirect(request.url)
            
            session['events'] = events
            return redirect(url_for('confirm_events'))  
    return render_template('upload.html')

@app.route('/confirm', methods=['GET', 'POST'])
def confirm_events():
    
    if 'events' not in session:
        flash('No events to confirm.')
        return redirect(url_for('upload_image'))
    events = session['events']
    
    tz = pytz.timezone('America/Phoenix')
    for event in events:
        
        if event.get('start_date_str') and event.get('start_time_str'):
            start_str = f"{event['start_date_str']} {event['start_time_str']}"
            naive_start = datetime.strptime(start_str, '%m/%d/%y %I:%M %p')
            event['start'] = tz.localize(naive_start)
        else:
            event['start'] = None
        
        if event.get('start_date_str') and event.get('end_time_str'):
            end_str = f"{event['start_date_str']} {event['end_time_str']}"
            naive_end = datetime.strptime(end_str, '%m/%d/%y %I:%M %p')
            event['end'] = tz.localize(naive_end)
        else:
            event['end'] = None
        
        if event.get('end_date_str'):
            event['end_date'] = datetime.strptime(event['end_date_str'], '%m/%d/%y')
        else:
            event['end_date'] = None
    if request.method == 'POST':
        selected_event_ids = request.form.getlist('event')
        selected_events = [event for event in events if event['id'] in selected_event_ids]
        if not selected_events:
            flash('No events selected.')
            return redirect(url_for('upload_image'))
        
        credentials = session.get('credentials', None)
        if credentials:
            credentials = Credentials(**credentials)
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                session['credentials'] = credentials_to_dict(credentials)
            else:
                return redirect(url_for('authorize')) 
        add_events_to_calendar(selected_events, credentials)
        flash('Selected events added to your Google Calendar.')
        session.pop('events', None)
        return redirect(url_for('upload_image'))
    
    reverse_day_mapping = {
        'MO': 'M',
        'TU': 'Tu',
        'WE': 'W',
        'TH': 'Th',
        'FR': 'F',
        'SA': 'Sa',
        'SU': 'Su'
    }
    for event in events:
        event['days_str'] = ', '.join([reverse_day_mapping.get(d, d) for d in event['days_of_week']])
    return render_template('confirm.html', events=events)
if __name__ == '__main__':
    app.run(debug=True)
