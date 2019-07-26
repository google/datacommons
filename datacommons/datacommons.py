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

Contains Query which performs graph queries on the Data Commons kg, Node
which wraps a node in the graph, and Frame which provides a tabular view of
graph data.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datacommons.utils import (format_response, _API_ROOT, _API_ENDPOINTS,
  _BIG_QUERY_PATH, _MAX_LIMIT)
from datacommons.places import PlacesMixin

from collections import defaultdict, OrderedDict
import pandas as pd
import copy
import requests

# -----------------------------------------------------------------------------
# Query Class
# -----------------------------------------------------------------------------

class Query(object):
  """ Performs a graph query to the Data Commons knowledge graph. """

  # Valid query languages
  _SPARQL_LANG = 'sparql'
  _VALID_LANG = [_SPARQL_LANG]

  def __init__(self, **kwargs):
    """ Initializes a Query.

    Keyword Args:
      sparql: A sparql query string.
    """
    if self._SPARQL_LANG in kwargs:
      self._query = kwargs[self._SPARQL_LANG]
      self._language = self._SPARQL_LANG
      self._result = None
    else:
      lang_str = ', '.join(self._VALID_LANG)
      raise ValueError('Must provide one of the following languages: {}'.format(lang_str))

  def rows(self, select=None):
    """ Returns the results of the query as an iterator over all rows.

    Rows from the query are represented as maps from query variable to its value
    in the current row.

    Args:
      select: A function that returns true if and only if a row in the query
        results should be kept. The argument for this function is a map from
        query variable to its value in a given row.
    """
    # Execute the query if the results are empty.
    if not self._result:
      self._execute()

    # Iterate through the query results
    header = self._result['header']
    for row in self._result['rows']:
      # Construct the map from query variable to cell value.
      row_map = {}
      for idx, cell in enumerate(row['cells']):
        if idx > len(header):
          raise RuntimeError('Query error: unexpected cell {}'.format(cell))
        if 'value' not in cell:
          raise RuntimeError('Query error: cell missing value {}'.format(cell))
        cell_var = header[idx]
        row_map[cell_var] = cell['value']

      # Yield the row if it is selected
      if select is None or select(row_map):
        yield row_map

  def as_Frame(self, type_hint, select=None, process=None, labels={}):
    """ Returns the result as a Frame.

    Args:
      type_hint: A map from query variables to types.
      select: A function that returns true if and only if a row in the query
        results should be kept. The argument for this function is a map from
        query variable to its value in a given row.
      process: A function that takes in a Pandas DataFrame. Can be used for
        post processing the results such as converting columns to certain types.
        Functions should index into columns using names prior to relabeling.
      labels: A map from the query variables to their column names in the
        Frame.

    Raises:
      RuntimeError: on query failure (see error hint).
    """
    # Get the rows filtered by select. Then process, and relabel as necessary.
    query_rows = list(self.rows(select=select))
    frame = Frame(query_rows, type_hint=type_hint, process=process)
    frame.rename(labels)
    return frame

  def _execute(self):
    """ Execute the query.

    Raises:
      RuntimeError: on query failure (see error hint).
    """
    # Create the query request.
    if self._language == self._SPARQL_LANG:
      payload = {'sparql': self._query}
    url = _API_ROOT + _API_ENDPOINTS['query']
    res = requests.post(url, json=payload)

    # Verify then store the results.
    res_json = res.json()
    if 'message' in res_json:
      raise RuntimeError('Query error: {}'.format(res_json['message']))
    self._result = res.json()


# -----------------------------------------------------------------------------
# Node Class
# -----------------------------------------------------------------------------

