from flask import Flask, request, jsonify, g
from flask_cors import CORS
from datetime import datetime, timedelta
import time
import random
import string
import logging
import os
import ssl
from functools import wraps
import jwt
import base64
from werkzeug.exceptions import HTTPException
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', secrets.token_hex(32)),
    JWT_SECRET=os.environ.get('JWT_SECRET', secrets.token_hex(32)),
    API_KEYS=os.environ.get('API_KEYS', 'default-key-123').split(','),
    SSL_CERT=os.environ.get('SSL_CERT', 'cert.pem'),
    SSL_KEY=os.environ.get('SSL_KEY', 'key.pem'),
    DEBUG=os.environ.get('DEBUG', 'False').lower() == 'true',
    BASIC_AUTH_USERS=os.environ.get('BASIC_AUTH_USERS', 'grafana:grafana123,admin:admin123').split(',')
)

# CORS configuration
CORS(app, resources={
    r"/*": {
        "origins": os.environ.get('ALLOWED_ORIGINS', '*').split(','),
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-API-Key"]
    }
})

# Rate limiting storage (in production, use Redis)
from collections import defaultdict
from time import time as current_time
request_log = defaultdict(list)

# Basic Auth users database (username: password_hash)
basic_auth_users = {}

def setup_basic_auth():
    """Setup basic authentication users from configuration"""
    for user_config in app.config['BASIC_AUTH_USERS']:
        if ':' in user_config:
            username, password = user_config.split(':', 1)
            # In production, you should use hashed passwords
            # For simplicity, we're storing plain text in this example
            # basic_auth_users[username] = generate_password_hash(password)
            basic_auth_users[username] = password
            logger.info(f"Added Basic Auth user: {username}")

# Initialize Basic Auth users
setup_basic_auth()

def rate_limit(max_requests=100, window=60):
    """Simple rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not app.config['DEBUG']:  # Skip rate limiting in debug mode
                client_ip = request.remote_addr
                now = current_time()
                
                # Clean old requests
                request_log[client_ip] = [req_time for req_time in request_log[client_ip] if now - req_time < window]
                
                # Check rate limit
                if len(request_log[client_ip]) >= max_requests:
                    logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                    return jsonify({
                        "error": "Rate limit exceeded",
                        "message": f"Maximum {max_requests} requests per {window} seconds"
                    }), 429
                
                request_log[client_ip].append(now)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def authenticate_basic_auth():
    """Validate Basic Authentication credentials"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Basic '):
        return None
    
    try:
        # Decode Base64 credentials
        encoded_credentials = auth_header[6:]
        decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
        username, password = decoded_credentials.split(':', 1)
        
        # Check credentials
        if username in basic_auth_users:
            # For hashed passwords: check_password_hash(basic_auth_users[username], password)
            if basic_auth_users[username] == password:
                return username
    except Exception as e:
        logger.warning(f"Basic Auth decoding error: {str(e)}")
    
    return None

def authenticate(f):
    """Authentication decorator - supports Basic Auth, API Key, and JWT"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip authentication for health check and login
        if request.endpoint in ['home', 'login']:
            return f(*args, **kwargs)
        
        # Try Basic Authentication first
        basic_auth_user = authenticate_basic_auth()
        if basic_auth_user:
            g.user = f"basic_auth_{basic_auth_user}"
            g.auth_method = "basic"
            return f(*args, **kwargs)
        
        # Check API Key
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key in app.config['API_KEYS']:
            g.user = f"api_key_{api_key[-6:]}"
            g.auth_method = "api_key"
            return f(*args, **kwargs)
        
        # Check JWT Token
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header[7:]
            try:
                payload = jwt.decode(token, app.config['JWT_SECRET'], algorithms=['HS256'])
                g.user = payload.get('sub', 'unknown')
                g.auth_method = "jwt"
                return f(*args, **kwargs)
            except jwt.ExpiredSignatureError:
                logger.warning("JWT token expired")
                return jsonify({"error": "Token expired"}), 401
            except jwt.InvalidTokenError:
                logger.warning("Invalid JWT token")
                return jsonify({"error": "Invalid token"}), 401
        
        logger.warning("Authentication failed - no valid credentials provided")
        return jsonify({
            "error": "Authentication required",
            "message": "Provide Basic Auth, X-API-Key header, or Bearer token",
            "supported_methods": [
                "Basic Authentication",
                "API Key (X-API-Key header)",
                "JWT Bearer Token"
            ]
        }), 401
    return decorated_function


def handle_errors(f):
    """Global error handling decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Unhandled error in {f.__name__}: {str(e)}", exc_info=True)
            return jsonify({
                "error": "Internal server error",
                "message": "An unexpected error occurred"
            }), 500
    return decorated_function

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(error):
    # Pass through HTTP errors
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), error.code
    
    # Log unexpected errors
    logger.error(f"Unhandled exception: {str(error)}", exc_info=True)
    return jsonify({"error": "Internal server error"}), 500

