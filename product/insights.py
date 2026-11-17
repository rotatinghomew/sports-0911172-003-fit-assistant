"""尺码建议的纯函数分析层：时间点视图、标签语义、候选模型与评分。

标签语义（与 docs/业务约定.md 一致）：
- 未回访、未退货、未售出都不是合身确认；
- 回访确认（followup/fit）才是确认；换货新单尺码记为"解决尺码"；退货只记方向；
- 非尺码原因（如 fabric、changed_mind）不作合身证据；
- 售后原因以更正后为准，但时间点视图只应用不晚于请求时刻的更正，
  更正依据业务时间字段（observed_at/corrected_at），不用变更日志的登记时刻。
"""

SIZE_ORDER = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
SIZE_UP = 'size_small'    # 买到的偏小，应往大一码
SIZE_DOWN = 'size_large'  # 买到的偏大，应往小一码
SIZE_REASONS = {SIZE_UP, SIZE_DOWN}
MODEL_VERSION = 'combined-1'
FAR_FUTURE = '9999-12-31'


class View:
    """某一业务时刻可观察到的记录。fittings/orders 按各自业务时间过滤，
    aftercare 为该时刻已生效的修订（含不晚于该时刻的更正）。"""

    def __init__(self, fittings, orders, aftercare, skus):
        self.fittings = fittings
        self.orders = orders
        self.aftercare = aftercare
        self.skus = skus  # id -> sku payload
        self.aftercare_by_order = {}
        for record in aftercare:
            self.aftercare_by_order.setdefault(record['order'], []).append(record)

    def sku_of(self, order):
        return self.skus.get(order['sku'], {})

    def brand_of(self, order):
        return self.sku_of(order).get('brand')

    def style_of(self, order):
        return self.sku_of(order).get('style')

    def size_of(self, order):
        return self.sku_of(order).get('size')

    def profile(self, person):
        """该人此刻最近一次测量；没有则为 None。"""
        own = [f for f in self.fittings if f['person'] == person]
        if not own:
            return None
        latest = max(own, key=lambda f: f['measured_at'])
        return {'height': latest['height'], 'foot': latest['foot'],
                'measured_at': latest['measured_at']}


def as_of(store, at):
    """按业务时间重建 at 时刻（含）之前可观察的记录。"""
    fittings = [f for f in store.all('fitting') if f['measured_at'] <= at]
    orders = [o for o in store.all('order') if o['happened_at'] <= at]
    visible = []
    for current in store.all('aftercare'):
        chosen = None
        for change in store.history('aftercare', current['id']):
            payload = change['payload']
            moment = payload.get('corrected_at') or payload['observed_at']
            if moment <= at:
                chosen = payload
        if chosen is not None:
            visible.append(chosen)
    skus = {s['id']: s for s in store.all('sku')}
    return View(fittings, orders, visible, skus)


def order_label(view, order):
    """订单的合身标签。未售后为 unverified（不是确认）；非尺码原因为 unrelated。"""
    records = view.aftercare_by_order.get(order['id'], [])
    if not records:
        return {'kind': 'unverified'}
    latest = max(records, key=lambda a: a.get('corrected_at') or a['observed_at'])
    size = view.size_of(order)
    if latest['outcome'] == 'followup':
        if latest['reason'] == 'fit':
            return {'kind': 'confirmed', 'size': size}
        return {'kind': 'unverified'}  # 回访了但未确认合身
    reason = latest['reason']
    if reason not in SIZE_REASONS:
        return {'kind': 'unrelated'}
    direction = 'up' if reason == SIZE_UP else 'down'
    if latest['outcome'] == 'exchange' and latest.get('replacement'):
        target = next((o for o in view.orders if o['id'] == latest['replacement']), None)
        if target is not None:
            return {'kind': 'resolved', 'size': view.size_of(target),
                    'from': size, 'direction': direction}
    return {'kind': 'directional', 'from': size, 'direction': direction}


