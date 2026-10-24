import sys
import os

# Ensure application root directory is on Python path for Vercel Serverless
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Vercel entrypoint
app = app
