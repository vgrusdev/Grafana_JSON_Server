from flask import Flask, request, jsonify
from datetime import datetime
import time
import random

from my_utilities import *

@app.route('/metrics', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=60, window=60)
@handle_errors
def search():
    """
    Returns available metrics/targets
    Needs to configure the list of SQL requests.
    Used in grafana's dashboard query configuration.
    "value" will be sent as a 'targets':[{'target':'value',...}, {...}]
    and identify the SQL request to be executed
    """

    defaultMetrics = [{
        "label": "SQL1",       # as it appears in the grafana's query config panel
        "value": "SQL1",        # what will exactly will be in the request target field
    }, {
        "label": "SQL2",
        "value": "SQL2",
    }]

    try:
        return jsonify(defaultMetrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ----------------------------
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
}

@app.route('/query', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=100, window=60)
@handle_errors
def query():
    """
    Handle query requests from Grafana
    Returns time series data or table data
    """
    try:
        data = request.get_json()
        print(data)     # debug
        targets = data.get('targets', [])                   # array of targets
        range_from = data.get('range', {}).get('from')      # in case needs to process time-range query
        range_to = data.get('range', {}).get('to')          #

        results = []
        for target in targets:
            target_ref = target.get('target', '')
            #print("target")
            #print(target)
            sample_result['target'] = target_ref
            results.append(sample_result)

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------

@app.route('/variable', methods=['POST', 'OPTIONS'])
@authenticate
@rate_limit(max_requests=60, window=60)
@handle_errors
def variables():
    """
    Return data for Variable of type Query
    Will be used to provide Variable list based on keyword in the Query field
    that will be in the request as 'payload':{'target':'host_db'}
    """
    variables_map = {
        "host_db": [
            {"__text":"dcshdb", "__value":"dcshdb"},
            {"__text":"hostdb", "__value":"hostdb"},
        ],
        "another_var": [
            {"__text":"val1", "__value":"val1"},
            {"__text":"val2", "__value":"val2"},
        ]
    }

    data = request.get_json()
    print(data)
    target = data.get('payload', {}).get('target','')
    if target == '':
        target = "host_db"
    
    return jsonify(variables_map[target])
# ----------------------------------


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