def _shift(size, delta):
    if size in SIZE_ORDER:
        index = SIZE_ORDER.index(size) + delta
        if 0 <= index < len(SIZE_ORDER):
            return SIZE_ORDER[index]
    return None


def _decision(model, size, evidence, population, uncertainties):
    return {'model': model, 'size': size, 'evidence': evidence,
            'population': population, 'uncertainties': uncertainties}


def own_history(view, request):
    """候选模型：只用本人记录。确认购买 > 最近有效试穿 > 换货解决 > 退货方向。"""
    evidence, uncertainties = [], []
    person, brand = request['person'], request['brand']
    own = [o for o in view.orders
           if o['person'] == person and view.brand_of(o) == brand]
    labeled = [(o, order_label(view, o)) for o in own]
    confirmed = [(o, l) for o, l in labeled if l['kind'] == 'confirmed']
    if confirmed:
        order, label = max(confirmed, key=lambda pair: pair[0]['happened_at'])
        evidence.append(f"{order['happened_at']} 本人购买 {brand} {label['size']}，回访确认合身")
        return _decision('own-history', label['size'], evidence, '本人历史记录', uncertainties)
    fittings = sorted((f for f in view.fittings
                       if f['person'] == person and f['brand'] == brand),
                      key=lambda f: f['measured_at'], reverse=True)
    for fitting in fittings:
        if fitting['result'] == 'unknown':
            continue
        if fitting['result'] == 'fit':
            evidence.append(f"{fitting['measured_at']} 本人试穿 {brand} {fitting['size']} 合身")
            return _decision('own-history', fitting['size'], evidence, '本人历史记录', uncertainties)
        delta = 1 if fitting['result'] == 'small' else -1
        word = '偏小' if fitting['result'] == 'small' else '偏大'
        size = _shift(fitting['size'], delta)
        if size is not None:
            evidence.append(f"{fitting['measured_at']} 本人试穿 {brand} {fitting['size']} {word}，按尺码表调整一码")
            uncertainties.append('由单次试穿结果外推一码，未经穿着确认')
            return _decision('own-history', size, evidence, '本人历史记录', uncertainties)
        uncertainties.append(f"试穿尺码 {fitting['size']} 超出已知尺码表，无法外推")
        break
    resolved = [(o, l) for o, l in labeled if l['kind'] == 'resolved']
    if resolved:
        order, label = max(resolved, key=lambda pair: pair[0]['happened_at'])
        evidence.append(f"{order['happened_at']} 本人 {brand} {label['from']} 换为 {label['size']}")
        uncertainties.append('换得尺码未经回访确认')
        return _decision('own-history', label['size'], evidence, '本人历史记录', uncertainties)
    directional = [(o, l) for o, l in labeled if l['kind'] == 'directional']
    if directional:
        order, label = max(directional, key=lambda pair: pair[0]['happened_at'])
        size = _shift(label['from'], 1 if label['direction'] == 'up' else -1)
        word = '偏小' if label['direction'] == 'up' else '偏大'
        if size is not None:
            evidence.append(f"{order['happened_at']} 本人购买 {brand} {label['from']} 因尺码{word}退货")
            uncertainties.append('仅知退货方向，合适尺码未经确认')
            return _decision('own-history', size, evidence, '本人历史记录', uncertainties)
    return _decision('own-history', None, evidence, '本人历史记录',
                     uncertainties or ['本人无可用记录'])


