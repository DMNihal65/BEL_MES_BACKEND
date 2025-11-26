from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
import os

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Hello from BEL MES Backend!')
        print('Received request at path:', self.path)
    
    def log_message(self, format, *args):
        print(format % args)

def run_server():
    print('Starting server on port 8002...')
    print('Python version:', sys.version)
    print('Current directory:', os.getcwd())
    print('Directory contents:', os.listdir('.'))
    
    try:
        server = HTTPServer(('0.0.0.0', 8002), Handler)
        print('Server started on port 8002')
        server.serve_forever()
    except Exception as e:
        print(f'Error starting server: {e}')

if __name__ == '__main__':
    run_server()
