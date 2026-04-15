import time as time_module
#import sys
import os
import json
import logging
#import argparse
from datetime import date, datetime, time, tzinfo, timezone
from typing import Dict, Any, List
from threading import Lock

#from db_config_manager import db_config_manager
import psycopg2                 # For PostgreSQL
import mysql.connector          # For MySQL
import sqlite3                  # For SQLite  
from hdbcli import dbapi        # For HANA

import redis_cache_thread_safe as redis_cache
from deduplicate_rows import *
#from my_utilities import generate_alphanumeric_key

logger = logging.getLogger(__name__)

class DBCallSQL:
    def __init__(self, db_config_manager, sql_list_manager):
        self.db_config_manager = db_config_manager
        self.sql_list_manager  = sql_list_manager
        self.last_scrape_success = 0

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
        

    def _get_db_connection(self, conn_name: str = None):
        """Get database connection based on configuration"""
        db_config = self.db_config_manager.get_database_config(conn_name)
        db_type = db_config.get('type', 'redis')
         
        if db_type == 'postgresql':
            host    = db_config['host']
            port    = db_config.get('port', 5432)
            database= db_config['database']
            user    = db_config['user']
            password= db_config['password']
            logger.debug(f"Connecting to {db_type}: Conn: {conn_name}, {user}@{host}:{port}/{database}")
            return psycopg2.connect(
                host=host, 
                port=port, 
                database=database, 
                user=user, 
                password=password
            )
        elif db_type == 'mysql':
            host    = db_config['host']
            port    = db_config.get('port', 3306)
            database= db_config['database']
            user    = db_config['user']
            password= db_config['password']
            logger.debug(f"Connecting to {db_type}: Conn: {conn_name}, {user}@{host}:{port}/{database}")
            return mysql.connector.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password
            )
        elif db_type == 'sqlite':
            database_path = db_config['database_path']
            logger.debug(f"Connecting to {db_type}: Conn: {conn_name}, {database_path}")
            return sqlite3.connect(database_path)
        elif db_type == 'hdb':
            host    = db_config['host']
            port    = db_config.get('port', 30015)
            user    = db_config['user']
            password= db_config['password']
            logger.debug(f"Connecting to {db_type}: Conn: {conn_name}, {user}@{host}:{port}")
            return dbapi.connect(
                address=host,
                port=port,
                user=user,
                password=password
            )
        else:
            raise ValueError(f"Unsupported database type: {db_type}")

    #def execute_query(self, conn_name: str, query: str, params: tuple = None) -> Dict[str, Any]:
    def execute_query(self, conn_name: str, sql_name: str) -> Dict[str, Any]:
        """
        Get data from cache or execute query if cache expired
        Execute SQL query and return results
        """

        #logger.debug(f"execute_query - Conn_name: {conn_name}, SQL name: {sql_name}")

        #db_config = self.db_config_manager.get_database_config(conn_name)
        sql_dict  = self.sql_list_manager.get_sql(sql_name)

        cache_key = '_'.join([self.redis_prefix_key, conn_name, sql_name])

        results_new = self.redis_cache.get(cache_key,
                lambda: self.get_fresh_data(conn_name, sql_name), ttl = sql_dict.get('ttl', -1))

        if len(results_new) == 0:
            return {}
        if len(results_new) == 1:
            columns = results_new[0]['columns']
        else:
            columns = list(results_new[1].keys())

        col_types   = {col_name:None for col_name in columns}

        results_list = []
        for row in results_new[1:]:
            results_row = []

            for col_name in columns:

                value_dict = row.get(col_name, {})

                # Handle common non-serializable types
                value_type = value_dict.get("type", "None")
                if value_type == 'time':
                    dt = value_dict.get("data", 0)
                    results_row.append(dt)
                    if not col_types[col_name] :
                        col_types[col_name] = "time"

                else:
                    dt = value_dict.get("data", None)
                    results_row.append(dt)
                    if not col_types[col_name] :
                        col_types[col_name] = value_type
            results_list.append(results_row)

        columns_target = [{"text":col_name, "type":col_types[col_name]} for col_name in columns]
        results = {"columns":columns_target, "rows":results_list, "type":"table", "target":sql_name}

        return results

    def execute_query_json(self, conn_name: str, sql_name: str, dedup_params: Optional[dict] = None) -> Dict[str, Any]:
        """
        Get data from cache or execute query if cache expired
        Execute SQL query and return results
        
        Response format to json_exporter:
        sample_result = {
                    '_conn_name'    : conn_name,
                    '_sql_title'    : sql_name,
                    '_sql_name'     : sql_dict.get('name', ''),
                    '_host'         : db_config['host'],
                    '_port'         : db_config['port'],
                    '_database'     : db_config.get('database',''),
                    '_elapsed'      : elapsed,
                    'results'       : [
                            { 
                                '_conn_name'    : conn_name,
                                '_sql_title'    : sql_name,
                                '_sql_name'     : sql_dict.get('name', ''),
                                '_host'         : db_config['host'],
                                '_port'         : db_config['port'],
                                '_database'     : db_config.get('database',''),
                                column_name1: value1, column_name2: value2 
                            },
                            ...
                    ]
        
        dedup_params = {
            'timestamp_col': None,
            'exclude_col':   None,
            'dedup_col':     None
        }
        """

        logger.debug(f"execute_query_json - Conn_name: {conn_name}, SQL name: {sql_name}")

        db_config = self.db_config_manager.get_database_config(conn_name)
        sql_dict  = self.sql_list_manager.get_sql(sql_name)

        cache_key = '_'.join([self.redis_prefix_key, conn_name, sql_name])

        results_new = self.redis_cache.get(cache_key,
                lambda: self.get_fresh_data(conn_name, sql_name), ttl = sql_dict.get('ttl', -1))

        # jsonExported does not process results if there is any empty array inside. I stops on it !
        if len(results_new) == 0:
            return {}

        base_data = {
            '_conn_name'    : conn_name,
            '_sql_title'    : sql_name,
            '_sql_name'     : sql_dict.get('name', ''),
            '_host'         : db_config['host'],
            '_port'         : db_config['port'],
            '_database'     : db_config.get('database','')
        }
        results = []
        elapsed = results_new[0]['elapsed']
        returned_data = base_data.copy()
        returned_data['_elapsed'] = elapsed
        #returned_data['results'] = results

        for row in results_new[1:]:
            #results_row = base_data.copy()
            results_row = {}
            for col_name in row:
                value_dict = row.get(col_name, {})

                # Handle common non-serializable types
                value_type = value_dict.get("type", "None")

                if value_type == 'time':
                    dt = value_dict.get("data", 0)
                    results_row[col_name] = dt
                else:
                    dt = value_dict.get("data", None)
                    results_row[col_name] = dt

            results.append(results_row)

        if dedup_params:
            results_dedup = deduplicate_rows(
                rows=results, 
                dedup_columns=dedup_params['dedup_col'],
                exclude_columns=dedup_params['exclude_col'],
                timestamp_column=dedup_params['timestamp_col']
            )
            logger.debug(f"Query: {sql_name} on {conn_name} {len(results_dedup)} rows left after deduplication")
        else:
            results_dedup = results

        for row in results_dedup:
            row.update(base_data)

        returned_data['results'] = results_dedup
        return returned_data

    def get_fresh_data(self, conn_name, sql_name) -> List[Dict[str, Any]]:
        '''
        First element in the list - is the colemn list:  {"columns":columns}
        '''
        debug = 0
        if debug == 1:
            sample_target_result = {
               "columns": [
                    {"text":"Time","type":"time"},
                    {"text":"Country","type":"string"},
                    {"text":"Number","type":"number"}
                ],
                "rows":[ 
                    [1234567,"SE",123],
                    [1234567,"DE",231],
                    [1234567,"US",321]
                ],
                "type":"table",
                "target":sql_name
            }
            sample_target_result['target'] = conn_name

            sample_result = [
                {"Time": {"data":datetime.fromisoformat('2011-11-04T00:00:23+00:00').timestamp() * 1000, "type":"time"},"Country": {"data":"SE","type":"string"},"Number": {"data":123,"type":"number"}},
                {"Time": {"data":datetime.fromisoformat('2011-11-14T00:05:23+05:00').timestamp() * 1000, "type":"time"},"Country": {"data":"DE","type":"string"},"Number": {"data":231,"type":"number"}},
                {"Time": {"data":datetime.fromisoformat('2011-11-24T00:04:23').timestamp() * 1000, "type":"time"},"Country": {"data":"US","type":"string"},"Number": {"data":321,"type":"number"}}
            ]
            return sample_result

        conn = None

        sql_dict = self.sql_list_manager.get_sql(sql_name)
  
        #query = ' '.join(sql_dict.get('sql'))                   # SQL - List of strings
        #query = ' '.join(s.strip() for s in sql_dict.get('sql'))
        query = sql_dict['sql']                                 # SQL - string

        params = sql_dict.get('param', [])
        params_tuple = ()
        if len(params) > 0 :
            params_tuple = tuple(params)

        try:
            start_time = time_module.perf_counter()

            conn = self._get_db_connection(conn_name)
            cursor = conn.cursor()
    
            logger.debug(f"Executing_query_name {sql_name} on {conn_name}, Query: {query}")
            cursor.execute(query, params_tuple or ())

            columns = [desc[0] for desc in cursor.description]
            #Debug logger.debug(f"execute_query - Columns: {columns}")

            exec_time = (time_module.perf_counter() - start_time) * 1000
            #results = []
            results = [
                {"columns":columns, "elapsed":exec_time},
            ]
            i = 1
            while (row := cursor.fetchone()) is not None:
                #DEBUG logger.debug(f"execute_query - row{i}: {row}")
                i=i+1
                row_dict = {}
                for col_name, value in zip(columns, row):
                    # Handle common non-serializable types
                    if isinstance(value, (datetime)):
                        if not value.tzinfo:
                            value.replace(tzinfo=timezone.utc)
                        dt = int(value.timestamp() * 1000)   # VG - it was 1000.0
                        #DEBUG logger.debug(f"DATETIME, Column: {col_name}, source data: {value}, converted data (timestamp): {dt}")
                        row_dict[col_name] = {"data":dt, "type":"time"}

                    elif isinstance(value, (time)):
                        #if not value.tzinfo:
                        #    value.replace(tzinfo=timezone.utc)
                        dt = value.isoformat()
                        row_dict[col_name] = {"data":dt, "type":"string"}
                        #logger.debug(f"TIME: source data: {value}, converted data (isoformat): {dt}")

                    elif isinstance(value, (date)):
                        dt = value.isoformat()
                        row_dict[col_name] = {"data":dt, "type":"string"}
                        #logger.debug(f"DATE: source data: {value}, converted data (isoformat): {value.isoformat()}")
                        
                    elif isinstance(value, (int, float)):
                        row_dict[col_name] = {"data":value, "type":"number"}

                    elif isinstance(value, (bool)):
                        row_dict[col_name] = {"data":value, "type":"boolean"}

                    elif hasattr(value, '__str__'):
                        row_dict[col_name] = {"data":str(value), "type":"string"}

                    else:
                        row_dict[col_name] = {"data":value, "type":"string"}

                results.append(row_dict)

            cursor.close()
            logger.debug(f"Query: {sql_name} on {conn_name} results: {i-1} rows returned, exec time(ms): {exec_time}")
            self.last_scrape_success = 1

            return results

        except Exception as e:
            logger.error(f"Query: {sql_name} on {conn_name}, Database query failed: {str(e)}")
            self.last_scrape_success = 0
            return []
        finally:
            if conn:
                conn.close()

# Global instance
#db_call_sql = DBCallSQL()
