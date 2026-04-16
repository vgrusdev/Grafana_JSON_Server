import ssl
import socket
import os
import logging
from datetime import datetime
import time

from typing import Optional, Dict, List
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
import base64

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
    timeout: int = 10
) -> Dict:
    """
    Retrieve SSL certificate information - return dictionary with results.
    Check certificate using DER binary format - works even with CERT_NONE.
    This is the most reliable method for self-signed certificates.
    
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
        'common_name': None,
        'serial_number': None,
        'signature_algorithm': None,
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
        # Create context with CERT_NONE (this works and gives us DER)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Accept any certificate

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        sock.connect((hostname, port))
        
        # Wrap socket with SSL/TLS
        logger.debug("run context.wrap_socket")
        with context.wrap_socket(sock, server_hostname=hostname) as ssock:
            # Get certificate in DER format (binary)
            cert_bin = ssock.getpeercert(binary_form=True)
            if not cert_bin:
                result['error'] = f"No certificate received from server"
                logger.error(result['error'])
                result['duration_ms'] = (time.time() - start_time) * 1000
                return result

            # Parse DER certificate using cryptography library
            cert = x509.load_der_x509_certificate(cert_bin, default_backend())
            
            # Extract certificate information
            expiry_date = cert.not_valid_after
            not_before = cert.not_valid_before
            now = datetime.now(expiry_date.tzinfo) if expiry_date.tzinfo else datetime.now()
            days_left = (expiry_date - now).total_seconds() / 86400
            
            # Extract subject and issuer
            subject = cert.subject
            issuer = cert.issuer
            
            # Check if self-signed (subject == issuer)
            is_self_signed = subject == issuer
            
            # Get common name
            common_name = None
            for attribute in subject:
                if attribute.oid._name == 'commonName':
                    common_name = attribute.value
                    break

            result.update({
                'success': True,
                'expiry_date': expiry_date.isoformat(),
                'days_left': round(days_left, 1),
                'is_expired': days_left < 0,
                'is_self_signed': is_self_signed,
                'issuer': str(issuer),
                'subject': str(subject),
                'common_name': common_name,
                'serial_number': hex(cert.serial_number),
                'signature_algorithm': cert.signature_algorithm_oid._name,
                'not_before': not_before.isoformat(),
                'version': cert.version.value
            })

    except socket.timeout:
        result['error'] = f"Connection timeout after {timeout}s"
        logger.error(result['error'])
    except socket.gaierror as e:
        result['error'] = f"DNS resolution failed: {str(e)}"
        logger.error(result['error'])
    except Exception as e:
        result['error'] = f"Error processing certificate: {str(e)}"
        logger.error(result['error'])
    finally:
        if sock:
            sock.close()
        result['duration_ms'] = round((time.time() - start_time) * 1000, 2)
        logger.debug(f"Success, duration {result['duration_ms']}ms")
    
    return result


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

def parse_certificate_name(name_tuple):
    """
    Robust parser that handles various nesting levels in certificate names.
    """
    if not name_tuple:
        return {}, "Unknown"
    
    result_dict = {}
    
    def extract_recursive(item):
        """Recursively extract key-value pairs"""
        if isinstance(item, tuple):
            if len(item) == 2 and isinstance(item[0], str) and isinstance(item[1], str):
                # Direct key-value pair
                result_dict[item[0]] = item[1]
            else:
                # Recursively process tuple elements
                for sub_item in item:
                    extract_recursive(sub_item)
        elif isinstance(item, list):
            for sub_item in item:
                extract_recursive(sub_item)
    
    extract_recursive(name_tuple)
    
    # Format as string
    formatted = ', '.join([f"{k}={v}" for k, v in result_dict.items()])
    
    return result_dict, formatted
