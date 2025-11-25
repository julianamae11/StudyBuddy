import mysql.connector
import os
import datetime
import tempfile
from mysql.connector import Error
from authlib.integrations.flask_client import OAuth
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename
from flask import request, redirect, url_for, flash
from datetime import date



# --- 1. Configuration ---
# Your MySQL Database Configuration
# We use os.environ.get to read from Vercel's Environment Variables.
# If they don't exist (like locally), we default to localhost values.
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "passwd": os.environ.get("DB_PASSWORD", ""), 
    "database": os.environ.get("DB_NAME", "study_buddy3"),
    "port": int(os.environ.get("DB_PORT", 3306))
}

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "1001684728871-dkd8nbuml9mk1p0etviclhpfbr54mgh5.apps.googleusercontent.com")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "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")

# --- Global State and Initialization ---
app = Flask(__name__)
# IMPORTANT: Session secret key required for Flask sessions (used by OAuth)
app.secret_key = os.environ.get("SECRET_KEY", '!SuperSecretKeyForSession!') 

# Simple placeholder for tracking logged-in user (USE FLASK SESSIONS IN PRODUCTION!)
LOGGED_IN_USER_ID = None 

UPLOAD_FOLDER = os.path.join('/tmp', 'uploads')
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx'}


# Initialize Authlib OAuth client
oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID, # Corrected case-sensitivity
    client_secret=GOOGLE_CLIENT_SECRET,
    access_token_url='https://oauth2.googleapis.com/token',
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    client_kwargs={'scope': 'openid email profile'},
    jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
)

def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


