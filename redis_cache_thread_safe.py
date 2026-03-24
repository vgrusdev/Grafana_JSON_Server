import redis
import json
import os
import logging
#import pickle
#from datetime import timedelta
import time
import uuid
import threading
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

class RedisCache:
    def __init__(self, redis_config, default_ttl=30, lock_timeout=30):

        self.redis_config = redis_config  or {
            'host': 'localhost',
            'port': 6379,
            'db': 0,
            'password': None,
            'max_connections':10
        }
        # Use connection pooling for better performance
        redis_pool = redis.ConnectionPool(**self.redis_config)
        #self.redis_client = redis.Redis(**self.redis_config)
        self.redis_client = redis.Redis(connection_pool=redis_pool)

        self.redis_ttl    = int(os.getenv('DB_CACHE_TTL', default_ttl), base=10)
        self.lock_timeout = lock_timeout
        self.local_locks = threading.local()  # Thread-local storage

        # Test Redis connection
        try:
            self.redis_client.ping()
            logger.info("Redis connection successful")
        except redis.ConnectionError:
            logger.error("Cannot connect to Redis")
    
    def get(self, cache_key, query_func, ttl=-1):
        """Get data from Redis cache"""

        if ttl <= 0:
            ttl = self.redis_ttl

        # Try to get from Redis
        try:
            cached_data = self.redis_client.get(cache_key)
            if cached_data:
                logger.debug(f"Redis cache HIT: key={cache_key}")
                return json.loads(cached_data)
        except Exception as e:
            logger.warning(f"Redis get failed: {e}")


        # Cache miss - execute query
        # 2. Try to acquire distributed lock
        lock_key = f"{cache_key}:lock"
        lock_token = str(uuid.uuid4())
        
        try:
            # Try to acquire lock with SETNX (SET if Not eXists)
            acquired = self.redis_client.setnx(lock_key, lock_token)
            if acquired:
                # Set lock expiration to prevent deadlock
                self.redis_client.expire(lock_key, self.lock_timeout)
                
                # Cache miss - we got the lock, compute fresh data
                logger.info(f"Redis cache MISS (lock acquired): key={cache_key}, ttl={ttl}")
                fresh_data = query_func()
                
                # Store in Redis
                serialized_data = json.dumps(fresh_data, default=str)
                self.redis_client.setex(cache_key, ttl, serialized_data)
                
                # Release lock
                self.redis_client.delete(lock_key)
                
                return fresh_data
            else:
                # Someone else is computing, wait and retry
                logger.debug(f"Waiting for cache computation: key={cache_key}")
                return self._wait_for_cache(cache_key, lock_key, query_func)
                
        except Exception as e:
            logger.error(f"Cache lock error for {cache_key}: {e}")
            # Fallback: compute without caching
            return query_func()

    def _wait_for_cache(self, cache_key: str, lock_key: str, query_func: Callable) -> Any:
        """Wait for another thread to compute the cache"""
        max_wait_time = self.lock_timeout
        retry_interval = 0.1  # 100ms
        waited = 0
        
        while waited < max_wait_time:
            time.sleep(retry_interval)
            waited += retry_interval
            
            # Check if lock still exists
            if not self.redis_client.exists(lock_key):
                # Lock released, check if cache is populated
                try:
                    cached_data = self.redis_client.get(cache_key)
                    if cached_data:
                        logger.debug(f"Cache populated after wait: key={cache_key}")
                        return json.loads(cached_data)
                except Exception:
                    pass
            
            # If we waited too long, fallback to computing
            if waited >= max_wait_time:
                logger.warning(f"Cache wait timeout, computing fresh: key={cache_key}")
                return query_func()
        
        # Shouldn't reach here, but as fallback
        return query_func()
