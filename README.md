# Grafana_JSON_Server
HTTP Server to serve Grafana JSON api

Key Features

Concurrent requests - Multiple workers handle simultaneous users
Process management - Automatic restart on failures
Security - Hardened against common attacks
Performance - Optimized for production workloads
Monitoring - Built-in logging and metrics
Scalability - Easy to scale horizontally
Basic Authentication Support: Username/password authentication
Multiple Auth Methods: Basic Auth, API Key, and JWT all supported
Flexible User Management: Configurable via environment variables
Backward Compatibility: Existing API Key and JWT methods still work
Enhanced Logging: Tracks which authentication method was used
Admin Endpoints: List users (with proper authorization)
Security: All methods work over HTTPS

Usage Examples

1. Get JWT Token

bash
curl -X POST https://localhost:5000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "grafana", "password": "grafana123"}'

2. Query with JWT Token

bash
curl -X POST https://localhost:5000/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{"targets": [{"target": "cpu_usage", "type": "timeseries"}]}'

3. Query with API Key

bash
curl -X POST https://localhost:5000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: grafana-key-123" \
  -d '{"targets": [{"target": "cpu_usage", "type": "timeseries"}]}'


Usage Examples with Basic Auth

1. Using Basic Authentication with curl

bash
# Encode credentials (grafana:grafana123)
echo -n "grafana:grafana123" | base64
# Returns: Z3JhZmFuYTpncmFmYW5hMTIz

# Make request with Basic Auth
curl -X POST https://localhost:5000/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic Z3JhZmFuYTpncmFmYW5hMTIz" \
  -d '{"targets": [{"target": "cpu_usage", "type": "timeseries"}]}'

2. Using Basic Authentication with curl (inline)

bash
curl -X POST https://localhost:5000/query \
  -u "grafana:grafana123" \
  -H "Content-Type: application/json" \
  -d '{"targets": [{"target": "cpu_usage", "type": "timeseries"}]}'

3. Using different authentication methods

bash
# Basic Auth
curl -u "admin:admin123" https://localhost:5000/search -X POST

# API Key
curl -H "X-API-Key: grafana-key-123" https://localhost:5000/search -X POST

# JWT Token (after login)
curl -H "Authorization: Bearer YOUR_JWT_TOKEN" https://localhost:5000/search -X POST

4. List users (admin only)

bash
curl -u "admin:admin123" https://localhost:5000/auth/users
Grafana Configuration with Basic Auth

In Grafana datasource configuration:

json
{
  "name": "JSON Datasource",
  "type": "simpod-json-datasource",
  "url": "https://your-server:5000",
  "access": "proxy",
  "basicAuth": true,
  "basicAuthUser": "grafana",
  "basicAuthPassword": "grafana123",
  "jsonData": {}
}