# Request logging
@app.before_request
def log_request():
    if request.endpoint != 'home':  # Skip logging for health checks
        logger.info(f"{request.method} {request.path} - IP: {request.remote_addr}")

@app.after_request
def log_response(response):
    if request.endpoint != 'home':
        logger.info(f"{request.method} {request.path} - Status: {response.status_code}")
    return response

# ----------------------------

@app.route('/')
@authenticate
@rate_limit(max_requests=60, window=60)
def home():
    """
    Health check endpoint - no authentication required
    GET with 200 status code response.
    Used for "Test connection" on the datasource config page.
    """
    return jsonify({
        "status": "ok", 
        "message": "Grafana JSON Datasource Server with Auth & HTTPS",
        "timestamp": int(time.time()),
        "version": "1.0.0_vg",
        "authentication_methods": [
            "Basic Authentication",
            "API Key (X-API-Key header)",
            "JWT Bearer Token"
        ]
    })

# ---------------------------

@app.route('/auth/login', methods=['POST'])
@handle_errors
def login():
    """Generate JWT token for authentication"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    # Validate credentials against basic auth users
    if username in basic_auth_users and basic_auth_users[username] == password:
        token = jwt.encode({
            'sub': username,
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(hours=24)
        }, app.config['JWT_SECRET'], algorithm='HS256')
        
        logger.info(f"Successful JWT login for user: {username}")
        return jsonify({
            "token": token, 
            "user": username,
            "expires_in": "24 hours"
        })
    
    logger.warning(f"Failed login attempt for user: {username}")
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/auth/users', methods=['GET'])
@authenticate
@handle_errors
def list_users():
    """List available Basic Auth users (admin only)"""
    # Simple admin check - in production, implement proper role-based access
    if not (g.user.startswith('basic_auth_admin') or g.user.startswith('api_key_admin')):
        return jsonify({"error": "Insufficient permissions"}), 403
    
    users_list = [username for username in basic_auth_users.keys()]
    return jsonify({
        "users": users_list,
        "total": len(users_list)
    })

def generate_ssl_context():
    """Generate SSL context for HTTPS"""
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        context.load_cert_chain(app.config['SSL_CERT'], app.config['SSL_KEY'])
        return context
    except Exception as e:
        logger.warning(f"SSL certificate not found: {str(e)}. Using HTTP.")
        return None

def parse_prometheus_param(param_value):
    """
    Parse Prometheus parameter that might be string or list
    Handles: '[value]', '[val1 val2]', 'value', ['value']
    """
    if param_value is None:
        return None
        
    # If it's already a list, return it
    if isinstance(param_value, list):
        return param_value
    
    # If it's a string
    if isinstance(param_value, str):
        param_value = param_value.strip()
        
        # Check if it's in '[values]' format
        if param_value.startswith('[') and param_value.endswith(']'):
            content = param_value[1:-1].strip()
            
            # Empty brackets
            if not content:
                return []
            
            # Check if content looks like space-separated
            if ' ' in content and ',' not in content:
                # Split by spaces (handle multiple spaces)
                import re
                return re.split(r'\s+', content)
            else:
                # Might be JSON-like with commas
                try:
                    import json
                    # Try to parse as JSON
                    return json.loads(param_value)
                except:
                    # Fall back to comma splitting
                    items = [item.strip().strip('"\'') for item in content.split(',')]
                    return [item for item in items if item]
        else:
            # Single string value
            return [param_value]
    
    # For other types, wrap in list
    return [param_value] if param_value is not None else []

# Test with Prometheus-like inputs
#prometheus_tests = [
#    '[db1 db2]',      # Your specific case
#    'db1',            # Single value
#    ['db1', 'db2'],   # Already a list
#    '["db1","db2"]',  # JSON format
#    '[db1]',          # Single in brackets
#    '[]',             # Empty
#    '[db1, db2]',     # Comma separated in brackets
#]
#
#print("\nPrometheus parameter parsing:")
#for test in prometheus_tests:
#    result = parse_prometheus_param(test)
#    print(f"  Input: {repr(test)}")
#    print(f"  Output: {result}")
#    print(f"  Type: {type(result)}")
#    print()

# Generate alphanumeric key
def generate_alphanumeric_key(length=32):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))
