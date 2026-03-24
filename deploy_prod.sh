#!/bin/bash

# Load environment variables
set -a
source .env

# generate random key for cache key prefix
CACHE_KEY_PREFIX="${CACHE_KEY_PREFIX:-$(cat /dev/urandom | LC_ALL=C tr -dc '0-9_A-Z_0-9_a-z_0-9' | head -c 8)}"
set +a

# Generate SSL certificates if they don't exist
if [ ! -f cert.pem ] || [ ! -f key.pem ]; then
    echo "Generating SSL certificates..."
    openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365 -subj "/CN=localhost"
fi

# Start the production server
echo "Starting Grafana JSON Datasource with HTTPS and Auth..."
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 4 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --certfile cert.pem \
    --keyfile key.pem \
    app:app
    