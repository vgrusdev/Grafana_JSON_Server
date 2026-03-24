import json
import os
from typing import Dict, Any, List
import re
import logging
from pathlib import Path

import redis_cache_thread_safe as redis_cache
#from my_utilities import generate_alphanumeric_key

logger = logging.getLogger(__name__)


class SQLListManager:
    def __init__(self, db_config_manager, config_file: str = None):
        if config_file is None:
            config_file = os.getenv('DB_SQL_FILE', 'sql_list.json')
        #self.config_file = config_file
        self.config_file = Path(config_file).resolve()
        #self.configs = self._load_recursive(self.config_file, set())

        # Cache storage
        redis_config = db_config_manager.get_database_config("redis", except_flg=False)
        self.redis_cache = redis_cache.RedisCache(redis_config)
        #self.redis_prefix_key = generate_alphanumeric_key(length=8)
        self.redis_prefix_key=os.getenv('CACHE_KEY_PREFIX', '')
    #

    def _load_configs(self, config_file: Path) -> Dict[str, Any]:
        """Load from JSON file or cache"""
        cache_key = '_'.join(["SQLListManager", self.redis_prefix_key, str(config_file)])

        configs_new = self.redis_cache.get(cache_key,
                lambda: self._get_fresh_configs(config_file), ttl = 5)
        return configs_new

    def _get_fresh_configs(self, config_file: Path):
        """Load from JSON file or cache"""
        with open(config_file, 'r', encoding='utf-8') as file:
            return json.load(file)
    
    def get_sql_names(self) -> List[str]:
        """Get list of all sqls name"""
        #if 'sqls' in self.configs:
        #    return list(self.configs['sqls'].keys())
        configs = self._load_recursive(self.config_file, set())
        if 'sqls' in configs:
            return list(configs['sqls'].keys())
        return []

    def get_all_sqls(self) -> Dict[str, Any]:
        """Get all sqls"""
        #return self.configs.get('sqls', {})
        configs = self._load_recursive(self.config_file, set())
        return configs.get('sqls', {})

    def get_sql_old(self, name: str) -> Dict[str, Any]:
        """Get specific SQL and cursor call parameters by name"""
        #if 'sqls' in self.configs and name in self.configs['sqls']:
        #    return self.configs['sqls'][name]
        configs = self._load_recursive(self.config_file, set())
        if 'sqls' in configs and name in configs['sqls']:
            return configs['sqls'][name]
        raise KeyError(f"SQL configuration: '{name}' not found")

    def get_sql(self, name:str) -> Dict[str, Any]:
        """ Get specific SQL and cursor call parameters by name """
        sql_dict = self.get_sql_old(name)
        new_sql_dict = {
            'sql':'',
            'name':  sql_dict.get('name', ''),
            'param': sql_dict.get('param', []),
            'ttl': sql_dict.get('ttl', -1)
        }
        if "sql" in sql_dict:       # sql - is SQL request itself - List of strings
            query = ' '.join(s.strip() for s in sql_dict['sql'])    # strip and joint with single space character
            new_sql_dict['sql'] = query
            # return new_sql_dict
        elif "file" in sql_dict:      # file - is file name with text - SQL query
            filename = sql_dict['file']

            cache_key = '_'.join(["SQLListManager", self.redis_prefix_key, str(self.config_file), filename])
            query = self.redis_cache.get(cache_key,
                lambda: self._get_file(filename), ttl = 5)
            #query = self._get_file(filename)
            new_sql_dict['sql'] = query
            # return new_sql_dict
        else:
            logger.error(f"SQL configuration error for {name} SQL")
            raise KeyError(f"SQL configuration '{name}' not found")

        return new_sql_dict

    def _get_file(self, filename: str) -> str:
        """
        Convert SQL file with comments and multi-line statements 
        into a single-line SQL statement ready for execution.
    
        Args:
            sql_file_path: Path to SQL file
        
        Returns:
            Executable SQL statement
        """
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
    
        # Remove multi-line comments first (/* */)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
    
        # Remove single-line comments (-- and #)
        lines = content.split('\n')
        cleaned_lines = []
    
        for line in lines:
            # Remove everything after -- or #
            line = re.sub(r'--.*$', '', line)
            line = re.sub(r'#.*$', '', line)
            line = line.strip()
            if line:
                cleaned_lines.append(line)
    
        # Join all lines
        full_sql = ' '.join(cleaned_lines)

        return full_sql
 
    def _load_recursive(self, file_path: Path, visited: set) -> Dict[str, Any]:
        if file_path in visited:
            raise ValueError(f"Circular include detected: {file_path}")
        visited.add(file_path)
        
        data = self._load_configs(file_path)
        # with open(file_path, 'r', encoding='utf-8') as f:
        #     data = json.load(f)
        if 'sqls' in data:
            logger.debug(f"loaded {len(data['sqls'])} SQLs from {file_path}")
        else:
            logger.debug(f"no SQLs in loaded file {file_path}")

        # Process includes if present
        if "include" in data:
            includes = data.pop("include")
            if isinstance(includes, str):
                includes = [includes]
            
            merged_data = {
                'sqls':{},
                'include':{}
            }
            # First load all includes
            for include in includes:
                include_path = (file_path.parent / include).resolve()
                included = self._load_recursive(include_path, visited)
                #logger.debug(f"include_path: {include_path}, include_path.parent: {include_path.parent}")
                # Merge with priority to later files
                if 'sqls' in included:
                    for sqlname, sqldict in included['sqls'].items():
                        if 'file' in sqldict:
                            included['sqls'][sqlname]['file'] = str((include_path.parent / sqldict['file']).resolve())
                    merged_data['sqls'].update(included['sqls'])
                if 'include' in included:
                    merged_data['include'].update(included['include'])
            
            # Then merge with current data (current overrides includes)
            if 'sqls' in data:
                merged_data['sqls'].update(data['sqls'])
            if 'include' in data:
                    merged_data['include'].update(data['include'])
            data = merged_data
        
        visited.remove(file_path)
        return data
    
