#!/usr/bin/env python
import sys
import os
import subprocess
import time
import signal
import importlib.util

def check_module_exists(module_name):
    """Check if a module exists and can be imported"""
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False

def run_test_server():
    """Run the test server as a fallback"""
    print("Starting test server as fallback...")
    from test_server import run_server
    run_server()

def run_fastapi_app():
    """Attempt to run the FastAPI application"""
    print("Attempting to start FastAPI application...")
    
    # Check if uvicorn is installed
    if not check_module_exists('uvicorn'):
        print("Error: uvicorn is not installed")
        return False
    
    # Check if the app module exists
    if not os.path.exists('app/main.py'):
        print("Error: app/main.py not found")
        return False
    
    try:
        # Try to import the app to check for syntax errors
        sys.path.append(os.getcwd())
        from app.main import app
        print("Successfully imported app")
        
        # Start uvicorn with the app
        print("Starting uvicorn server...")
        process = subprocess.Popen(
            ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002", "--log-level", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Wait a bit to see if it starts successfully
        time.sleep(5)
        
        # Check if the process is still running
        if process.poll() is None:
            print("FastAPI application started successfully")
            
            # Forward output
            while process.poll() is None:
                stdout_line = process.stdout.readline()
                if stdout_line:
                    print(stdout_line.strip())
                stderr_line = process.stderr.readline()
                if stderr_line:
                    print(stderr_line.strip(), file=sys.stderr)
            
            return True
        else:
            stdout, stderr = process.communicate()
            print(f"Error starting FastAPI application: {stderr}")
            return False
    except Exception as e:
        print(f"Error running FastAPI application: {e}")
        return False

if __name__ == "__main__":
    # Try to run the FastAPI app first
    success = run_fastapi_app()
    
    # If FastAPI app fails, run the test server
    if not success:
        run_test_server()
