"""HA schema compatibility without renaming existing options or states."""
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).parents[1]

class TranslationContractTests(unittest.TestCase):
    def test_all_state_translation_keys_are_schema_compatible(self):
        base=ROOT/'custom_components/ariston'
        for path in [base/'strings.json',*(base/'translations').glob('*.json')]:
            data=json.loads(path.read_text())
            for domain,entities in data.get('entity',{}).items():
                for key,entity in entities.items():
                    for state in entity.get('state',{}):
                        with self.subTest(file=path.name,domain=domain,key=key,state=state):
                            self.assertRegex(state,r'^[a-z0-9]+(?:[_-][a-z0-9]+)*$')

