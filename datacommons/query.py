# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Data Commons Python API Query Module.

Implements functions for sending graph queries to the Data Commons Graph.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datacommons.utils import _API_ROOT, _API_ENDPOINTS, _ENV_VAR_API_KEY

import json
import os
import time
import six.moves.urllib.error
import six.moves.urllib.request

# delay between a failed a query and a second attempt in recursive_query
_DELAY_TIME = 5
# maximum number of attempts of a single query to DC
_TRIAL_LIMIT = 5
# maximum size of split input list to submit a query for
_CHUNK_SIZE = 350

# ----------------------------- WRAPPER FUNCTIONS -----------------------------


def query(query_string, select=None):
    """ Returns the results of executing a SPARQL query on the Data Commons graph.

  Args:
    query_string (:obj:`str`): The SPARQL query string.
    select (:obj:`func` accepting a row in the query result): A function that
      selects rows to be returned by :code:`query`. This function accepts a row
      in the results of executing :code:`query_string` and return True if and
      only if the row is to be returned by :code:`query`. The row passed in as
      an argument is represented as a :obj:`dict` that maps a query variable in
      :code:`query_string` to its value in the given row.

  Returns:
    A table, represented as a :obj:`list` of rows, resulting from executing the
    given SPARQL query. Each row is a :obj:`dict` mapping query variable to its
    value in the row. If `select` is not `None`, then a row is included in the
    returned :obj:`list` if and only if `select` returns :obj:`True` for that
    row.

  Raises:
    ValueError: If the payload returned by the Data Commons REST API is
      malformed.

  Examples:
    We would like to query for the name associated with three states identified
    by their dcids
    `California <https://browser.datacommons.org/kg?dcid=geoId/06>`_,
    `Kentucky <https://browser.datacommons.org/kg?dcid=geoId/21>`_, and
    `Maryland <https://browser.datacommons.org/kg?dcid=geoId/24>`_.

    >>> query_str = '''
    ... SELECT ?name ?dcid
    ... WHERE {
    ...   ?a typeOf Place .
    ...   ?a name ?name .
    ...   ?a dcid ("geoId/06" "geoId/21" "geoId/24") .
    ...   ?a dcid ?dcid
    ... }
    ... '''
    >>> result = query(query_str)
    >>> for r in result:
    ...   print(r)
    {"?name": "Maryland", "?dcid": "geoId/24"}
    {"?name": "Kentucky", "?dcid": "geoId/21"}
    {"?name": "California", "?dcid": "geoId/06"}

    Optionally, we can specify which rows are returned by setting :code:`select`
    like so. The following returns all rows where the name is "Maryland".

    >>> selector = lambda row: row['?name'] == 'Maryland'
    >>> result = query(query_str, select=selector)
    >>> for r in result:
    ...   print(r)
    {"?name": "Maryland", "?dcid": "geoId/24"}
  """

    req_url = _API_ROOT + _API_ENDPOINTS['query']
    headers = {'Content-Type': 'application/json'}
    if os.environ.get(_ENV_VAR_API_KEY):
        headers['x-api-key'] = os.environ[_ENV_VAR_API_KEY]

    req = six.moves.urllib.request.Request(req_url,
                                           data=json.dumps({
                                               'sparql': query_string
                                           }).encode("utf-8"),
                                           headers=headers)

    try:
        res = six.moves.urllib.request.urlopen(req)
    except six.moves.urllib.error.HTTPError as e:
        raise ValueError('Response error {}:\n{}'.format(e.code, e.read()))

    # Verify then store the results.
    res_json = json.loads(res.read())

    # Iterate through the query results
    header = res_json.get('header')
    if header is None:
        raise ValueError('Ill-formatted response: does not contain a header.')
    result_rows = []
    for row in res_json.get('rows', []):
        # Construct the map from query variable to cell value.
        row_map = {}
        for idx, cell in enumerate(row.get('cells', [])):
            if idx > len(header):
                raise ValueError('Query error: unexpected cell {}'.format(cell))
            if 'value' not in cell:
                raise ValueError(
                    'Query error: cell missing value {}'.format(cell))
            cell_var = header[idx]
            row_map[cell_var] = cell['value']
        # Add the row to the result rows if it is selected
        if select is None or select(row_map):
            result_rows.append(row_map)
    return result_rows