class Node(object):
  """ Wraps a node found in the Data Commons knowledge graph. """

  def __init__(self, **kwargs):
    """ Constructor for the node.

    Keyword Args:
      dcid: The dcid of the node. Either this or "value" must be specified
      value: The value contained by a Node. This specifies the node as a leaf
        node. Either this or "dcid" must be specified
      name: The name of the node
      types: A list of Data Commons types associated with the node.
      node: If this is specified, then the constructor is treated as a copy
        constructor. This parameter must be an instance of a Node.

    Node instance variables:
      _dcid: The dcid of the node. This should never change after the creation
        of the Node.
      _name: The name of the node
      _value: A node is a leaf node if it only contains a value. Leaf nodes do
        not have a specific dcid assigned to them.
      _types: A list of types associated with the node
      _in_props: A map from incoming property to other nodes.
      _out_props: A map from outgoing property to other nodes.

    Raises:
      ValueError: If neither of dcid or value are provided.
    """
    # If 'node' is provided as a keyword argument, then this constructor is
    # treated as a copy constructor.
    if 'node' in kwargs:
      if not isinstance(kwargs['node'], Node):
        raise ValueError('The node must be an instance of Node.')
      self._dcid = kwargs['node']._dcid
      self._name = kwargs['node']._name
      self._value = kwargs['node']._value
      self._types = copy.deepcopy(kwargs['node']._types)
      self._in_props = copy.deepcopy(kwargs['node']._in_props)
      self._out_props = copy.deepcopy(kwargs['node']._out_props)
      return

    # TODO(antaresc): Remove this after id -> dcid in the EntityInfo proto
    if 'id' in kwargs and 'dcid' not in kwargs:
      kwargs['dcid'] = kwargs['id']
    if 'dcid' not in kwargs and 'value' not in kwargs:
      raise ValueError('Must specify one of "dcid" or "value"')
    if 'dcid' in kwargs and 'value' in kwargs:
      raise ValueError('Can only specify one of "dcid" or "value"')

    # Initialize all fields
    self._dcid = None
    self._name = None
    self._value = None
    self._types = []
    self._in_props = {}
    self._out_props = {}

    # Populate fields based on if this is a node with a dcid or a leaf node.
    if 'dcid' in kwargs:
      if 'name' in kwargs and 'types' in kwargs:
        self._name = kwargs['name']
        self._types = kwargs['types']
      else:
        # Send a request to get basic node information from the graph.
        params = "?dcid={}".format(kwargs['dcid'])
        url = _API_ROOT + _API_ENDPOINTS['get_node'] + params
        res = requests.get(url)
        payload = format_response(res)

        # Set the name and type
        if 'name' in kwargs:
          self._name = kwargs['name']
        elif 'name' in payload:
          self._name = payload['name']
        if 'types' in kwargs:
          self._types = kwargs['types']
        elif 'types' in payload:
          self._types = payload['types']
      # Set the dcid
      self._dcid = kwargs['dcid']
    else:
      self._value = kwargs['value']

  def __eq__(self, other):
    """ Overrides == operator.

    Two nodes are equal if and only if they have the same dcid. Leaf-nodes are
    by definition not equal to each other. This means a comparison between a
    leaf node and a node with a dcid or two leaf nodes is always False.
    """
    return bool(self._dcid) and bool(other._dcid) and self._dcid == other._dcid

  def __ne__(self, other):
    """ Overrides != operator. """
    return not (self == other)

  def __str__(self):
    """ Overrides str() operator. """
    fields = {}
    if self._dcid:
      fields['dcid'] = self._dcid
    if self._name:
      fields['name'] = self._name
    if self._value:
      fields['value'] = self._value
    return str(fields)

  def __hash__(self):
    """ Overrides hash() operator.

    The hash of a node with a dcid is the hash of the string "dcid: <the dcid>"
    while the hash of a leaf is the hash of "value: <the value>".
    """
    if self.is_leaf():
      return hash('value:{}'.format(self._value))
    return hash('dcid:{}'.format(self._dcid))

  def is_leaf(self):
    """ Returns true if the node only contains a single value. """
    return bool(self._value)

  def get_properties(self, outgoing=True, reload=False):
    """ Returns a list of properties associated with this node.

    Args:
      outgoing: whether or not the node is a subject or object.
    """
    # Generate the GetProperty query and send the request.
    params = "?dcid={}".format(self._dcid)
    if reload:
      params += "&reload=true"
    url = _API_ROOT + _API_ENDPOINTS['get_property'] + params
    res = requests.get(url)
    payload = format_response(res)

    # Return the results based on the orientation
    if outgoing and 'outArcs' in payload:
      return payload['outArcs']
    elif not outgoing and 'inArcs' in payload:
      return payload['inArcs']
    return []

  def get_property_values(self,
                          prop,
                          outgoing=True,
                          value_type=None,
                          reload=False,
                          limit=_MAX_LIMIT):
    """ Returns a list of values mapped to this node by a given prop.

    Args:
      prop: The property adjacent to the current node.
      outgoing: whether or not the node is a subject or object.
      value_type: Filter values mapped to this node by the given type.
      reload: Send the query without hitting cache.
      limit: The maximum number of values to return.
    """
    # Check if there are enough property values in the cache.
    if outgoing and prop in self._out_props:
      if len(self._out_props[prop]) >= limit:
        return self._out_props[prop][:limit]
    elif not outgoing and prop in self._in_props:
      if len(self._in_props[prop]) >= limit:
        return self._in_props[prop][:limit]

    # Query for the rest of the nodes to meet limit. First create request body
    req_json = {
      'dcids': [self._dcid],
      'property': prop,
      'outgoing': outgoing,
      'reload': reload,
      'limit': limit
    }
    if value_type:
      req_json['value_type'] = value_type

    # Send the request to GetPropertyValue
    url = _API_ROOT + _API_ENDPOINTS['get_property_value']
    res = requests.post(url, json=req_json)
    payload = format_response(res)

    # Create nodes for each property value returned.
    prop_vals = set()
    if self._dcid in payload and prop in payload[self._dcid]:
      nodes = payload[self._dcid][prop]
      for node in nodes:
        prop_vals.add(Node(**node))

    # Cache the results and set prop_vals to the appropriate list of nodes.
    in_props, out_props = defaultdict(set), defaultdict(set)
    if outgoing:
      out_props[prop] = prop_vals
    else:
      in_props[prop] = prop_vals
    self._update_props(in_props, out_props)

    # Return the results
    return list(prop_vals)

  def get_triples(self, as_node=False, reload=False, limit=_MAX_LIMIT):
    """ Returns a list of triples where this node is either a subject or object.

    The return value is a list of tuples (s, p, o) where s denotes the subject
    entity, p the property, and o the object. When "as_node" is set to True, the
    subject and object are converted to Node instances.

    Note that if as_node is specified, then the Node instances are deep copies
    of the given node wherever it is a subject or object.

    Args:
      as_node: Convert the subject and object in the triples into nodes.
      reload: Send the query without hitting cache.
      limit: The maximum number of values to return.
    """
    # Generate the GetTriple query and send the request.
    params = "?dcid={}&limit_per_arc={}".format(self._dcid, limit)
    if reload:
      params += "&reload=true"
    url = _API_ROOT + _API_ENDPOINTS['get_triple'] + params
    res = requests.get(url)
    payload = format_response(res)

    # If the payload did not return triples, return an empty list.
    triples = []
    if 'triples' not in payload:
      return triples

    # Iterate through all triples in the payload to build the response list.
    for t in payload['triples']:
      if as_node:
        if 'subjectId' in t and 'objectId' in t:
          # A triple with the object as a reference node.
          if t['subjectId'] == self._dcid:
            # A triple with an outgoing predicate
            object_info = {
              'dcid': t['objectId'],
              'types': t['objectTypes']
            }
            if 'objectName' in t:
              object_info['name'] = t['objectName']

            # Create the triple
            sub, obj = Node(node=self), Node(**object_info)
            triples.append((sub, t['predicate'], obj))
          else:
            # A triple with an incoming predicate
            subject_info = {
              'dcid': t['subjectId'],
              'types': t['subjectTypes']
            }
            if 'subjectName' in t:
              subject_info['name'] = t['subjectName']

            # Create the triple
            sub, obj = Node(**subject_info), Node(node=self)
            triples.append((sub, t['predicate'], obj))
        elif 'objectValue' in t:
          # A triple with the object as a leaf node. Currently, all triples with
          # objectValue also have the given node as the subjectId.
          sub, obj = Node(node=self), Node(value=t['objectValue'])
          triples.append((sub, t['predicate'], obj))
      else:
        if 'objectId' in t:
          triples.append((t['subjectId'], t['predicate'], t['objectId']))
        elif 'objectValue' in t:
          triples.append((t['subjectId'], t['predicate'], t['objectValue']))

    # If as_node is specified, add all triples to the node's in/out_props cache.
    if as_node:
      in_props, out_props = defaultdict(set), defaultdict(set)
      for s, p, o in triples:
        if s == self:
          out_props[p].add(o)
        else:
          in_props[p].add(o)
      self._update_props(in_props, out_props)

    # Return the resulting triples.
    return triples

  def _update_props(self, in_props, out_props):
    """ Updates self._in_props and _out_props given the following dicts.

    Args:
      in_props: A map from property name to a set of property values to update
        self._in_props with.
      out_props: A map from property name to a set of property values to update
        self._out_props with.
    """
    for p in in_props:
      if p not in self._in_props:
        self._in_props[p] = []
      self._in_props[p] = list(set(self._in_props[p]).union(in_props[p]))
    for p in out_props:
      if p not in self._out_props:
        self._out_props[p] = []
      self._out_props[p] = list(set(self._out_props[p]).union(out_props[p]))


