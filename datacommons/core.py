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
""" Data Commons base Python Client API.

Contains wrapper functions for get_property_labels, get_property_values, and
get_triples
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import defaultdict

import pandas as pd

import datacommons.utils as utils
import requests

# ----------------------------- WRAPPER FUNCTIONS -----------------------------


def get_property_labels(dcids, out=True):
  """ Returns a map from given dcids to a list of defined properties defined.

  Args:
    dcids: A list of nodes identified by their dcids.
    out: Whether or not the property points away from the given list of nodes.
  """
  # Generate the GetProperty query and send the request
  url = utils._API_ROOT + utils._API_ENDPOINTS['get_property_labels']
  res = requests.post(url, json={'dcids': dcids})
  payload = utils._format_response(res)

  # Return the results based on the orientation
  results = {}
  for dcid in dcids:
    if out:
      results[dcid] = payload[dcid]['outArcs']
    else:
      results[dcid] = payload[dcid]['inArcs']
  return results


def get_property_values(dcids,
                        prop,
                        out=True,
                        value_type=None,
                        limit=utils._MAX_LIMIT):
  """ Returns values associated to given dcids via the given property.

  If the dcids field is a list, then the return value is a dictionary mapping
  dcid to the list of values associated with the given property.

  If the dcids field is a Pandas Series, then the return value is a Series where
  the i-th cell is the list of values associated with the given property for the
  i-th dcid.

  Args:
    dcids: A string, list of, or Pandas DataSeries of dcid.
    prop: The property to get the property values for.
    out: Whether or not the property points away from the given list of nodes.
    value_type: Filter returning values by a given type.
    reload: A flag that sends the query without hitting cache when set.
    limit: The maximum number of values to return.
  """
  # Convert the dcids field and format the request to GetPropertyValue
  dcids, req_dcids = utils._convert_dcids_type(dcids)
  req_json = {
    'dcids': req_dcids,
    'property': prop,
    'outgoing': out,
    'limit': limit
  }
  if value_type:
    req_json['value_type'] = value_type

  # Send the request
  url = utils._API_ROOT + utils._API_ENDPOINTS['get_property_values']
  res = requests.post(url, json=req_json)
  payload = utils._format_response(res)

  # Create the result format for when dcids is provided as a list.
  result = defaultdict(list)
  for dcid in dcids:
    if dcid in payload and prop in payload[dcid]:
      for node in payload[dcid][prop]:
        if 'dcid' in node:
          result[dcid].append(node['dcid'])
        elif 'value' in node:
          result[dcid].append(node['value'])
    else:
      result[dcid] = []

  # Format the result as a Series if a Pandas Series is provided.
  if isinstance(dcids, pd.Series):
    return pd.Series([result[dcid] for dcid in dcids])
  return dict(result)


def get_triples(dcids, limit=utils._MAX_LIMIT):
  """ Returns a list of triples where the dcid is either a subject or object.

  The return value is a list of tuples (s, p, o) where s denotes the subject
  entity, p the property, and o the object.

  Args:
    dcid: A list of dcids to get triples for.
    limit: The maximum number of triples to get for each combination of property
    and type of the neighboring node.
  """
  # Generate the GetTriple query and send the request.
  url = utils._API_ROOT + utils._API_ENDPOINTS['get_triples']
  res = requests.post(url, json={'dcids': dcids, 'limit': limit})
  payload = utils._format_response(res)

  # Create a map from dcid to list of triples.
  results = defaultdict(list)
  for dcid in dcids:
    for t in payload[dcid]:
      if 'objectId' in t:
        results[dcid].append(
          (t['subjectId'], t['predicate'], t['objectId']))
      elif 'objectValue' in t:
        results[dcid].append(
          (t['subjectId'], t['predicate'], t['objectValue']))
  return dict(results)
