from flask import Flask, request, jsonify
#from datetime import datetime
import time
import os
#import random
import logging

from concurrent.futures import ThreadPoolExecutor, as_completed

# from db_config_manager import db_config_manager     # Loads DB config file JSON
# from sql_list import sql_list                       # Loads SQL List

import db_config_manager        # Loads DB config file JSON
import sql_list_manager         # Loads SQL List
import db_call_sql_redis as db_call_sql              # SQL exec
#from query_ssl_cert import QuerySSLCert
import query_ssl_cert as query_ssl_cert

# Default log level
default_level = logging.INFO

# Get log level from environment
log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()

# Validate log level
valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
if log_level_name not in valid_levels:
    print(f"Invalid LOG_LEVEL: {log_level_name}. Using INFO.")
    log_level = default_level
else:
    log_level = getattr(logging, log_level_name)

    # Configure logging
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    #datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        #logging.FileHandler('grafana_datasource.log'),
        logging.StreamHandler()
    ]
)

from my_utilities import *

db_config_manager   = db_config_manager.DatabaseConfigManager()
sql_list_manager    = sql_list_manager.SQLListManager(db_config_manager)
db_call_sql         = db_call_sql.DBCallSQL(db_config_manager, sql_list_manager)
query_ssl_cert      = query_ssl_cert.QuerySSLCert(db_config_manager)

#from my_utilities import *

logger = logging.getLogger(__name__)


