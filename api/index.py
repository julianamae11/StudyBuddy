import os
import sys

# Add the parent directory to sys.path so we can import app.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# This is required for Vercel to find the Flask app instance
# Vercel looks for a variable named 'app' or 'application' in the entry file.
application = app