def recursive_query(query_str, trial_num):
    """Helper function to recursively call query function from above.

  If the query results in an error, then this function will be recursively
  called after a delay of DELAY_TIME seconds until the query is resolved or the
  TRIAL_LIMIT has been exceeded.

  Args:
      query_str: The query to be passed to query function above.
      trial_num: The number of times the query has been attempted.

  Returns:
    The result of the query function from above which is one array of tuples
    from the SPARQL select statements.

  Raises:
    If the TRIAL_LIMIT is exceeded, then the error thrown by query is
    raised.
  """
    try:
        return query(query_str)
    except:
        if trial_num >= _TRIAL_LIMIT:
            print('exceeded trial limit: ' + query_str)
            raise
        time.sleep(_DELAY_TIME)
        recursive_query(query_str, trial_num + 1)


def conduct_chunked_query(query_template, template_mapping):
    """Generates query strings from args and passes them to recursive_query().

  Chunks the value in template_mapping dictionary whose value is a list with the
  longest length into smaller sizes. A query string is generated for each chunk,
  which is passed to the helper function recursive_query.

  Args:
	  query_template: A template string for the desired query which should have
        variable substitutions by name, such that format_map() can be applied
        with the format dictionary that is passed in.
	  template_mapping: A dictionary containing the keys and values to fill in
          the template string given as query_template.

  Returns:
      The results from each of the chunked queries, joined together as one array
      of tuples from the SPARQL select statement.

  Examples:
    We would like to query for the all DiseaseGeneAssociation nodes that have a
    property called geneID with the value being in a given list called
    gene_dcid_list. Let's say two gene dcids within the list are 'bio/hg38_CDSN'
    and 'bio/hg38_PPARA'.

    >>> query_template = '''
    ... SELECT ?cga_dcid ?gene
    ... WHERE {{
    ...   ?gene dcid ("{gene_dcids}") .
    ...   ?cga_dcid typeOf {type} .
    ...   ?cga_dcid {label} ?gene .
    ... }}
    ... '''
    >>> gene_dcid_list = ['bio/hg38_CDSN', 'bio/hg38_PPARA', ...]
    >>> mapping = {
    ... 'type': 'ChemicalCompoundGeneAssociation',
    ... 'label': 'geneID',
    ... 'gene_dcids': gene_dcid_list
    ... }
    >>> result = conduct_chunked_query(query_template, mapping)
    >>> print(result)
    [{'?cga_dcid': 'bio/CGA_CHEMBL888_hg38_CDSN', '?gene': 'bio/hg38_CDSN'},
    {'?cga_dcid': 'bio/CGA_PA449061_hg38_PPARA', '?gene': 'bio/hg38_PPARA'},
    ...
  """
    # find longest list value
    max_val = {'key': '', 'length': -1}
    for key, value in template_mapping.items():
        if value and isinstance(value, list):
            if len(value) > max_val['length']:
                max_val['length'] = len(value)
                max_val['key'] = key

    # no need to chunk if no list value exists or if longest list < CHUNK_SIZE
    if max_val['length'] < _CHUNK_SIZE:
        return query(query_template.format_map(template_mapping))

    chunk_input = max_val['key']
    query_arr = template_mapping[chunk_input]

    mapping = template_mapping.copy()

    result = []
    i = 0
    while i + _CHUNK_SIZE < len(query_arr):
        mapping[chunk_input] = '" "'.join(query_arr[i:i + _CHUNK_SIZE])
        query_str = query_template.format_map(mapping)
        chunk_result = recursive_query(query_str, 0)
        if chunk_result:
            result.extend(chunk_result)
        i += _CHUNK_SIZE

    # conduct query for remaining chunk
    mapping[chunk_input] = '" "'.join(query_arr[i:])
    query_str = query_template.format_map(mapping)
    chunk_result = recursive_query(query_str, 0)
    if chunk_result:
        result.extend(chunk_result)
    return result
