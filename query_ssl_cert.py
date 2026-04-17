import ssl
import socket
import os
import logging
from datetime import datetime, timezone
import time

from typing import Optional, Dict, List, Tuple
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import ExtensionOID, NameOID
#from cryptography.hazmat.primitives import hashes
#import base64

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

    def get_ssl_certificate(self, conn, ttl = 30):
        ''' conn is server connection params string:
            NAME:hostname[:port[:SNI]]
            if SNI == 'hostname':
                SNI = hostname
            elif SNI == 'None':
                SNI = None
        '''
        logger.debug(f"execute_query_json - Conn: {conn}, ttl: {ttl}")

        conn_split = conn.split(sep=":", maxsplit=4)
        l = len(conn_split)
        if l < 2 :
            logger.warning(f"empty connection string")
            return {}

        conn_name = conn_split[0]
        hostname = conn_split[1]
        port = 443
        SNI = None
        if l > 2:
            try:
                port = int(conn_split[2])
            except ValueError as e:
                logger.error(f"port number {e}")
                return {}
        if l > 3:
            SNI = conn_split[3]
            if SNI == 'hostname':
                SNI = hostname
            elif SNI == 'None':
                SNI = None

        cache_key = '_'.join([self.redis_prefix_key, conn])
        results_new = self.redis_cache.get(cache_key,
            lambda: get_fresh_data(hostname, port=port, SNI=SNI), ttl = ttl)

        if not results_new['success']:
            logger.debug(f"❌ Error: {results_new['error']}")
            return {}

        results_new['name'] = conn_name
        #logger.debug(f"{conn}: success, duration {results_new['elapsed_ms']}ms")
        return results_new

#

def get_fresh_data (
    hostname: str,
    port: int = 443,
    SNI: str = None,
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
        'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
        'success': False,
        'hostname': hostname,
        'port': port,
        'issued_date': 0,
        'expiry_date': 0,
        'days_left': 0.0,
        'ms_left': 0,
        'is_expired': False,
        'certificate_type': None,
        'is_self_signed': False,
        'issuer': None,
        'subject': None,
        'signature_algorithm': None,
        'serial_number': None,
        'version': None,
        'error': None,
        'elapsed_ms': 0
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

        with context.wrap_socket(sock, server_hostname=SNI) as ssock:
            # Get certificate in DER format (binary)
            cert_bin = ssock.getpeercert(binary_form=True)

            if not cert_bin:
                result['error'] = f"No certificate received from server"
                logger.error(result['error'])
                result['elapsed_ms'] = (time.time() - start_time) * 1000
                return result

            result.update(_process_cert_bin(cert_bin))
            logger.debug(f"{hostname}:{port} type: {type_analysis['certificate_type']}, expire: {expiry_date.isoformat()}, elapsed: {round((time.time() - start_time) * 1000, 2)}")

    except ssl.SSLError as e:
        # Some servers close connection immediately - this is OK
        if 'SSL alert' in str(e) or 'unrecognized name' in str(e).lower():
            # Try to get certificate anyway (might still have it)
            logger.debug(f"server closed connection trying to process a minimum info...")
            if sock and hasattr(sock, '_sslobj'):
                try:
                    der_cert = sock._sslobj.getpeercert(binary_form=True)
                    if der_cert:
                        result.update(_process_cert_bin(der_cert))
                        logger.debug(f"{hostname}:{port} type: {type_analysis['certificate_type']}, expire: {expiry_date.isoformat()}, elapsed: {round((time.time() - start_time) * 1000, 2)}")
                except:
                    pass
        result['error'] = str(e)

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
        result['elapsed_ms'] = round((time.time() - start_time) * 1000, 2)
    
    return result

def _process_cert_bin(cert_bin: bytes) -> Dict[str, any]:

    if not cert_bin:
        return {}

    # Parse DER certificate using cryptography library
    cert = x509.load_der_x509_certificate(cert_bin, default_backend())
            
    # Extract certificate information
    expiry_date = cert.not_valid_after_utc
    not_before = cert.not_valid_before_utc
    now = datetime.now(timezone.utc)
    days_left = (expiry_date - now).total_seconds() / 86400
    ms_left = (expiry_date - now).total_seconds() * 1000
            
    # Extract certificate info
    subject_str = x509.Name(cert.subject).rfc4514_string({NameOID.EMAIL_ADDRESS: "E"})
    issuer_str = x509.Name(cert.issuer).rfc4514_string()

    # Analyze certificate type
    type_analysis = analyze_certificate_safe(cert_bin)

    return {
        'success': True,
        'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000)
        #'issued_date': not_before.isoformat(),
        #'expiry_date': expiry_date.isoformat(),
        'issued_date': int(not_before.timestamp() * 1000),
        'expiry_date': int(expiry_date.timestamp() * 1000),
        'days_left': round(days_left, 2),
        'ms_left': round(ms_left, 0),
        'is_expired': days_left < 0,
        'certificate_type': type_analysis['certificate_type'],
        'is_self_signed': type_analysis['is_self_signed'],
        'subject': subject_str,
        'issuer': issuer_str,
        'signature_algorithm': cert.signature_algorithm_oid._name,
        'serial_number': hex(cert.serial_number),
        'version': cert.version.value
    }


def analyze_certificate_safe(der_cert: bytes) -> Dict[str, any]:
    """
    Analyze certificate type with safe datetime handling.
    """
    cert = x509.load_der_x509_certificate(der_cert, default_backend())
    
    result = {
        'is_self_signed': False,
        'is_ca': False,
        'certificate_type': 'unknown',
        'reasons': []
    }
    
    # Compare subject and issuer
    subject = cert.subject
    issuer = cert.issuer
    
    if subject == issuer:
        result['is_self_signed'] = True
        result['reasons'].append("Subject matches issuer")
    
    # Check Basic Constraints
    try:
        basic_constraints = cert.extensions.get_extension_for_oid(
            ExtensionOID.BASIC_CONSTRAINTS
        )
        is_ca = basic_constraints.value.ca
        result['is_ca'] = is_ca
        
        if is_ca:
            result['reasons'].append("Basic constraints CA:TRUE")
        else:
            result['reasons'].append("Basic constraints CA:FALSE")
    except x509.extensions.ExtensionNotFound:
        result['reasons'].append("No basic constraints extension")
    
    # Determine type
    if result['is_self_signed'] and result['is_ca']:
        result['certificate_type'] = 'root_ca_self_signed'
    elif result['is_self_signed'] and not result['is_ca']:
        result['certificate_type'] = 'self_signed_end_entity'
    elif not result['is_self_signed'] and result['is_ca']:
        result['certificate_type'] = 'intermediate_ca'
    else:
        result['certificate_type'] = 'ca_signed_end_entity'
    
    return result


def _certificate_name_to_string(name) -> str:
    """Convert certificate name to readable string."""
    parts = []
    for attribute in name:
        parts.append(f"{attribute.oid._name}={attribute.value}")
    return ', '.join(parts)