def crowd_neighbors(view, request):
    """候选模型：同品牌已验证人群的多数尺码，优先体型相近者。"""
    evidence, uncertainties = [], []
    brand = request['brand']
    best = {}
    for order in view.orders:
        if view.brand_of(order) != brand:
            continue
        label = order_label(view, order)
        if label['kind'] not in ('confirmed', 'resolved'):
            continue
        current = best.get(order['person'])
        if current is None or order['happened_at'] > current[1]:
            best[order['person']] = (label['size'], order['happened_at'])
    entries = [{'person': p, 'size': s, 'profile': view.profile(p)}
               for p, (s, _) in best.items()]
    pool, scope = entries, '品牌全体'
    height, foot = request.get('height'), request.get('foot')
    if height and foot:
        near = [e for e in entries if e['profile']
                and abs(e['profile']['height'] - height) <= 5
                and abs(e['profile']['foot'] - foot) <= 0.5]
        if len(near) >= 3:
            pool, scope = near, '体型相近人群'
        else:
            uncertainties.append(f'体型相近样本不足（{len(near)} 人），扩大到品牌全体')
    else:
        uncertainties.append('缺少本人测量，按品牌全体估计')
    counts = {}
    for entry in pool:
        counts[entry['size']] = counts.get(entry['size'], 0) + 1
    total = sum(counts.values())
    if total >= 3:
        size, count = max(counts.items(), key=lambda item: item[1])
        if count / total >= 0.6:
            evidence.append(f'{scope}中 {count}/{total} 的已验证合身尺码为 {size}')
            return _decision('crowd-neighbors', size, evidence,
                             f'{scope}（{brand}，已验证 n={total}）', uncertainties)
    uncertainties.append(f'{scope}已验证样本不足或意见分散（n={total}）')
    return _decision('crowd-neighbors', None, evidence,
                     f'{scope}（{brand}）', uncertainties)


def combined(view, request):
    """候选模型：本人记录优先，不足时回退到人群统计。"""
    first = own_history(view, request)
    if first['size'] is not None:
        first['model'] = 'combined'
        return first
    second = crowd_neighbors(view, request)
    second['model'] = 'combined'
    second['evidence'] = first['evidence'] + second['evidence']
    second['uncertainties'] = first['uncertainties'] + second['uncertainties']
    return second


CANDIDATES = {'own-history': own_history, 'crowd-neighbors': crowd_neighbors,
              'combined': combined}


def _ratio(part, whole):
    return round(part / whole, 4) if whole else None


def _direction_consistent(size, label):
    if size not in SIZE_ORDER or label['from'] not in SIZE_ORDER:
        return False
    delta = SIZE_ORDER.index(size) - SIZE_ORDER.index(label['from'])
    return (delta > 0) if label['direction'] == 'up' else (delta < 0)


def score(results):
    """results: [{'decision':…, 'label':…}]。可靠性只在已验证答案上计算；
    只知方向的退货单列；无法验证的答案保持可见，不计入可靠性。"""
    answered = [r for r in results if r['decision']['size'] is not None]
    verified = [r for r in answered if r['label']['kind'] in ('confirmed', 'resolved')]
    correct = [r for r in verified if r['decision']['size'] == r['label']['size']]
    directional = [r for r in answered if r['label']['kind'] == 'directional']
    consistent = [r for r in directional
                  if _direction_consistent(r['decision']['size'], r['label'])]
    return {
        'requests': len(results),
        'answered': len(answered),
        'coverage': _ratio(len(answered), len(results)),
        'verified_answers': len(verified),
        'correct': len(correct),
        'reliability': _ratio(len(correct), len(verified)),
        'directional_answers': len(directional),
        'directional_consistent': len(consistent),
        'unverifiable_answers': len(answered) - len(verified) - len(directional),
    }


def assign_folds(requests, k):
    """按人分组、按各组首个请求时间连续分折：同一人的请求不跨折，
    各折保持时间先后，供候选模型在同一折划分上可比。"""
    by_person = {}
    for request in requests:
        by_person.setdefault(request['person'], []).append(request)
    persons = sorted(by_person,
                     key=lambda p: min(r['at'] for r in by_person[p]))
    folds = [[] for _ in range(k)]
    for index, person in enumerate(persons):
        folds[min(index * k // len(persons), k - 1)].extend(by_person[person])
    return folds
