import importlib.util
import json
import unittest
from pathlib import Path
from product.app import Application
from product import insights

ROOT = Path(__file__).parent.parent


def load_evaluator():
    spec = importlib.util.spec_from_file_location(
        'evaluate_advisor', ROOT / 'scripts' / 'evaluate_advisor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AdvisorTestBase(unittest.TestCase):
    def setUp(self):
        self.app = Application()

    def tearDown(self):
        self.app.store.close()

    def replay(self, *operations):
        return self.app.replay(list(operations))

    def stock(self, id='s1', size='L', count=5, style='trail'):
        return ('catalog.stock', {'id': id, 'brand': 'ridge', 'style': style,
                                  'size': size, 'count': count, 'price': 10000})


class RecommendationTests(AdvisorTestBase):
    def test_fit_conclusion_ignores_stock(self):
        self.replay(self.stock('m', 'M', 5), self.stock('l', 'L', 0),
                 ('fittings.measure', {'id': 'f1', 'person': 'p1',
                                       'measured_at': '2026-10-01', 'height': 175,
                                       'foot': 26, 'brand': 'ridge', 'size': 'L',
                                       'result': 'fit'}))
        soldout = self.app.execute('advisor.recommend', {
            'id': 'r1', 'person': 'p1', 'style': 'trail', 'asked_at': '2026-10-02'})
        self.assertEqual(soldout['conclusion'], 'advise')
        self.assertEqual(soldout['fit_size'], 'L')
        self.assertFalse(soldout['fit_in_stock'])
        self.assertEqual([item['size'] for item in soldout['purchasable']], ['M'])
        self.assertIn('不代表与合身尺码同等合适', soldout['purchasable_note'])
        self.assertTrue(any('无货' in note for note in soldout['uncertainties']))
        self.app.execute('catalog.adjust', {'id': 'l', 'delta': 2})
        restocked = self.app.execute('advisor.recommend', {
            'id': 'r2', 'person': 'p1', 'style': 'trail', 'asked_at': '2026-10-03'})
        self.assertEqual(restocked['fit_size'], 'L')
        self.assertTrue(restocked['fit_in_stock'])
        self.assertEqual(restocked['evidence'], soldout['evidence'])

    def test_abstains_without_basis_and_never_orders(self):
        self.replay(self.stock())
        result = self.app.execute('advisor.recommend', {
            'id': 'r1', 'person': 'stranger', 'style': 'trail',
            'asked_at': '2026-10-02'})
        self.assertEqual(result['conclusion'], 'abstain')
        self.assertIsNone(result['fit_size'])
        self.assertTrue(result['uncertainties'])
        self.assertEqual(self.app.store.all('order'), [])

    def test_unknown_style_rejected(self):
        with self.assertRaises(ValueError):
            self.app.execute('advisor.recommend', {
                'id': 'r1', 'person': 'p1', 'style': 'ghost',
                'asked_at': '2026-10-02'})

    def test_correction_changes_eligibility_but_keeps_history(self):
        operations = [
            self.stock('m', 'M'), self.stock('l', 'L'),
            ('orders.place', {'id': 'o1', 'person': 'p1', 'sku': 'm',
                              'happened_at': '2026-10-02'}),
            ('orders.place', {'id': 'o2', 'person': 'p1', 'sku': 'l',
                              'happened_at': '2026-10-04'}),
            ('aftercare.record', {'id': 'a1', 'order': 'o1', 'outcome': 'exchange',
                                  'reason': 'size_small', 'observed_at': '2026-10-04',
                                  'replacement': 'o2'})]
        self.replay(*operations)
        before = self.app.execute('advisor.recommend', {
            'id': 'r1', 'person': 'p1', 'style': 'trail', 'asked_at': '2026-10-05'})
        self.assertEqual(before['conclusion'], 'advise')
        self.assertEqual(before['fit_size'], 'L')
        self.assertTrue(any('换为' in item for item in before['evidence']))
        self.app.execute('aftercare.correct', {
            'id': 'a1', 'reason': 'fabric', 'observed_at': '2026-10-10'})
        after = self.app.execute('advisor.recommend', {
            'id': 'r2', 'person': 'p1', 'style': 'trail', 'asked_at': '2026-10-11'})
        self.assertEqual(after['conclusion'], 'abstain')
        kept = self.app.store.get('advice', 'r1')
        self.assertEqual(kept['conclusion'], 'advise')
        self.assertEqual(kept['fit_size'], 'L')
        history = self.app.store.history('advice', 'r1')
        self.assertEqual(len(history), 1)

    def test_place_links_and_validates_advice(self):
        self.replay(self.stock('l', 'L'),
                 ('fittings.measure', {'id': 'f1', 'person': 'p1',
                                       'measured_at': '2026-10-01', 'height': 175,
                                       'foot': 26, 'brand': 'ridge', 'size': 'L',
                                       'result': 'fit'}),
                 ('advisor.recommend', {'id': 'r1', 'person': 'p1', 'style': 'trail',
                                        'asked_at': '2026-10-02'}))
        with self.assertRaises(ValueError):
            self.app.execute('orders.place', {'id': 'o1', 'person': 'other',
                                              'sku': 'l', 'happened_at': '2026-10-03',
                                              'advice': 'r1'})
        with self.assertRaises(ValueError):
            self.app.execute('orders.place', {'id': 'o2', 'person': 'p1',
                                              'sku': 'l', 'happened_at': '2026-10-03',
                                              'advice': 'missing'})
        order = self.app.execute('orders.place', {
            'id': 'o3', 'person': 'p1', 'sku': 'l', 'happened_at': '2026-10-03',
            'advice': 'r1'})
        self.assertEqual(order['advice'], 'r1')


class ReviewTests(AdvisorTestBase):
    def prepare(self):
        operations = [
            self.stock('l', 'L'), self.stock('m', 'M'),
            ('fittings.measure', {'id': 'f1', 'person': 'p1',
                                  'measured_at': '2026-10-01', 'height': 175,
                                  'foot': 26, 'brand': 'ridge', 'size': 'L',
                                  'result': 'fit'}),
            ('advisor.recommend', {'id': 'r1', 'person': 'p1', 'style': 'trail',
                                   'asked_at': '2026-10-02'}),
            ('orders.place', {'id': 'o1', 'person': 'p1', 'sku': 'l',
                              'happened_at': '2026-10-03', 'advice': 'r1'}),
            ('advisor.recommend', {'id': 'r2', 'person': 'p1', 'style': 'trail',
                                   'asked_at': '2026-10-04'}),
            ('orders.place', {'id': 'o2', 'person': 'p1', 'sku': 'l',
                              'happened_at': '2026-10-05', 'advice': 'r2'})]
        self.replay(*operations)

    def test_review_links_recommendation_to_outcome(self):
        self.prepare()
        self.replay(('aftercare.record', {'id': 'a1', 'order': 'o1',
                                       'outcome': 'followup', 'reason': 'fit',
                                       'observed_at': '2026-10-09'}),)
        review = self.app.execute('advisor.review', {'person': 'p1'})
        status = {item['advice']: item['status'] for item in review['items']}
        self.assertEqual(status['r1'], 'confirmed')
        self.assertEqual(status['r2'], 'pending')
        self.assertEqual(review['summary']['confirmed'], 1)

    def test_review_reflects_corrections(self):
        self.prepare()
        self.replay(('aftercare.record', {'id': 'a2', 'order': 'o2',
                                       'outcome': 'return', 'reason': 'size_large',
                                       'observed_at': '2026-10-09'}),)
        review = self.app.execute('advisor.review', {'person': 'p1'})
        status = {item['advice']: item['status'] for item in review['items']}
        self.assertEqual(status['r2'], 'contradicted')
        self.app.execute('aftercare.correct', {
            'id': 'a2', 'reason': 'fabric', 'observed_at': '2026-10-10'})
        review = self.app.execute('advisor.review', {'person': 'p1'})
        status = {item['advice']: item['status'] for item in review['items']}
        self.assertEqual(status['r2'], 'pending')
        kept = self.app.store.get('advice', 'r2')
        self.assertEqual(kept['fit_size'], 'L')


class InsightTests(AdvisorTestBase):
    def test_as_of_view_respects_business_time(self):
        self.replay(self.stock('m', 'M'), self.stock('l', 'L'),
                 ('fittings.measure', {'id': 'f1', 'person': 'p1',
                                       'measured_at': '2026-10-05', 'height': 175,
                                       'foot': 26, 'brand': 'ridge', 'size': 'M',
                                       'result': 'fit'}),
                 ('orders.place', {'id': 'o1', 'person': 'p1', 'sku': 'm',
                                   'happened_at': '2026-10-06'}),
                 ('aftercare.record', {'id': 'a1', 'order': 'o1', 'outcome': 'return',
                                       'reason': 'size_small',
                                       'observed_at': '2026-10-08'}))
        early = insights.as_of(self.app.store, '2026-10-04')
        self.assertEqual(early.fittings, [])
        self.assertEqual(early.orders, [])
        later = insights.as_of(self.app.store, '2026-10-08')
        self.assertEqual(len(later.fittings), 1)
        self.assertEqual(later.aftercare[0]['reason'], 'size_small')
        self.app.execute('aftercare.correct', {
            'id': 'a1', 'reason': 'fabric', 'observed_at': '2026-10-10'})
        before_fix = insights.as_of(self.app.store, '2026-10-09')
        self.assertEqual(before_fix.aftercare[0]['reason'], 'size_small')
        after_fix = insights.as_of(self.app.store, '2026-10-10')
        self.assertEqual(after_fix.aftercare[0]['reason'], 'fabric')

    def test_labels_follow_true_meaning(self):
        self.replay(self.stock('m', 'M'), self.stock('l', 'L'),
                 ('orders.place', {'id': 'o1', 'person': 'p1', 'sku': 'm',
                                   'happened_at': '2026-10-01'}),
                 ('orders.place', {'id': 'o2', 'person': 'p1', 'sku': 'l',
                                   'happened_at': '2026-10-02'}),
                 ('orders.place', {'id': 'o3', 'person': 'p1', 'sku': 'm',
                                   'happened_at': '2026-10-03'}),
                 ('orders.place', {'id': 'o4', 'person': 'p1', 'sku': 'm',
                                   'happened_at': '2026-10-04'}),
                 ('orders.place', {'id': 'o5', 'person': 'p1', 'sku': 'm',
                                   'happened_at': '2026-10-05'}),
                 ('aftercare.record', {'id': 'a1', 'order': 'o2',
                                       'outcome': 'followup', 'reason': 'fit',
                                       'observed_at': '2026-10-06'}),
                 ('aftercare.record', {'id': 'a2', 'order': 'o3',
                                       'outcome': 'exchange', 'reason': 'size_small',
                                       'observed_at': '2026-10-06',
                                       'replacement': 'o2'}),
                 ('aftercare.record', {'id': 'a3', 'order': 'o4',
                                       'outcome': 'return', 'reason': 'size_large',
                                       'observed_at': '2026-10-06'}),
                 ('aftercare.record', {'id': 'a4', 'order': 'o5',
                                       'outcome': 'return', 'reason': 'fabric',
                                       'observed_at': '2026-10-06'}))
        view = insights.as_of(self.app.store, insights.FAR_FUTURE)
        kinds = {}
        for order in view.orders:
            kinds[order['id']] = insights.order_label(view, order)
        self.assertEqual(kinds['o1']['kind'], 'unverified')
        self.assertEqual(kinds['o2'], {'kind': 'confirmed', 'size': 'L'})
        self.assertEqual(kinds['o3']['kind'], 'resolved')
        self.assertEqual(kinds['o3']['size'], 'L')
        self.assertEqual(kinds['o4'], {'kind': 'directional', 'from': 'M',
                                       'direction': 'down'})
        self.assertEqual(kinds['o5']['kind'], 'unrelated')

    def test_folds_keep_persons_together(self):
        requests = [{'person': f'p{i % 4}', 'at': f'2026-10-{day:02d}'}
                    for i, day in enumerate([1, 2, 3, 4, 5, 6, 7, 8])]
        folds = insights.assign_folds(requests, 2)
        seen = {}
        for index, fold in enumerate(folds):
            for request in fold:
                if request['person'] in seen:
                    self.assertEqual(seen[request['person']], index)
                else:
                    seen[request['person']] = index
        self.assertEqual(sum(len(fold) for fold in folds), len(requests))

    def test_score_counts_only_verified_answers(self):
        results = [
            {'decision': {'size': 'L'}, 'label': {'kind': 'confirmed', 'size': 'L'}},
            {'decision': {'size': 'L'}, 'label': {'kind': 'resolved', 'size': 'M',
                                                  'from': 'S', 'direction': 'up'}},
            {'decision': {'size': 'L'}, 'label': {'kind': 'unverified'}},
            {'decision': {'size': None}, 'label': {'kind': 'confirmed', 'size': 'L'}},
            {'decision': {'size': 'XL'}, 'label': {'kind': 'directional',
                                                   'from': 'M', 'direction': 'up'}}]
        scored = insights.score(results)
        self.assertEqual(scored['requests'], 5)
        self.assertEqual(scored['answered'], 4)
        self.assertEqual(scored['verified_answers'], 2)
        self.assertEqual(scored['correct'], 1)
        self.assertEqual(scored['reliability'], 0.5)
        self.assertEqual(scored['unverifiable_answers'], 1)
        self.assertEqual(scored['directional_consistent'], 1)


class EvaluationTests(AdvisorTestBase):
    def test_harness_never_uses_future_facts(self):
        operations = [
            self.stock('m', 'M'),
            ('orders.place', {'id': 'o1', 'person': 'p1', 'sku': 'm',
                              'happened_at': '2026-10-02'}),
            ('aftercare.record', {'id': 'a1', 'order': 'o1', 'outcome': 'followup',
                                  'reason': 'fit', 'observed_at': '2026-10-09'})]
        self.replay(*operations)
        module = load_evaluator()
        report = module.evaluate(self.app, k=1)
        self.assertEqual(report['requests'], 1)
        for name, model_report in report['models'].items():
            self.assertEqual(model_report['overall']['answered'], 0, name)
        self.assertEqual(report['label_distribution'], {'confirmed': 1})

    def test_harness_reports_slices_and_folds(self):
        operations = json.loads((ROOT / 'data' / 'history.json').read_text())
        self.replay(*operations)
        module = load_evaluator()
        report = module.evaluate(self.app, k=4)
        self.assertEqual(set(report['models']), set(insights.CANDIDATES))
        for model_report in report['models'].values():
            self.assertEqual(len(model_report['folds']), 4)
            total = sum(fold['requests'] for fold in model_report['folds'])
            self.assertEqual(total, report['requests'])
            self.assertIn('体型', model_report['slices'])
            self.assertIn('品牌', model_report['slices'])
            self.assertIn('证据积累', model_report['slices'])
        self.assertGreater(report['label_distribution']['unverified'], 0)


if __name__ == '__main__':
    unittest.main()
