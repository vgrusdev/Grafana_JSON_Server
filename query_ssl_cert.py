import ssl
import socket
import os
import logging
from datetime import datetime
import time

from typing import Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum

import redis_cache_thread_safe as redis_cache

logger = logging.getLogger(__name__)

class QuerySSLCert:
    def __init__(self, db_config_manager):
        self.db_config_manager = db_config_manager

        # Cache storage
        redis_config = db_config_manager.get_database_config("redis", except_flg=False)
        self.redis_cache = redis_cache.RedisCache(redis_config)
        #self.redis_prefix_key = generate_alphanumeric_key(length=8)
        self.redis_prefix_key=os.getenv('CACHE_KEY_PREFIX', '')

        #self._cache             = {}  # Stores cached data: {cache_key: data}
        #self._timestamps        = {}  # Stores when data was cached: {cache_key: timestamp}
        #self._lock              = Lock()  # Thread lock for thread-safe cache operations
        #self._cache_ttl          = int(os.getenv('DB_CACHE_TTL', '30'), base=10)
        #self._access_count      = {}  # Track how often items are accessed
        #self.max_cache_size     = int(os.getenv('DB_CACHE_LENGTH', '30'), base=10)

    def get_ssl_certificate(self, conn):
        ''' conn is server connection params string:
            NAME:hostname:port
        '''

        logger.debug(f"execute_query_json - Conn: {conn}")

        conn_split = conn.split(sep=":", maxsplit=3)
        l = len(conn_split)
        if l < 2 :
            logger.warning(f"empty connection string")
            return {}

        conn_name = conn_split[0]
        hostname = conn_split[1]
        port=443
        if l > 2:
            try:
                port = int(conn_split[2])
            except ValueError as e:
                logger.error(f"port number {e}")
                return {}

        cache_key = '_'.join([self.redis_prefix_key, conn])
        results_new = self.redis_cache.get(cache_key,
            lambda: get_fresh_data(hostname, port=port), ttl = 30)

        if not results_new['success']:
            logger.debug(f"❌ Error: {results_new['error']}")
            return {}

        results_new['name'] = conn_name
        return results_new

#

