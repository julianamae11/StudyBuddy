# api/index.py
from flask import Flask

# Assuming your main app object is called 'app' in 'main.py'
# If your app is called 'application' in 'app.py', adjust the import.
from main import app

# This is the Vercel handler function
# It takes the request and returns the response from your Flask app
@app.route("/")
def home():
    return "Hello from Vercel Serverless Flask!" 

# NOTE: You might need a more complex setup using a wrapper library 
# like 'flask-vercel' for production-ready deployment.