@app.route('/metrics', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=60, window=60)
@handle_errors
def metrics():
    """
    Returns available metrics/targets
    Needs to configure the list of SQL requests.
    Used in grafana's dashboard query configuration.
    "value" will be sent as a 'targets':[{'target':'value',...}, {...}]
    and identify the SQL request to be executed

    defaultMetrics = [{
        "label": "SQL1",       # as it appears in the grafana's query config panel
        "value": "SQL1",        # what will exactly will be in the request target field
    }, {
        "label": "SQL2",
        "value": "SQL2",
    }]
    """

    sql_metrics = []
    all_sqls = sql_list_manager.get_all_sqls()
    logger.debug(f"metrics handler. all sqls returned from get_all_sqls: {all_sqls} ")
    for sql in all_sqls :
        elem = dict(value=sql)
        #elem["label"] = all_sqls[sql].get('name', sql))
        elem["label"] = "-".join( [item for item in [sql, all_sqls[sql].get('name', "")] if item != ""] )
        sql_metrics.append(elem)

    try:
        return jsonify(sql_metrics)
    except Exception as e:
        logger.error(f"Metrics processing error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ----------------------------

@app.route('/query', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=100, window=60)
@handle_errors
def query():
    """
    Handle query requests from Grafana
    Returns time series data or table data
    This is request coming from Grafana:

    {   'app': 'dashboard', 'requestId': 'SQR100', 'timezone': 'browser', 
        'range': {
            'to': '2025-11-20T17:30:23.859Z', 'from': '2025-11-20T11:30:23.859Z', 'raw': {'from': 'now-6h', 'to': 'now'}
        }, 
        'interval': '30s', 'intervalMs': 30000, 
        'targets': [{                                               <===== This is List of targets per Query field
            'datasource': {'type': 'simpod-json-datasource', 'uid': 'ff4ioqzingruoc'}, 'editorMode': 'code', 
            'payload': {'myvar': '1', 'db_connection_name': 'dcshdb'},  <===== this is written in the payload fiels of the QUERY Edit panel, $variable can be used
            'refId': 'A', 
            'target': 'SQL1'                                        <===== this is selected as a Metric name at the QUERY Edit panel
        }], 
        'maxDataPoints': 752, 
        'scopedVars': {
            'undefined': {'selected': False, 'text': 'dcshdb', 'value': 'dcshdb'}, 
            '__sceneObject': {'text': '__sceneObject'}, 
            '__interval': {'text': '30s', 'value': '30s'}, 
            '__interval_ms': {'text': '30000', 'value': 30000}
        }, 
        'startTime': 1763659823990, 'rangeRaw': {'from': 'now-6h', 'to': 'now'}, 
        'dashboardUID': '9c48f14b-e8ff-4455-b9e2-da8e264b9de6', 'panelId': 1, 'panelName': 'New panel', 'panelPluginId': 'table', 'dashboardTitle': 'New dashboard - 1'
    }
    -----------------------
    Response format to Grafana:
    sample_result = {
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
        "type":"table"
        "target":"SQL1"
    }
    """
    try:
        data = request.get_json()                           # get request from Grafana
        logger.debug(f"Query - request_data_from_grafana: {data}")

        targets = data.get('targets', [])                   # array of targets
        range_from = data.get('range', {}).get('from')          # in case we need to process time-range query
        range_to = data.get('range', {}).get('to')              #

        results = []
        i = 1
        for target in targets:
            sql_name     = target.get('target', '')           #  SQL name
            payload      = target.get('payload')
            db_conn_name = payload.get('db_connection_name')                  # DB connection name
            logger.debug(f"Query - target{i}: db_connection_name (from payload): {db_conn_name}, SQL name (Metric): {sql_name}")
            i=i+1
            result = db_call_sql.execute_query(db_conn_name, sql_name)

            result['target'] = sql_name
            result['type']   = 'table'

            results.append(result)

        return jsonify(results)
    except Exception as e:
        logger.error(f"Query processing error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ----------------------------

@app.route('/query_json', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=100, window=60)
@handle_errors
def query_json():
    """
    Handle query requests from json_exporter (https://github.com/prometheus-community/json_exporter/)

    This is request coming from json_exporter:

    {   'db_connection_name': 'dcshdb'},    <===== DB connection name
        'target': ['SQL1' 'SQL2']                    <===== SQL Name
        'timestamp_col': [ 'TIMESTAMP' ]
        'exclude_col': [ 'Value' ]
        'dedup_col': [ 'A' 'B' ]
    }

    timestamp_col, exclude_col, dedup_col - are optional.
    Prometheus does not like duplicated labels in one serie of data, it just ignore whole serie.
    So, we need to dedup rows from database, grouping by labels, except probably TIMESTAMP and Value.

    We can provide any combination of columns for deduplication (see function).
    timestamp may be needed to select last record among duplicates.
    -----------------------
    Response format to json_exporter:
    sample_result = [
        {   column_name1: value1, 
            column_name2: value2
        },
        ...
    ]

    """
    try:
        data = request.get_json()                           # get request from client
        logger.debug(f"query_json - request_data: {data}")

        db_connections = parse_prometheus_param(data.get('db_connection_name', [])) # List of db_conn names, Normalize to list
        #sql_name     = data.get('target', '')                                       #  SQL name

        sql_list        = parse_prometheus_param(data.get('target', []))
        if (not db_connections) or (not sql_list):
            logger.error(f"db_conncetion_name and target parameters must be provided")
            return jsonify({"db_conncetion_name or target must be provided"}), 500
    except Exception as e:
        logger.error(f"Query JSON processing error: {str(e)}")
        return jsonify({"Input JSON processing error": str(e)}), 500

    dedup_params = {
        'timestamp_col': None,
        'exclude_col':   None,
        'dedup_col':     None
    }
    t = parse_prometheus_param(data.get('timestamp_col', []))
    if t:
        dedup_params['timestamp_col'] = t[0]
    e = parse_prometheus_param(data.get('exclude_col', []))
    if e:
        dedup_params['exclude_col'] = e
    d = parse_prometheus_param(data.get('dedup_col', []))
    if d:
        dedup_params['dedup_col'] = d

    all_results = []
    for sql_name in sql_list:
        with ThreadPoolExecutor(max_workers=5) as executor:    
            # Submit all queries in parallel
            future_to_conn = {}
            for db_conn in db_connections:
                future = executor.submit(db_call_sql.execute_query_json, db_conn, sql_name, dedup_params=dedup_params)
                future_to_conn[future] = db_conn

            #all_results = []
            for future in as_completed(future_to_conn):
                db_conn = future_to_conn[future]
                try:
                    result = future.result(timeout=30)  # 30 second timeout
                    if ('results' in result) and (len(result['results']) > 0):
                        all_results.append(result)
                except Exception as e:
                    logger.error(f"Query exec error for conn:{db_conn}. {str(e)}")

    return jsonify(all_results)


# ------------------------------------

@app.route('/variable', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=60, window=60)
@handle_errors
def variables():
    """
    Return data for Variable of type Query
    Will be used to provide Variable list based on keyword in the Query field
    It can be used as hostname in DB connection request for example...
    that will be in the request as 'payload':{'target':'host_db'}

    This is request comong from Grafana, when variable query is called

    {
        'payload': {'target': 'db_connection_name'},           <=== the word 'host_db' is written in the Query field of the variable type Query
                                                                    in the current release it is not used.
        'range': {'to': '2025-11-20T19:20:17.437Z', 'from': '2025-11-20T13:20:17.437Z', 'raw': {'from': 'now-6h', 'to': 'now'}}
    }
    ----------
    variables_map = {
        "db_connection_name": [
            {"__text":"dcshdb", "__value":"dcshdb"},    <=== db connection names
            {"__text":"hostdb", "__value":"hostdb"},
        ]
    }
    """


    #data = request.get_json()
    #logger.debug(f"varible - request_data: {data}")
    #target = data.get('payload', {}).get('target','db_connection_name')

    variables_resp = []

    for db_name in db_config_manager.get_database_names() :
        elem = dict(__text=db_name, __value=db_name)
        variables_resp.append(elem)

    logger.debug(f"variables_resp: {variables_resp}")

    try:
        return jsonify(variables_resp)
    except Exception as e:
        logger.error(f"Variable processing error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ----------------------------------

@app.route('/query_ssl', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=100, window=60)
@handle_errors
def query_ssl():
    """
    Handle query requests from json_exporter (https://github.com/prometheus-community/json_exporter/)

    This is request coming from json_exporter:

    {   'connections': 'server.local:443:myserver.local'},    <===== colon separeted connection address, port, servername 
                                                                                (in case server hosts multiple websites - SNI)
    }
    -----------------------
    Response format to json_exporter:
    sample_result = [
        {   column_name1: value1, 
            column_name2: value2
        },
        ...
    ]

    """
    try:
        data = request.get_json()                           # get request from client
        logger.debug(f"query_ssl - request_data: {data}")

        connections = parse_prometheus_param(data.get('connections', [])) # List of connections names, Normalize to list
        if (not connections):
            logger.error(f"connections option must be provided")
            return jsonify({"connections must be provided"}), 500

        ttl = 30
        ttl_list = parse_prometheus_param(data.get('ttl', []))
        try:
            if ttl_list:
                ttl = int(ttl_list[0])
        except Exception as e:
            logger.warning(f"Can not convert ttl parameter to int: {ttl_list[0]}")
            ttl = 30
        if ( ttl < 0 or ttl > 10000000):
            logger.warning(f"ttl is out of range, use default value {ttl} sec")
        else:
            logger.info(f"Use ttl: {ttl} sec")

    except Exception as e:
        logger.error(f"JSON processing error: {str(e)}")
        return jsonify({"Input JSON processing error": str(e)}), 500

    all_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:    
        # Submit all queries in parallel

        future_to_conn = {}
        for conn in connections:
            future = executor.submit(query_ssl_cert.get_ssl_certificate, conn, ttl)
            future_to_conn[future] = conn

        #all_results = []
        for future in as_completed(future_to_conn):
            conn = future_to_conn[future]
            try:
                result = future.result(timeout=30)  # 30 second timeout
                #if ('results' in result) and (len(result['results']) > 0):
                if result:
                    all_results.append(result)
            except Exception as e:
                logger.error(f"query_ssl exec error for conn:{conn}. {str(e)}")

    return jsonify(all_results)

# ------------------------------------

if __name__ == '__main__':
    ssl_context = generate_ssl_context()
    
    if ssl_context:
        logger.info("Starting HTTPS server with Basic Auth support...")
        app.run(
            host='0.0.0.0', 
            port=5000, 
            debug=app.config['DEBUG'],
            ssl_context=ssl_context
        )
    else:
        logger.info("Starting HTTP server with Basic Auth support...")
        app.run(
            host='0.0.0.0', 
            port=5000, 
            debug=app.config['DEBUG']
        )