def get_fresh_data (
    hostname: str,
    port: int = 443,
    timeout: int = 10,
    check_hostname: bool = True,
    allow_self_signed: bool = False
) -> Dict:
    """
    Retrieve SSL certificate information - return dictionary with results.
    
    Args:
        hostname: The server hostname or IP address
        port: The port number (default 443 for HTTPS)
        timeout: Connection timeout in seconds
        check_hostname: Whether to verify the hostname matches the certificate
        allow_self_signed: If True, accept self-signed certificates for checking
    
    Returns:
        Dictionary with keys: success, hostname, port, expiry_date, 
        days_left, is_expired, issuer, subject, error, duration_ms, is_self_signed
    
    """
    start_time = time.time()
    result = {
        'success': False,
        'hostname': hostname,
        'port': port,
        'issued_date': None,
        'expiry_date': None,
        'days_left': None,
        'is_expired': None,
        'is_self_signed': False,
        'issuer': None,
        'subject': None,
        'error': None,
        'duration_ms': 0
    }
    # Validate port number
    if not isinstance(port, int) or port < 1 or port > 65535:
        result['error'] = f"Invalid port number: {port}"
        logger.error(result['error'])
        return result
    
    # Validate hostname
    if not hostname or not isinstance(hostname, str):
        result['error'] = f"Invalid hostname: {hostname}"
        logger.error(result['error'])
        return result
    
    sock = None
    
    try:
        # Create context based on whether we allow self-signed
        if allow_self_signed:
            # Don't verify certificate chain for self-signed
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # Accept any certificate
        else:
            context = ssl.create_default_context()
            if not check_hostname:
                context.check_hostname = False

        # Attempt to resolve hostname
        try:
            addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            if not addr_info:
                raise socket.gaierror(f"Could not resolve hostname: {hostname}")
        except socket.gaierror as e:
            result['error'] = f"DNS resolution failed for {hostname}: {str(e)}"
            logger.error(result['error'])
            result['duration_ms'] = (time.time() - start_time) * 1000
            return result
        
        # Create socket and set timeout
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        # Attempt to connect
        try:
            sock.connect((hostname, port))
        except socket.timeout:
            result['error'] = f"Connection timeout after {timeout} seconds to {hostname}:{port}"
            logger.error(result['error'])
            result['duration_ms'] = (time.time() - start_time) * 1000
            return result

        except socket.error as e:
            result['error'] = f"Socket connection error: {str(e)}"
            logger.error(result['error'])
            result['duration_ms'] = (time.time() - start_time) * 1000
            return result
        
        # Wrap socket with SSL/TLS
        logger.debug("run context.wrap_socket")
        try:
            with context.wrap_socket(sock, server_hostname=hostname if check_hostname and not allow_self_signed  else None) as ssock:
                cert_bin = ssock.getpeercert(binary_form=False)
                if not cert_bin:
                    result['error'] = f"No certificate received from server"
                    logger.error(result['error'])
                    result['duration_ms'] = (time.time() - start_time) * 1000
                    return result

                # Check if certificate is self-signed
                issuer = dict(cert_bin.get('issuer', []))
                subject = dict(cert_bin.get('subject', []))
                is_self_signed = issuer.get('commonName', [''])[0] == subject.get('commonName', [''])[0]
                result['is_self_signed'] = is_self_signed
                logger.debug(f"issuer: {issuer}")
                logger.debug(f"subject: {subject}")

                not_before = cert_bin.get('notBefore')
                if not_before:
                    try:
                        issued_date = datetime.strptime(not_before, '%b %d %H:%M:%S %Y %Z')
                        result['issued_date'] = issued_date
                    except ValueError as e:
                        logger.warning(f"Failed to parse certificate issued date: {str(e)}")
                else:
                    logger.warning("Certificate missing issue date")

                not_after = cert_bin.get('notAfter')
                if not_after:
                    try:
                        expiry_date = datetime.strptime(not_after, '%b %d %H:%M:%S %Y %Z')
                        result['expiry_date'] = expiry_date
                        # Calculate days until expiry
                        now = datetime.now()
                        days_until = (expiry_date - now).total_seconds() / 86400
                        result['days_left'] = round(days_until, 2)
                        result['is_expired'] = days_until < 0
                    except ValueError as e:
                        logger.warning(f"Failed to parse certificate expire date: {str(e)}")
                else:
                    logger.error("Certificate missing expiration date")
                
                # Extract issuer and subject
                issuer = cert_bin.get('issuer', [])
                subject = cert_bin.get('subject', [])
                result['issuer']  = _format_cert_name(issuer) if issuer else None
                result['subject'] = _format_cert_name(subject) if subject else None
                
                # Check hostname match
                if check_hostname:
                    logger.debug("checking hostname match")
                    try:
                        ssl.match_hostname(cert_bin, hostname)
                    except ssl.CertificateError as e:
                        result['error'] = f"Hostname mismatch: {str(e)}"
                        logger.error(result['error'])
                        result['duration_ms'] = (time.time() - start_time) * 1000
                        return result

                result['success'] = True
                result['duration_ms'] = (time.time() - start_time) * 1000
                logger.debug(f"Success return {result}")
                return result

        except ssl.SSLCertVerificationError as e:
            result['error'] = f"SSL certificate verification failed: {str(e)}"
            logger.error(result['error'])
            result['duration_ms'] = (time.time() - start_time) * 1000
            logger.debug(f"result is: {result}")
            return result

        except ssl.SSLError as e:
            result['error'] = f"SSL handshake failed: {str(e)}"
            logger.error(result['error'])
            result['duration_ms'] = (time.time() - start_time) * 1000
            return result
            
    except Exception as e:
        result['error'] = f"Unexpected error: {str(e)}"
        logger.error(result['error'])
        result['duration_ms'] = (time.time() - start_time) * 1000
        return result
        
    finally:
        if sock:
            sock.close()


def _format_cert_name(name_tuple):
    """
    Format certificate name tuple into a readable string.
    
    Args:
        name_tuple: Tuple of certificate name components
    
    Returns:
        Formatted string representation of the certificate name
    """
    if not name_tuple:
        return None
    
    parts = []
    for component in name_tuple:
        if isinstance(component, tuple):
            for item in component:
                if isinstance(item, tuple) and len(item) == 2:
                    parts.append(f"{item[0]}={item[1]}")
                elif isinstance(item, str):
                    parts.append(item)
        elif isinstance(component, str):
            parts.append(component)
    
    return ', '.join(parts) if parts else None
