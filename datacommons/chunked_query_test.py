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
""" Data Commons Python API unit tests.

Unit tests for the SPARQL chunked query wrapper.
"""
import unittest
import datacommons as dc
from query import conduct_chunked_query


class TestChunkedQuery(unittest.TestCase):
    """ Unit tests for the conduct_chunked_query. """

    def test_large_query(self):
        """Test with a large list of almost 4000 gene dcids"""

        # Use Data Commons to get a long list of gene dcids
        # This query finds all DiseaseGeneAssociation nodes involving the disease
        query_str = '''
    SELECT ?dga_dcid
    WHERE {
    ?disease dcid "bio/DOID_8778" .
    ?dga_dcid typeOf 	DiseaseGeneAssociation .
    ?dga_dcid diseaseOntologyID ?disease .
    }
    '''
        result = dc.query(query_str)

        hg38_associated_genes = []

        dga_dcids = set()
        for row in result:
            dga_dcids.add(row['?dga_dcid'])

        genes_raw = dc.get_property_values(dga_dcids, 'geneID')
        # gets genes list
        hg38_associated_genes = [
            gene for cdc, gene_list in genes_raw.items() for gene in gene_list
            if gene.startswith('bio/hg38')
        ]

        print(len(hg38_associated_genes))
        # find ChemicalCompoundGeneAssociation nodes for each gene
        query_template = '''
    SELECT ?cga_dcid ?gene
    WHERE {{
    ?gene dcid ("{gene_dcids}") .
    ?cga_dcid typeOf {type} .
    ?cga_dcid {label} ?gene .
    }}
    '''
        mapping = {
            'type': 'ChemicalCompoundGeneAssociation',
            'label': 'geneID',
            'gene_dcids': hg38_associated_genes,
        }

        chunk_result = conduct_chunked_query(query_template, mapping)
        print(len(chunk_result))
        self.assertEqual(len(chunk_result) > 4200, True)

    def test_no_list(self):
        """Test with no list as an input to template string."""
        query_str = '''
    SELECT ?dcid
    WHERE {{
    ?dcid typeOf Disease .
    ?dcid commonName "{name}" .
    }}
    '''
        result = conduct_chunked_query(query_str, {'name': "Crohn's disease"})
        self.assertEqual(result, [{'?dcid': 'bio/DOID_8778'}])


if __name__ == '__main__':
    unittest.main()
