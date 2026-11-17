from .store import require
from . import insights


class Advisor:
    """尺码建议服务。合身结论只由证据决定，库存只影响可买清单；
    证据不足时拒答；建议只记录不下单。建议记录一经写入不修改，
    售后更正只影响之后的建议与评估，历史建议仍按当时证据解释。"""

    def __init__(self, store):
        self.store = store

    def recommend(self, id, person, style, asked_at, height=None, foot=None):
        require(isinstance(person, str) and person, '顾客编号无效')
        require(isinstance(style, str) and style, '款式无效')
        require(isinstance(asked_at, str) and asked_at, '请求时间无效')
        for value, name in ((height, '身高'), (foot, '脚长')):
            require(value is None or (isinstance(value, (int, float))
                                      and not isinstance(value, bool) and value > 0),
                    f'{name}无效')
        with self.store.atomic():
            skus = [s for s in self.store.all('sku') if s['style'] == style]
            require(skus, '未知款式')
            brands = sorted({s['brand'] for s in skus})
            view = insights.as_of(self.store, asked_at)
            source = None
            if height is not None and foot is not None:
                source = '本次提供'
            else:
                profile = view.profile(person)
                if profile is not None:
                    height = height if height is not None else profile['height']
                    foot = foot if foot is not None else profile['foot']
                    source = f"最近测量 {profile['measured_at']}"
            request = {'person': person, 'brand': brands[0], 'style': style,
                       'at': asked_at, 'height': height, 'foot': foot}
            decision = insights.combined(view, request)
            uncertainties = list(decision['uncertainties'])
            if len(brands) > 1:
                uncertainties.append('款式对应多个品牌，按第一个品牌估计')
            if source is None:
                uncertainties.append('缺少本人测量数据')
            size = decision['size']
            available = [s for s in skus if s['count'] > 0]
            in_stock = None if size is None else any(s['size'] == size for s in available)
            if size is not None and not in_stock:
                uncertainties.append(
                    f'合身尺码 {size} 当前无货；可买清单仅为库存信息，不代表同等合适')
            record = dict(person=person, style=style, brand=brands[0], asked_at=asked_at,
                          height=height, foot=foot, measurement_source=source,
                          model=insights.MODEL_VERSION,
                          conclusion='advise' if size else 'abstain',
                          fit_size=size, fit_in_stock=in_stock,
                          evidence=decision['evidence'],
                          population=decision['population'],
                          uncertainties=uncertainties,
                          purchasable=[{'sku': s['id'], 'size': s['size'], 'count': s['count']}
                                       for s in available],
                          purchasable_note='可买清单仅反映库存，不改变合身结论，'
                                           '也不代表与合身尺码同等合适')
            return self.store.put('advice', id, record, create=True)

    def review(self, person=None):
        """把已记录的建议与关联订单、当前售后（含更正）对照。
        状态：confirmed/contradicted/pending/unordered/abstained。"""
        advices = [a for a in self.store.all('advice')
                   if person is None or a['person'] == person]
        orders = self.store.all('order')
        skus = {s['id']: s for s in self.store.all('sku')}
        view = insights.View(self.store.all('fitting'), orders,
                             self.store.all('aftercare'), skus)
        by_advice = {}
        for order in orders:
            if order.get('advice'):
                by_advice.setdefault(order['advice'], []).append(order)
        items = []
        summary = {}
        for advice in advices:
            linked = []
            for order in by_advice.get(advice['id'], []):
                linked.append({'order': order['id'], 'size': view.size_of(order),
                               'happened_at': order['happened_at'],
                               'label': insights.order_label(view, order)})
            if advice['conclusion'] != 'advise':
                status = 'abstained'
            elif not linked:
                status = 'unordered'
            else:
                fit = advice['fit_size']
                confirmed = any(l['label']['kind'] in ('confirmed', 'resolved')
                                and l['label'].get('size') == fit for l in linked)
                contradicted = any(
                    (l['label']['kind'] in ('resolved', 'directional')
                     and l['label'].get('from') == fit)
                    or (l['label']['kind'] == 'confirmed'
                        and l['label'].get('size') != fit)
                    for l in linked)
                status = ('confirmed' if confirmed
                          else 'contradicted' if contradicted else 'pending')
            summary[status] = summary.get(status, 0) + 1
            items.append({'advice': advice['id'], 'person': advice['person'],
                          'model': advice['model'], 'asked_at': advice['asked_at'],
                          'conclusion': advice['conclusion'],
                          'fit_size': advice['fit_size'], 'status': status,
                          'orders': linked})
        return {'summary': summary, 'items': items}