class study_buddyDB:
    """Handles all MySQL database connections and operations."""
    
    def __init__(self, config):
        self.config = config
        self.connection = None
        self.connect()

    def connect(self):
        """Establishes the MySQL database connection, omitting password if empty."""
        try:
            config_copy = self.config.copy()
            if not config_copy['passwd']:
                del config_copy['passwd']

            # Handle SSL if provided in environment (Common for cloud databases like Aiven/Azure)
            if os.environ.get("DB_SSL_CA"):
                config_copy['ssl_ca'] = os.environ.get("DB_SSL_CA")
                config_copy['ssl_disabled'] = False

            self.connection = mysql.connector.connect(**config_copy)
            if self.connection.is_connected():
                print("✅ MySQL Connection established.")
                return True
            return False
        except Error as e:
            print(f"❌ Error connecting to MySQL: {e}")
            self.connection = None
            return False

    def check_connection(self):
        """
        Checks if the connection is active. If not, attempts to reconnect.
        Returns True if connection is available, False otherwise.
        """
        if self.connection is None or not self.connection.is_connected():
            print("⚠️ DB connection lost. Attempting to reconnect...")
            return self.connect()
        return True
    
    # --- Functional Requirement 8: Study Reminder Fetcher (New) ---
    def fetch_most_difficult_incomplete_topic(self, user_id):
        """
        Fetches the single incomplete topic with the highest difficulty rating,
        breaking ties by estimated study time (longest first).
        Returns a dictionary {subject_name, topic_name, difficulty_rating} or None.
        """
        if not self.check_connection(): return None

        query = """
            SELECT 
                s.subject_name,
                t.topic_name,
                t.difficulty_rating
            FROM 
                topics t
            JOIN 
                subjects s ON t.subject_id = s.subject_id
            WHERE 
                s.user_id = %s AND t.is_completed = FALSE
            ORDER BY
                t.difficulty_rating DESC, t.estimated_study_time_hrs DESC
            LIMIT 1;
        """
        cursor = self.connection.cursor(dictionary=True)
        
        try:
            cursor.execute(query, (user_id,))
            result = cursor.fetchone()
            
            # Return the dictionary or None if no incomplete topics are found
            return result
        except Error as e:
            print(f"❌ Error fetching most difficult topic: {e}")
            return None
        finally:
            cursor.close()

    def close(self):
        """Closes the MySQL database connection."""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            
    # --- DB METHOD: Get Username (for dashboard personalization) ---
    def get_username_by_id(self, user_id):
        """Retrieves the username for a given user_id."""
        if not self.check_connection(): return "Guest"
        
        select_query = "SELECT username FROM users WHERE user_id = %s"
        cursor = self.connection.cursor()
        
        try:
            cursor.execute(select_query, (user_id,))
            result = cursor.fetchone()
            # Returns the username string, or a fallback if not found
            return result[0] if result and result[0] else "Unknown User"
        except Error as e:
            print(f"❌ Error fetching username: {e}")
            return "Error User"
        finally:
            cursor.close()
            
    # --- Functional Requirement 1: Login/Registration methods ---
    def register_user(self, username, password, email):
        """Inserts a new user registered via the manual form."""
        if not self.check_connection(): return False, "Database connection failed."
        
        insert_query = "INSERT INTO users (username, password, email) VALUES (%s, %s, %s)"
        cursor = self.connection.cursor()
        
        try:
            cursor.execute(insert_query, (username, password, email))
            self.connection.commit()
            return True, "User registered successfully! You can now log in."
        except Error as e:
            if e.errno == 1062:
                return False, "Registration failed: Username or Email already exists."
            return False, f"Registration failed due to DB error: {e}"
        finally:
            cursor.close()
            
    def login_or_register_google(self, google_id, email, name):
        """Handles both login and registration for Google users (upsert/on registration page)."""
        if not self.check_connection(): return None

        cursor = self.connection.cursor()
        
        # 1. Check if user already exists via google_id
        select_query = "SELECT user_id FROM users WHERE google_id = %s"
        cursor.execute(select_query, (google_id,))
        user_record = cursor.fetchone()

        if user_record:
            user_id = user_record[0]
            cursor.close()
            return user_id # Login successful
        
        # 2. If not found, check if email exists (prevent duplicate accounts)
        select_query = "SELECT user_id FROM users WHERE email = %s"
        cursor.execute(select_query, (email,))
        user_record = cursor.fetchone()
        
        if user_record:
            # Email exists, update the existing account with google_id and log them in
            update_query = "UPDATE users SET google_id = %s WHERE user_id = %s"
            cursor.execute(update_query, (google_id, user_record[0]))
            self.connection.commit()
            cursor.close()
            return user_record[0]
        
        # 3. If neither exists, create a new account (Registration behavior)
        insert_query = "INSERT INTO users (username, password, email, google_id) VALUES (%s, %s, %s, %s)"
        try:
            cursor.execute(insert_query, (name, 'GOOGLE_AUTH_PLACEHOLDER', email, google_id))
            self.connection.commit()
            user_id = cursor.lastrowid
            return user_id
        except Error as e:
            print(f"❌ Google Registration Error: {e}")
            return None
        finally:
            cursor.close()
            
    def login_google_strict(self, google_id, email):
        """
        Authenticates Google users ONLY if they already exist in the database 
        (via google_id or email). Does not register new users (Strict Login behavior).
        """
        if not self.check_connection(): return None

        cursor = self.connection.cursor()
        
        # 1. Check if user already exists via google_id
        select_query_id = "SELECT user_id FROM users WHERE google_id = %s"
        cursor.execute(select_query_id, (google_id,))
        user_record = cursor.fetchone()

        if user_record:
            cursor.close()
            return user_record[0] # Login successful via google_id
        
        # 2. Check if user already exists via email (for manual account linkage)
        select_query_email = "SELECT user_id FROM users WHERE email = %s"
        cursor.execute(select_query_email, (email,))
        user_record = cursor.fetchone()
        
        if user_record:
            # Email exists, update the existing account with google_id and log them in
            update_query = "UPDATE users SET google_id = %s WHERE user_id = %s"
            try:
                cursor.execute(update_query, (google_id, user_record[0]))
                self.connection.commit()
            except Error as e:
                # Log but don't fail login if the update fails
                print(f"❌ Error linking Google ID to existing account: {e}")
                
            cursor.close()
            return user_record[0] # Login successful via email linkage

        # 3. User not found by ID or Email, strict login fails.
        cursor.close()
        return None
            
    def login_user(self, username, password):
        """Authenticates a user and returns their user_id."""
        if not self.check_connection(): return None
        select_query = "SELECT user_id FROM users WHERE username = %s AND password = %s"
        cursor = self.connection.cursor()
        try:
            cursor.execute(select_query, (username, password))
            result = cursor.fetchone()
            return result[0] if result else None
        except Error as e:
            print(f"❌ Login error: {e}")
            return None
        finally:
            cursor.close()

    def fetch_all_subjects_and_topics(self, user_id):
        subjects_data = {}
        # 🎯 FIX 3: Initialize local db and cursor to None
        db = None
        cursor = None

        try:
            # 🎯 FIX 4: Open a fresh connection explicitly for this SELECT query
            config_copy = self.config.copy()
            if not config_copy.get('passwd'):
                del config_copy['passwd']

            db = mysql.connector.connect(**config_copy) 
            # Use dictionary=True for safe mapping
            cursor = db.cursor(dictionary=True)
            
            query = """
            SELECT 
                s.subject_id,
                s.subject_name,
                t.topic_id, 
                t.topic_name,
                t.estimated_study_time_hrs,
                t.difficulty_rating,
                t.is_completed, 
                t.completion_date
            FROM 
                subjects s
            LEFT JOIN 
                topics t ON s.subject_id = t.subject_id
            WHERE 
                s.user_id = %s
            ORDER BY 
                s.subject_name ASC, t.topic_id ASC;
            """
            
            cursor.execute(query, (user_id,))
            results = cursor.fetchall()
            
            # ... (Rest of the data structuring logic is now correct) ...
            for row in results:
                
                subject_name = row['subject_name']
                
                if subject_name not in subjects_data:
                    subjects_data[subject_name] = {
                        'subject_id': row['subject_id'],
                        'topics': []
                    }
                    
                if row['topic_name']: 
                    subjects_data[subject_name]['topics'].append({
                        'topic_id': row['topic_id'],
                        'name': row['topic_name'],
                        'time': float(row['estimated_study_time_hrs']) if row['estimated_study_time_hrs'] is not None else 0.0,
                        'difficulty': row['difficulty_rating'],
                        'is_completed': bool(row['is_completed']), 
                        'completion_date': row['completion_date']
                    })
                    
            return subjects_data

        except Exception as e: 
            print(f"❌ Error fetching all subjects and topics: {e}")
            return {}
            
        finally:
            # 🎯 FIX 5: Crucial: Always close the temporary cursor AND connection
            if cursor: 
                cursor.close()
            if db:
                db.close()
                
        # --- Functional Requirement 3: Schedule Data Fetcher ---
    def fetch_prioritized_topics(self, user_id):
            """
            Fetches all incomplete topics for the user, prioritized by (difficulty * time).
            """
            if not self.check_connection(): return []
            
            select_query = """
                SELECT
                    t.topic_id,
                    s.subject_name,
                    t.topic_name,
                    t.estimated_study_time_hrs,
                    t.difficulty_rating
                FROM
                    topics t
                JOIN
                    subjects s ON t.subject_id = s.subject_id
                WHERE
                    s.user_id = %s AND t.is_completed = FALSE
                ORDER BY
                    (t.estimated_study_time_hrs * t.difficulty_rating) DESC, t.estimated_study_time_hrs DESC;
            """
            cursor = self.connection.cursor(dictionary=True)
            
            try:
                cursor.execute(select_query, (user_id,))
                return cursor.fetchall()
            except Error as e:
                print(f"❌ Error fetching prioritized topics: {e}")
                return []
            finally:
                cursor.close()

        # --- Functional Requirement 5: Dashboard Summary --- 
    def fetch_dashboard_summary(self, user_id):
            """
            Calculates key statistics for the dashboard home page.
            """
            if not self.check_connection(): return None

            query = """
                SELECT
                    COUNT(t.topic_id) AS total_topics,
                    SUM(t.is_completed = TRUE) AS completed_topics,
                    SUM(t.estimated_study_time_hrs) AS total_estimated_time,
                    AVG(t.difficulty_rating) AS avg_difficulty
                FROM
                    topics t
                JOIN
                    subjects s ON t.subject_id = s.subject_id
                WHERE
                    s.user_id = %s;
            """
            cursor = self.connection.cursor(dictionary=True) 
            
            try:
                cursor.execute(query, (user_id,))
                summary = cursor.fetchone()

                # --- Explicitly convert all database Decimal/None values to standard Python types ---
                
                # Handle case where user has no topics yet or summary is empty
                if not summary or summary['total_topics'] is None or summary['total_topics'] == 0:
                    return {
                        'total_topics': 0, 'completed_topics': 0, 
                        'total_estimated_time': 0.0, 'avg_difficulty': 0.0,
                        'completion_percentage': 0.0, 'remaining_topics': 0
                    }

                # Explicitly cast retrieved values to prevent Decimal/float conflicts
                total_topics = int(summary['total_topics'])
                completed_topics = int(summary['completed_topics'] if summary['completed_topics'] else 0)
                total_estimated_time = float(summary['total_estimated_time']) if summary['total_estimated_time'] else 0.0
                avg_difficulty = float(summary['avg_difficulty']) if summary['avg_difficulty'] else 0.0

                # Rebuild the final summary dictionary
                summary_data = {
                    'total_topics': total_topics,
                    'completed_topics': completed_topics, 
                    'total_estimated_time': total_estimated_time, 
                    'avg_difficulty': round(avg_difficulty, 1),
                }

                # Calculate completion percentage using safe types
                if summary_data['total_topics'] > 0:
                    summary_data['completion_percentage'] = round((summary_data['completed_topics'] / summary_data['total_topics']) * 100, 1)
                else:
                    summary_data['completion_percentage'] = 0.0
                    
                summary_data['remaining_topics'] = summary_data['total_topics'] - summary_data['completed_topics']
                    
                return summary_data

            except Error as e:
                print(f"❌ Error fetching dashboard summary: {e}")
                return None
            finally:
                cursor.close()

        # --- Functional Requirement 2: Add Subject/Topic ---
        # Assuming this is inside your DBManager class definition

    # ... (Previous methods like add_subject, check_connection, etc.)

    def add_topic(self, subject_id, topic_name, study_time, difficulty, scheduled_datetime=None, file_path=None):
        if not self.check_connection(): 
            return False, "Database connection failed."
    
    # Use the parameter 'file_path' for the material path variable
        material_path = file_path if file_path else None
        
        insert_query = """
    INSERT INTO topics 
    (subject_id, topic_name, estimated_study_time_hrs, difficulty_rating, scheduled_datetime, study_material_path) 
    VALUES (%s, %s, %s, %s, %s, %s) 
    """
    
        cursor = self.connection.cursor()
    
        try:
            # Pass the 'material_path' variable to the execution tuple
            cursor.execute(insert_query, (subject_id, topic_name, study_time, difficulty, scheduled_datetime, material_path))
            self.connection.commit()

            # The rest of your logic, using the variable name 'material_path'
            filename = os.path.basename(material_path) if material_path else None
            
            file_msg = f" (Material link for '{filename}' saved)" if filename else ""
            return True, f"Topic added and scheduled successfully!{file_msg}"
    
        except Exception as e: # Catch a broader Exception if 'Error' is undefined
            print(f"❌ SQL INSERTION FAILED: {e}") 
            # Attempt to access the message attribute, falling back to str(e)
            return False, f"Failed to add topic: {getattr(e, 'msg', str(e))}"
            
        finally:
            cursor.close()

    # --- FIX START: The next method must include 'self' and be properly defined ---

    def get_subjects_by_user(self, user_id): 
        """Retrieves a list of (subject_id, subject_name) for a user's subjects."""
        
        if not self.check_connection(): 
            return []
            
        subjects = []
        
        select_query = "SELECT subject_id, subject_name FROM subjects WHERE user_id = %s"
        cursor = self.connection.cursor(dictionary=True) # Use dictionary=True for easier key access
        
        try:
            # Assuming your subjects table has a user_id column
            cursor.execute(select_query, (user_id,))
            
            # Fetch all results
            subjects = [(row['subject_id'], row['subject_name']) for row in cursor.fetchall()]
            
        except Error as e:
            print(f"❌ ERROR fetching subjects: {e}")
            
        finally:
            cursor.close()
            
        return subjects # Return the list of subjects

    # ... (Add other remaining methods here, like get_schedule, etc.) ...
    def fetch_all_subjects_and_topics(self, user_id):
        """
        Fetches all subjects and their associated topics for the main user, 
        including topic_id for completion tracking.
        """
        # Assuming self.check_connection() handles connection lifecycle and ensures self.connection is available
        if not self.check_connection(): 
            return {}

        query = """
        SELECT 
            s.subject_id,
            s.subject_name,
            t.topic_id, 
            t.topic_name,
            t.estimated_study_time_hrs,
            t.difficulty_rating,
            t.is_completed  -- 🎯 FIX 1: Removed alias 'As completed' to use the column name t.is_completed
        FROM 
            subjects s
        LEFT JOIN 
            topics t ON s.subject_id = t.subject_id
        WHERE 
            s.user_id = %s
        ORDER BY 
            s.subject_name ASC, t.topic_id ASC;
        """
        
        # 🎯 FIX 2: Use dictionary=True cursor for safe mapping (highly recommended!)
        # If using mysql.connector, this is the safest way to prevent index errors.
        cursor = self.connection.cursor(dictionary=True) 
        
        subjects_data = {}
        
        try:
            cursor.execute(query, (user_id,))
            results = cursor.fetchall()
            
            # Structure the data into a dictionary: {SubjectName: [Topics...]}
            for row in results:
                
                # Since we are using dictionary=True, we don't need manual unpacking like 'row = subject_id, subject_name, ...'
                subject_name = row['subject_name']
                
                if subject_name not in subjects_data:
                    subjects_data[subject_name] = {
                        'subject_id': row['subject_id'],
                        'topics': []
                    }
                    
                if row['topic_name']: # Only add topic if it exists (Left Join can return NULLs for topics)
                    subjects_data[subject_name]['topics'].append({
                        'topic_id': row['topic_id'],
                        'name': row['topic_name'],
                        'time': float(row['estimated_study_time_hrs']) if row['estimated_study_time_hrs'] is not None else 0.0,
                        'difficulty': row['difficulty_rating'],
                        'is_completed': bool(row['is_completed']) # 🎯 FIX 3: Use the correct key and convert MySQL 1/0 to Python bool
                    })
                    
            return subjects_data

        except Exception as e: # Changed generic 'Error' to 'Exception' for broad coverage
            print(f"❌ Error fetching all subjects and topics: {e}")
            return {}
            
        finally:
            # Assuming cursor.close() is handled here
            if cursor:
                cursor.close()
                
        # --- Functional Requirement 3: Schedule Data Fetcher ---
    def fetch_prioritized_topics(self, user_id):
            """
            Fetches all incomplete topics for the user, prioritized by (difficulty * time).
            """
            if not self.check_connection(): return []
            
            select_query = """
                SELECT
                    t.topic_id,
                    s.subject_name,
                    t.topic_name,
                    t.estimated_study_time_hrs,
                    t.difficulty_rating,
                    t.study_material_path,
                    t.scheduled_datetime
                FROM
                    topics t
                JOIN
                    subjects s ON t.subject_id = s.subject_id
                WHERE
                    s.user_id = %s AND t.is_completed = FALSE
                ORDER BY
                    (t.estimated_study_time_hrs * t.difficulty_rating) DESC, t.estimated_study_time_hrs DESC;
            """
            cursor = self.connection.cursor(dictionary=True)
            
            try:
                cursor.execute(select_query, (user_id,))
                return cursor.fetchall()
            except Error as e:
                print(f"❌ Error fetching prioritized topics: {e}")
                return []
            finally:
                if cursor:
                    cursor.close()
                # 🎯 ACTION: Add this line to ensure the connection is fully closed.
                if self.connection: 
                    self.connection.close()

        # --- Functional Requirement 5: Dashboard Summary --- 


        # --- NEW DB METHOD: Fetch Historical Study Sessions (Functional Requirement 7) ---
    def fetch_historical_schedule(self, user_id):
            """
            Fetches a list of completed topics, grouped by the date they were completed.
            Returns a dictionary: {formatted_date_str: [list_of_topics_on_that_date]}
            """
            if not self.check_connection(): return {}

            query = """
                SELECT
                    t.topic_name,
                    s.subject_name,
                    DATE_FORMAT(t.completion_date, '%%Y-%%m-%%d') AS completion_date_str, 
                    t.estimated_study_time_hrs
                FROM
                    topics t
                JOIN
                    subjects s ON t.subject_id = s.subject_id
                WHERE
                    s.user_id = %s AND t.is_completed = TRUE AND t.completion_date IS NOT NULL
                ORDER BY
                    t.completion_date DESC, s.subject_name ASC;
            """
            cursor = self.connection.cursor(dictionary=True)

            historical_data = {}

            try:
                cursor.execute(query, (user_id,))
                results = cursor.fetchall()
                
                # Group results by date
                for row in results:
                    date_str = row['completion_date_str']
                    
                    # Convert 'YYYY-MM-DD' string back to a date object for user-friendly formatting
                    try:
                        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                        formatted_date = date_obj.strftime("%A, %B %d, %Y")
                    except ValueError:
                        formatted_date = date_str # Fallback if parsing fails

                    if formatted_date not in historical_data:
                        historical_data[formatted_date] = []

                    historical_data[formatted_date].append({
                        'subject': row['subject_name'],
                        'topic': row['topic_name'],
                        'time_spent': float(row['estimated_study_time_hrs']) if row['estimated_study_time_hrs'] else 0.0
                    })

                return historical_data
            
            except Error as e:
                print(f"❌ Error fetching historical schedule: {e}")
                return {}
            finally:
                cursor.close()


    # --- 2. Schedule Generation Logic (Functional Requirement 3) ---
    
    def add_subject(self, user_id, subject_name):
        """Inserts a new subject for a specific user."""
        if not self.connection: return False, "Database connection failed."
        
        insert_query = "INSERT INTO subjects (user_id, subject_name) VALUES (%s, %s)"
        cursor = self.connection.cursor()
        
        try:
            cursor.execute(insert_query, (user_id, subject_name))
            self.connection.commit()
            subject_id = cursor.lastrowid
            return subject_id, "Subject added successfully!"
        except Error as e:
            if e.errno == 1062:
                return False, f"Subject '{subject_name}' already exists for this user."
            return False, f"Failed to add subject: {e}"
        finally:
            cursor.close()

    def mark_topic_complete_db(self, topic_id, user_id): 
        MYSQL_TRUE = 1 
        cursor = None # Only the cursor needs to be scoped to the try block
        
        # 🎯 FIX 1: Use the class's established connection
        if not self.check_connection(): 
            print("DEBUG: Connection check failed.")
            return False

        cursor = self.connection.cursor() # Use the default cursor for UPDATE
        
        try:
            current_date = date.today().strftime('%Y-%m-%d')
            
            sql_update = """
            UPDATE topics t
            JOIN subjects s ON t.subject_id = s.subject_id
            SET 
                t.is_completed = %s, 
                t.completion_date = %s 
            WHERE 
                t.topic_id = %s 
                AND s.user_id = %s;
            """
            
            params = (MYSQL_TRUE, current_date, topic_id, user_id)
            
            cursor.execute(sql_update, params)
            self.connection.commit() # Commit on the main connection
            
            if cursor.rowcount == 0:
                print(f"DEBUG: DB update failed. Topic ID {topic_id} not found or doesn't belong to User ID {user_id}.")
                return False 
                
            return True
                
        except Exception as e:
            self.connection.rollback() # Rollback on the main connection
            print(f"Database error on marking topic complete: {e}") 
            return False
            
        finally:
            # 🎯 FIX 2: Only close the cursor, not the main connection
            if cursor: 
                cursor.close()

    def snooze_topic(self, topic_id, user_id, minutes=5):
        """Snoozes a topic's alarm by adding 'minutes' to its scheduled_datetime."""
        if not self.check_connection(): return False

        cursor = self.connection.cursor()
        try:
            # Add minutes to the current scheduled_datetime
            sql_update = """
            UPDATE topics t
            JOIN subjects s ON t.subject_id = s.subject_id
            SET t.scheduled_datetime = DATE_ADD(t.scheduled_datetime, INTERVAL %s MINUTE)
            WHERE t.topic_id = %s AND s.user_id = %s AND t.scheduled_datetime IS NOT NULL;
            """
            cursor.execute(sql_update, (minutes, topic_id, user_id))
            self.connection.commit()    
            return cursor.rowcount > 0
        except Error as e:
            print(f"❌ Error snoozing topic: {e}")
            return False
        finally:
            cursor.close()

    def fetch_chart_data(self, user_id):
        """
        Fetches data for the charts:
        1. Average difficulty per subject.
        2. Difficulty of individual topics.
        """
        if not self.check_connection(): return {'subjects': [], 'topics': []}

        cursor = self.connection.cursor(dictionary=True)
        
        chart_data = {
            'subjects': [],
            'topics': []
        }

        try:
            # 1. Average Difficulty per Subject
            query_subjects = """
                SELECT 
                    s.subject_name, 
                    AVG(t.difficulty_rating) as avg_difficulty
                FROM 
                    subjects s 
                JOIN 
                    topics t ON s.subject_id = t.subject_id 
                WHERE 
                    s.user_id = %s 
                GROUP BY 
                    s.subject_name
            """
            cursor.execute(query_subjects, (user_id,))
            # Convert Decimal to float if necessary
            subjects = cursor.fetchall()
            for s in subjects:
                if s['avg_difficulty']:
                    s['avg_difficulty'] = float(s['avg_difficulty'])
            chart_data['subjects'] = subjects

            # 2. Topic Difficulty
            query_topics = """
                SELECT 
                    t.topic_name, 
                    t.difficulty_rating, 
                    s.subject_name 
                FROM 
                    topics t 
                JOIN 
                    subjects s ON t.subject_id = s.subject_id 
                WHERE 
                    s.user_id = %s 
                ORDER BY 
                    t.difficulty_rating DESC
            """
            cursor.execute(query_topics, (user_id,))
            chart_data['topics'] = cursor.fetchall()

            return chart_data

        except Error as e:
            print(f"❌ Error fetching chart data: {e}")
            return {'subjects': [], 'topics': []}
        finally:
            cursor.close()

