import json
import unittest
from pathlib import Path
from product.app import Application


class BusinessBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.app=Application()
        self.app.replay(json.loads((Path(__file__).parent.parent/'data'/'demo.json').read_text()))

    def tearDown(self):
        self.app.store.close()
    def test_exchange_restores_original_stock(self):
        self.assertEqual(self.app.store.get('sku','m')['count'],3)
        self.assertEqual(self.app.store.get('sku','l')['count'],0)

    def test_correction_does_not_rewrite_original_reason(self):
        history=self.app.store.history('aftercare','a1')
        self.assertEqual([item['payload']['reason'] for item in history],['size_small','fabric'])