# -----------------------------------------------------------------------------
# Frame Class
# -----------------------------------------------------------------------------

class Frame(PlacesMixin):
  """ Provides a tabular view of the Data Commons knowledge graph. """

  def __init__(self, data={}, type_hint={}, process=None):
    """ Initializes a Frame.

    Args:
      data: Either a list of dictionaries where each row is represented as a
        dictionary mapping column name to column value, or a dictionary mapping
        column name to all values in that column.
      process: A function that takes in a Pandas DataFrame. Can be used for
        post processing the results such as converting columns to certain types.
        Functions should index into columns using names prior to relabeling.
      type_hint: A map from column names to a list of types that the column
        contains.
      file_name: File name of a cached Frame to initialize the given frame
        from.

    Frame instance variables:
      _dataframe: The Pandas dataframe containing data in this Frame
      _col_types: A map from each column in the frame to a list of types it
        contains.

    Raises:
      RuntimeError: some problem with the given data
    """
    # Initialize the Frame from a (potentially empty) data parameter. First
    # create the Pandas data frame
    pd_frame = pd.DataFrame(data)
    if process:
      pd_frame = process(pd_frame)

    # Verify that all columns are typed.
    for col in pd_frame:
      if col not in type_hint:
        raise ValueError('No type provided for column {} in data.'.format(col))

    # Initialize the fields of the Frame
    self._dataframe = pd_frame.reset_index(drop=True)
    self._col_types = type_hint

  def columns(self):
    """ Returns the set of column names for this frame.

    Returns:
      Set of column names for this frame.
    """
    return [col for col in self._dataframe]

  def types(self, col_name):
    """ Returns a map from column name to associated Data Commons type.

    Args:
      col_name: Column to get types for.

    Returns:
      Map from column name to column type.
    """
    if col_name not in self.columns():
      raise ValueError('Frame error: {} not a column in the frame.'.format(col_name))
    return self._col_types[col_name]

  def pandas(self, col_names=None, ignore_populations=False):
    """ Returns a copy of the data in this view as a Pandas DataFrame.

    Args:
      col_names: An optional list specifying which columns to extract.
      ignore_populations: Ignores all columns that have type
        StatisticalPopulation. col_names takes precedence over this argument

    Returns: A deep copy of the underlying Pandas DataFrame.
    """
    if not col_names:
      col_names = list(self._dataframe)
    if ignore_populations:
      col_names = list(filter(lambda name: 'StatisticalPopulation' in self._col_types[name], col_names))
    return self._dataframe[col_names].copy()

  def csv(self, col_names=None):
    """ Returns the data in this view as a CSV string.

    Args:
      col_names: An optional list specifying which columns to extract.

    Returns:
      The DataFrame exported as a CSV string.
    """
    if col_names:
      return self._dataframe[col_names].to_csv(index=False)
    return self._dataframe.to_csv(index=False)

  def tsv(self, col_names=None):
    """ Returns the data in this view as a TSV string.

    Args:
      col_names: An optional list specifying which columns to extract.

    Returns:
      The DataFrame exported as a TSV string.
    """
    if col_names:
      return self._dataframe[col_names].to_csv(index=False, sep='\t')
    return self._dataframe.to_csv(index=False, sep='\t')

  def rename(self, labels):
    """ Renames the columns of the Frame.

    Args:
      labels: A map from current to new column names.
    """
    col_types = {}
    for col in self._dataframe:
      col_name = col
      if col in labels:
        col_name = labels[col]
      col_types[col_name] = self._col_types[col]
    self._col_types = col_types
    self._dataframe = self._dataframe.rename(index=str, columns=labels)

  def add_column(self, col_name, col_type, col_vals):
    """ Adds a column containing the given values of the given type.

    Args:
      col_name: The name of the column
      col_type: The string type of the column
      col_vals: The values in the given column
    """
    self._verify_col_add(col_name)
    self._col_types[col_name] = [col_type]
    self._dataframe[col_name] = col_vals

  def expand(self,
             property,
             seed_col_name,
             new_col_name,
             new_col_type=None,
             outgoing=True,
             reload=False,
             limit=100):
    """ Creates a new column containing values for the given property.

    For each entity in the given seed column, queries for entities related to
    the seed entity via the given property. Results are stored in a new column
    under the provided name. The seed column should contain only DCIDs.

    Args:
      property: The property to add to the table.
      seed_col_name: The column name that contains dcids that the added
        properties belong to.
      new_col_name: The new column name.
      new_col_type: The type contained by the new column. Provide this if the
        type is not immediately inferrable.
      outgoing: Set this flag if the seed property points away from the entities
        denoted by the seed column. That is the seed column serve as subjects
        in triples formed with the given property.
      reload: Send the query without hitting cache.
      limit: The maximum number of rows returned by the query results.

    Raises:
      ValueError: when input argument is not valid.
    """
    # Error checking
    self._verify_col_add(new_col_name, seed_col=seed_col_name)
    self._verify_col_type_excludes(seed_col_name, ['Text'])

    # Get the seed column information
    seed_col = self._dataframe[seed_col_name]
    seed_col_type = self._col_types[seed_col_name]

    # Get the list of DCIDs to query for
    # NOTE: new_col_type may be None here and so the updated column type may be
    #       wrong
    dcids = list(seed_col)
    if not dcids:
      self._dataframe[new_col_name] = ''
      self._col_types[new_col_name] = [new_col_type]
      return

    # Create and send the GetPropertyValue request.
    req_json = {
      'dcids': dcids,
      'property': property,
      'outgoing': outgoing,
      'reload': reload,
      'limit': limit
    }
    if new_col_type:
      req_json['value_type'] = new_col_type
    url = _API_ROOT + _API_ENDPOINTS['get_property_value']
    res = requests.post(url, json=req_json)
    payload = format_response(res)

    # Format the new column and create the new frame
    new_rows, new_types = [], set()
    for seed in payload:
      if property in payload[seed]:
        for node in payload[seed][property]:
          # Create the new row in the dataframe
          if 'value' in node:
            new_rows.append({seed_col_name: seed, new_col_name: node['value']})
          elif 'dcid' in node:
            new_rows.append({seed_col_name: seed, new_col_name: node['dcid']})

          # Interpret the type of the entry.
          if 'types' in node:
            new_types.update(node['types'])
          elif 'value' in node and node['value'].isnumeric():
            new_types.add('Number')
          else:
            new_types.add('Text')

    # Update the Frame with the new column type
    new_col_types = {seed_col_name: seed_col_type}
    if new_col_type:
      new_col_types[new_col_name] = [new_col_type]
    elif new_col_name:
      new_col_types[new_col_name] = list(new_types)

    # Create the new frame and merge the frame in.
    new_frame = Frame(new_rows, type_hint=new_col_types)
    self.merge(new_frame)

  def merge(self, frame, how='left', default=''):
    """ Joins the given frame into the current frame along shared column names.

    Args:
      frame: The Frame to merge in.
      how: Optional argument specifying the joins type to perform. Valid types
        include 'left', 'right', 'inner', and 'outer'
      default: The default place holder for an empty cell produced by the join.

    Raises:
      ValueError: if the given arguments are not valid. This may include either
        the given or current Frame does not contain the columns specified.
    """
    merge_on = set(self.columns()) & set(frame.columns())
    merge_on = list(merge_on)

    # If the current dataframe is empty, concat the given dataframe. If the
    # tables have no columns in common, perform a cross join. Otherwise join on
    # common columns.
    if self._dataframe.empty:
      self._col_types = {}
      self._dataframe = pd.concat([self._dataframe, frame._dataframe])
    elif len(merge_on) == 0:
      # Construct a unique dummy column name
      cross_on = ''.join(self.columns() + frame.columns())

      # Perform the cross join
      curr_frame = self._dataframe.assign(**{cross_on: 1})
      new_frame = frame._dataframe.assign(**{cross_on: 1})
      merged = curr_frame.merge(new_frame)
      self._dataframe = merged.drop(cross_on, 1)
    else:
      # Verify that columns being merged have the same type
      for col in merge_on:
        if set(self._col_types[col]) != set(frame._col_types[col]):
          raise ValueError(
              'Merge error: columns type mismatch for {}.\n  Current: {}\n  Given: {}'.format(col, self._col_types[col], frame._col_types[col]))

      # Merge dataframe, column types, and property maps
      self._dataframe = self._dataframe.merge(frame._dataframe, how=how, left_on=merge_on, right_on=merge_on)
      self._dataframe = self._dataframe.fillna(default)

    # Merge the types
    self._update_col_types(frame._col_types)

  def clear(self):
    """ Clears all the data stored in this extension. """
    self._col_types = {}
    self._dataframe = pd.DataFrame()

  # -------------------------- OTHER HELPER METHODS ---------------------------

  def _update_col_types(self, new_col_types):
    """ Updates the given Frame's column types with new column types.

    Args:
      new_col_types: A map from column name to a list or set of types.
    """
    for col in new_col_types:
      if col in self._col_types and isinstance(new_col_types[col], set):
        self._col_types[col] = list(new_col_types[col].union(set(self._col_types[col])))
      elif col in self._col_types and isinstance(new_col_types[col], list):
        self._col_types[col] = list(set(new_col_types[col] + self._col_types[col]))
      else:
        self._col_types[col] = list(new_col_types[col])

  def _verify_col_add(self, new_col, seed_col=None):
    """ Verifies that the seed_col exists and that the new_col does not.

    Args:
      new_col: The column that should not exist.
      seed_col: The column that must exist.

    Raises:
      RuntimeError: If either the seed_col does not exist, or the new_col does.
    """
    if new_col in self._dataframe:
      raise ValueError(
          'Column Error: {} is already a column.'.format(new_col))
    if seed_col and seed_col not in self._dataframe:
      raise ValueError(
          'Column Error: {} is not a valid seed column.'.format(seed_col))

  def _verify_col_type_includes(self, col_name, col_types):
    """ Verifies that the given column do not contain the given types.

    Args:
      col_name: The column to check.
      col_types: Column types to include.

    Raises:
      RuntimeError: If the column does not include the given types.
    """
    if any(t not in self._col_types[col_name] for t in col_types):
      type_str = col_types.join(', ')
      raise ValueError(
          'Column Error: column {} must include types {}'.format(col_name, type_str))

  def _verify_col_type_excludes(self, col_name, col_types):
    """ Verifies that the given column contains the given types.

    Args:
      col_name: The column to check.
      col_types: Column types to exclude.

    Raises:
      RuntimeError: If the column does includes the given types.
    """
    if any(t in self._col_types[col_name] for t in col_types):
      type_str = col_types.join(', ')
      raise ValueError(
          'Column Error: column {} cannot include types {}'.format(col_name, type_str))
