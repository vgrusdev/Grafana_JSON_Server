import json
import os
import logging
from typing import Dict, Any, List

import redis_cache_thread_safe as redis_cache
#from my_utilities import generate_alphanumeric_key

logger = logging.getLogger(__name__)

class DatabaseConfigManager:
    def __init__(self, config_file: str = None):
        if config_file is None:
            config_file = os.getenv('DB_CONFIG_FILE', 'database_config.json')
        self.config_file = config_file
        #self.configs = self._load_configs()

        # Cache storage
        redis_config = self.get_database_config("redis", except_flg=False)
        self.redis_cache = redis_cache.RedisCache(redis_config)
        #self.redis_prefix_key = generate_alphanumeric_key(length=8)
        self.redis_prefix_key=os.getenv('CACHE_KEY_PREFIX', '')
    #

    def _load_configs(self) -> Dict[str, Any]:
        """Load all database configurations from JSON file"""
        cache_key = '_'.join(["DBConfigManager", self.redis_prefix_key, self.config_file])

        configs_new = self.redis_cache.get(cache_key,
                lambda: self._get_fresh_configs(self.config_file), ttl = 5)
        return configs_new

    def _get_fresh_configs(self, config_file):
        """Load all database configurations from JSON file"""
        with open(config_file, 'r') as file:
            return json.load(file)
    
    def get_database_names(self) -> List[str]:
        """Get list of all database configuration names"""
        configs = self._load_configs()
        if 'databases' in configs:
            #return list(configs['databases'].keys())
            return [keys for keys in configs['databases'].keys() if keys != 'redis']
        return []
    
    def get_database_config(self, name: str, except_flg: bool = True) -> Dict[str, Any]:
        """Get specific database configuration by name"""
        if name == 'redis':
            configs = self._get_fresh_configs(self.config_file)
        else:
            configs = self._load_configs()
        if 'databases' in configs and name in configs['databases']:
            return configs['databases'][name]
        if except_flg :
            raise KeyError(f"Database configuration '{name}' not found")
        else:
            return None
    
    def get_all_databases(self) -> Dict[str, Any]:
        """Get all database configurations"""
        configs = self._load_configs()
        return configs.get('databases', {})
    
    def get_connection_string(self, name: str, dialect: str = 'postgresql') -> str:
        """Generate connection string for a database"""
        config = self.get_database_config(name)
        return (
            f"{dialect}://{config['username']}:{config['password']}"
            f"@{config['host']}:{config['port']}/{config['database']}"
        )