def generate_schedule(db_manager, user_id, daily_limit_hours=8.0):
        """
        Generates a daily study schedule based on topic priority and a fixed time limit.
        """
        prioritized_topics = db_manager.fetch_prioritized_topics(user_id)
        
        schedule = []
        total_time_scheduled = 0.0
        
        for topic in prioritized_topics:
            # Note: Estimated study time is assumed to be float already when retrieved by fetch_prioritized_topics
            topic_time = float(topic['estimated_study_time_hrs'])
            
            # Extract filename from path if it exists
            filename = None
            if topic.get('study_material_path'):
                filename = os.path.basename(topic['study_material_path'])

            # Format scheduled_datetime to 12-hour format
            scheduled_display = None
            raw_dt = topic.get('scheduled_datetime')
            if raw_dt:
                if isinstance(raw_dt, (datetime.datetime, datetime.date)):
                    scheduled_display = raw_dt.strftime("%Y-%m-%d %I:%M %p")
                else:
                    scheduled_display = str(raw_dt)

            if total_time_scheduled + topic_time <= daily_limit_hours:
                schedule.append({
                    'subject': topic['subject_name'],
                    'topic': topic['topic_name'],
                    'time_needed': topic_time,
                    'difficulty': topic['difficulty_rating'],
                    'topic_id': topic['topic_id'], # Include ID for potential future actions
                    'filename': filename,
                    'scheduled_datetime': scheduled_display
                })
                total_time_scheduled += topic_time
            else:
                # Schedule the partial remaining time if the topic is too big
                remaining_time = daily_limit_hours - total_time_scheduled
                if remaining_time >= 0.1: # Schedule if remaining time is at least 6 minutes
                    schedule.append({
                        'subject': topic['subject_name'],
                        'topic': topic['topic_name'] + f" (Partial Session - {int(remaining_time*60)} mins)",
                        'time_needed': remaining_time,
                        'difficulty': topic['difficulty_rating'],
                        'topic_id': topic['topic_id'], # Include ID for potential future actions
                        'filename': filename,
                        'scheduled_datetime': scheduled_display
                    })
                    total_time_scheduled += remaining_time
                # Stop scheduling once the limit is hit
                break 
                
        return schedule, total_time_scheduled




# --- 3. Flask Application Setup and Routes ---

db_manager = study_buddyDB(DB_CONFIG)


@app.route('/')
def index():
    """Default route redirects to dashboard if logged in, otherwise login."""
    global LOGGED_IN_USER_ID
    if LOGGED_IN_USER_ID:
        return redirect(url_for('dashboard')) # Redirect to the new dashboard
    # Show the public landing page for visitors who are not logged in
    return render_template('landingpage.html')


@app.route('/landingpage')
def landingpage():
    """Explicit landing page route."""
    global LOGGED_IN_USER_ID
    if LOGGED_IN_USER_ID:
        return redirect(url_for('dashboard'))
    return render_template('landingpage.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login."""
    global LOGGED_IN_USER_ID
    # Redirect already logged-in users away from the login page
    if LOGGED_IN_USER_ID: 
        return redirect(url_for('dashboard')) # Redirect to the new dashboard
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user_id = db_manager.login_user(username, password)
        if user_id:
            LOGGED_IN_USER_ID = user_id
            return redirect(url_for('dashboard')) # Redirect to the new dashboard
        return render_template('login.html', message="Login failed: Invalid credentials.")
            
    return render_template('login.html', message='')


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handles user registration."""
    global LOGGED_IN_USER_ID
    if LOGGED_IN_USER_ID:
        return redirect(url_for('dashboard')) # Redirect to the new dashboard
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        
        if not all([username, password, email]):
            return render_template('register.html', message="Please fill out all fields.", success=False)

        success, db_message = db_manager.register_user(username, password, email)
        
        return render_template('register.html', message=db_message, success=success)
            
    return render_template('register.html', message='')


# --- LOGOUT ROUTE ---
@app.route('/logout')
def logout():
    """Logs out the user by clearing the global user ID."""
    global LOGGED_IN_USER_ID
    LOGGED_IN_USER_ID = None
    return redirect(url_for('login'))


# --- Google OAuth Routes (Combined Login/Registration - Used on Register Page) ---

@app.route('/login/google')
def login_google():
    """Starts the Google OAuth flow for COMBINED Login/Registration (used on Register page)."""
    redirect_uri = url_for('authorize', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/google/auth')
def authorize():
    """Handles the callback from Google for COMBINED Login/Registration."""
    global LOGGED_IN_USER_ID
    
    try:
        token = oauth.google.authorize_access_token()
        resp = oauth.google.get('userinfo', token=token)
        resp.raise_for_status()
        user_info = resp.json()
    except Exception as e:
        print(f"OAuth/User Info Error: {e}")
        return redirect(url_for('register', message='Google authentication failed.', success=False))

    # Log user in or register them in the database (Upsert behavior)
    google_id = user_info.get('id')
    email = user_info.get('email')
    name = user_info.get('name')
    
    user_id = db_manager.login_or_register_google(google_id, email, name)

    if user_id:
        LOGGED_IN_USER_ID = user_id
        return redirect(url_for('dashboard')) # Redirect to the new dashboard
    else:
        return redirect(url_for('register', message='Database error during Google sign-in/registration.', success=False))

# --- NEW Google OAuth Routes (STRICT Login Only - Used on Login Page) ---

@app.route('/login/google/strict')
def login_google_strict_flow():
    """Starts the Google OAuth flow for STRICT Login Only (used on Login page)."""
    # The callback URI is directed to the new strict authorization handler
    redirect_uri = url_for('authorize_strict', _external=True) 
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/google/auth/strict')
def authorize_strict():
    """
    Handles the callback from Google for STRICT Login Only. 
    It will only authenticate if the user exists.
    """
    global LOGGED_IN_USER_ID
    
    try:
        token = oauth.google.authorize_access_token()
        resp = oauth.google.get('userinfo', token=token)
        resp.raise_for_status()
        user_info = resp.json()
    except Exception as e:
        print(f"OAuth/User Info Error: {e}")
        return redirect(url_for('login', message='Google authentication failed.'))

    google_id = user_info.get('id')
    email = user_info.get('email')
    
    # Attempt strict login - this function returns None if the user is not found
    user_id = db_manager.login_google_strict(google_id, email)

    if user_id:
        LOGGED_IN_USER_ID = user_id
        return redirect(url_for('dashboard')) # Login successful
    else:
        # Redirect to login page with an error message indicating the user needs to register
        return redirect(url_for('login', message='Login failed: Google account not found in database. Please register first.'))


# --- UPDATED ROUTE: Dashboard Home (Functional Requirement 5) ---
@app.route('/dashboard')
def dashboard():
    """Renders the main dashboard with summary statistics and the username."""
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login'))

    # Fetch summary data
    summary_data = db_manager.fetch_dashboard_summary(LOGGED_IN_USER_ID)
    
    # NEW: Fetch the username to display
    username = db_manager.get_username_by_id(LOGGED_IN_USER_ID)
    
    return render_template(
        'dashboard.html',
        summary=summary_data,
        username=username
    )

@app.route('/subject')
def subject():
    global LOGGED_IN_USER_ID 
    global db_manager 
    
    if not LOGGED_IN_USER_ID:
        flash("You must be logged in to view subjects.", "error")
        return redirect(url_for('login'))
        
    # 🎯 ACTION: Calls the fixed, fresh-connection method
    subjects_data = db_manager.fetch_all_subjects_and_topics(LOGGED_IN_USER_ID)
    
    return render_template('manage_subjects.html', subjects_data=subjects_data)

# --- study_buddy3 Functional Routes (Functional Requirement 2, 3, 4, 6) ---

@app.route('/add_subject_topic', methods=['GET', 'POST'])
def add_subject_topic():
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login'))

    subject_message = ''
    topic_message = ''
    file_path = None

    if request.method == 'POST':
        if 'add_subject' in request.form:
            # --- ACTION 1: ADD SUBJECT LOGIC ---
            subject_name = request.form.get('subject_name')
            _, subject_message = db_manager.add_subject(LOGGED_IN_USER_ID, subject_name)
            
        elif 'add_topic' in request.form:
            # --- ACTION 2: ADD TOPIC LOGIC (WITH FILE SUPPORT) ---
            
            # 1. Retrieve raw data
            subject_id_str = request.form.get('subject_id')
            topic_name = request.form.get('topic_name')
            study_time_str = request.form.get('study_time_hrs')
            difficulty_str = request.form.get('difficulty')
            scheduled_date_str = request.form.get('scheduled_date')
            scheduled_time_str = request.form.get('scheduled_time')
            scheduled_datetime = None
            
            if scheduled_date_str and scheduled_time_str:
                scheduled_datetime = f"{scheduled_date_str} {scheduled_time_str}:00" 

            # --- FILE UPLOAD HANDLING START ---
            file = request.files.get('study_material')
            file_upload_error = None
            
            if file and file.filename != '':
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    
                    # Create a unique filename for saving
                    unique_filename = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                    
                    # Construct the full path (this is the value stored in file_path)
                    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                    
                    # Save the file
                    try:
                        file.save(file_path)
                        print(f"File saved to: {file_path}")
                    except Exception as e:
                        print(f"Error saving file: {e}")
                        file_upload_error = f"Error saving file: {e}"
                        file_path = None

                else:
                    file_upload_error = f"Error: File type not allowed. Please use {', '.join(ALLOWED_EXTENSIONS)}."
                    file_path = None # Ensure file_path is None if the file is invalid
            # --- FILE UPLOAD HANDLING END ---

            # 2. Convert numeric data types safely
            try:
                subject_id = int(subject_id_str)
                study_time = float(study_time_str) if study_time_str else None
                difficulty = int(difficulty_str) if difficulty_str else None

                if study_time is None or difficulty is None or not subject_id_str:
                    topic_message = "Error: Subject, study time, and difficulty are required."
                else:
                    # 3. Call DB method with converted types, INCLUDING the file_path
                    success, db_msg = db_manager.add_topic(
                        subject_id, topic_name, study_time, difficulty, scheduled_datetime, file_path
                    )
                    
                    if file_upload_error:
                        topic_message = f"{db_msg} (Warning: {file_upload_error})"
                    else:
                        topic_message = db_msg

            except ValueError:
                topic_message = "Error: Please enter valid numbers for Study Time and Difficulty."
                
    # --- ACTION 3: RE-FETCH SUBJECTS ---
    subjects = db_manager.get_subjects_by_user(LOGGED_IN_USER_ID)
    
    return render_template(
        'add_subject_topic.html', 
        subject_message=subject_message, 
        topic_message=topic_message,
        subjects=subjects 
    )


# --- NEW ROUTE: Topic Completion (Functional Requirement 6) ---
@app.route('/mark_topic_complete_route/<int:topic_id>', methods=['POST'])
def mark_topic_complete_route(topic_id): 
    
    global LOGGED_IN_USER_ID
    global db_manager
    
    if not LOGGED_IN_USER_ID:
        flash("You must be logged in to complete a topic.", "error")
        return redirect(url_for('login'))
    
    success = db_manager.mark_topic_complete_db(topic_id, LOGGED_IN_USER_ID)

    if success:
        # 🎯 Action: The success message and redirect are correct.
        flash(f"Topic successfully marked as complete!", "success")
    else:
        print(f"DEBUG: DB update failed. Rowcount was 0.")
        flash("Failed to mark topic complete. Topic may not exist or belong to you.", "error")

    # Redirects back to the previous page (/subject) which will now run the fresh SELECT query
    return redirect(request.referrer or url_for('subject'))


@app.route('/subjects')
def view_subjects():
    """Fetches and displays all subjects and their topics for the logged-in user."""
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login')) 
    
    # Fetch all structured subject and topic data
    subjects_data = db_manager.fetch_all_subjects_and_topics(LOGGED_IN_USER_ID)

    return render_template(
        'subjects.html',
        subjects_data=subjects_data,
        user_id=LOGGED_IN_USER_ID 
    )



@app.route('/schedule')
def schedule():
    """Handles viewing the generated study schedule and past schedules. Protected route."""
    global LOGGED_IN_USER_ID, db_manager # 👈 CRITICAL: Declare DB_manager as global if it's not local
    
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login')) 
    
    # 1. FIX: The generate_schedule function expects (db_manager, user_id, time)
    schedule, total_time = generate_schedule(db_manager, LOGGED_IN_USER_ID, daily_limit_hours=8.0)

    # Use python's datetime for the date display
    # 🚨 NOTE: Ensure `datetime` is imported at the top of your file: `import datetime`
    today = datetime.date.today().strftime("%A, %B %d, %Y")
    
    # 2. NEW: Fetch historical completed topics
    historical_schedule = db_manager.fetch_historical_schedule(LOGGED_IN_USER_ID)

    return render_template(
        'schedule.html',
        today=today,
        schedule=schedule,
        total_time=total_time,
        historical_schedule=historical_schedule
    )

# --- New Route: Study Reminder Pop-up (Functional Requirement 8) ---
@app.route('/get_study_reminder')
def get_study_reminder():
    """
    Fetches the most difficult incomplete topic and returns a personalized message
    for use in a client-side pop-up.
    """
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        # Return a generic message if not logged in
        return "Please log in to receive personalized study reminders."

    # Fetch the most difficult incomplete topic
    most_difficult_topic = db_manager.fetch_most_difficult_incomplete_topic(LOGGED_IN_USER_ID)
    username = db_manager.get_username_by_id(LOGGED_IN_USER_ID)

    if most_difficult_topic:
        subject = most_difficult_topic['subject_name']
        topic = most_difficult_topic['topic_name']
        difficulty = most_difficult_topic['difficulty_rating']
        
        message = (
            f"🔔 **Hey {username}, time to tackle a challenge!** 🧠\n\n"
            f"Your most difficult pending topic is: **{topic}** (Difficulty: {difficulty}/5) "
            f"in the subject **{subject}**.\n\n"
            f"Start your focused study session now!"
        )
    else:
        # If all topics are completed
        message = (
            f"🎉 **Great job, {username}!** 🎉\n\n"
            "You have completed all your recorded topics. Time to add more subjects or relax!"
        )
        
    # We return the message as raw text (or JSON) to be used by JavaScript
    return message

from flask import jsonify # Ensure you import jsonify at the top

# --- New Route: Fetch Scheduled Alarms (For Client-Side JS) ---
@app.route('/fetch_scheduled_alarms')
def fetch_scheduled_alarms():
    """
    Returns a list of incomplete topics with a scheduled_datetime in JSON format.
    """
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return jsonify([])

    if not db_manager.check_connection():
        return jsonify([])
        
    query = """
        SELECT
            t.topic_id,
            t.topic_name,
            s.subject_name,
            t.scheduled_datetime
        FROM
            topics t
        JOIN
            subjects s ON t.subject_id = s.subject_id
        WHERE
            s.user_id = %s AND t.is_completed = FALSE AND t.scheduled_datetime IS NOT NULL
        ORDER BY
            t.scheduled_datetime ASC;
    """
    
    cursor = db_manager.connection.cursor(dictionary=True)
    try:
        cursor.execute(query, (LOGGED_IN_USER_ID,))
        results = cursor.fetchall()
        
        formatted_results = []
        for row in results:
            # Convert Python datetime object to an ISO string for reliable JS parsing
            if row['scheduled_datetime']:
                row['scheduled_datetime'] = row['scheduled_datetime'].isoformat()
            formatted_results.append(row)
            
        return jsonify(formatted_results)
    except Error as e:
        print(f"❌ Error fetching scheduled alarms: {e}")
        return jsonify([])
    finally:
        cursor.close()

@app.route('/snooze_alarm/<int:topic_id>', methods=['POST'])
def snooze_alarm(topic_id):
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
    success = db_manager.snooze_topic(topic_id, LOGGED_IN_USER_ID)
    return jsonify({'success': success})

@app.route('/real_time_clock')
def real_time_clock():
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login'))
    return render_template('real_time_clock.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/charts')
def charts():
    global LOGGED_IN_USER_ID
    if not LOGGED_IN_USER_ID:
        return redirect(url_for('login'))
    
    data = db_manager.fetch_chart_data(LOGGED_IN_USER_ID)
    username = db_manager.get_username_by_id(LOGGED_IN_USER_ID)
    
    return render_template('charts.html', data=data, username=username)

if __name__ == '__main__':
    # Add a safety check before starting
    if db_manager.connection is None:
        print("\n🚨 WARNING: Cannot start app, database connection failed. Please check config/MySQL server.")
    else:
        app.run(debug=True)