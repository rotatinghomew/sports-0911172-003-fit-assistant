"""候选尺码模型的可比较离线验证。

用法:
    python3 scripts/evaluate_advisor.py --input data/history.json [--output report.json]

保证:
- 每个请求只用它发生时刻（含）之前可观察的记录，售后更正按业务时间生效，
  不用后来的售后回答较早的请求；
- 同一人的全部请求落在同一验证折，连买换货不拆到两侧；
- 未回访、未退货、未售出的订单不作确认标签，在报告中单独可见；
- 所有候选模型面对同一批请求与同一折划分，指标口径一致。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from product.app import Application
from product import insights

NOVELTY_LIMIT = 10


def build_requests(store):
    """每个历史订单构成一个"下单时刻该推荐什么"的请求。"""
    skus = {s['id']: s for s in store.all('sku')}
    requests = []
    for order in store.all('order'):
        sku = skus[order['sku']]
        requests.append({'person': order['person'], 'style': sku['style'],
                         'brand': sku['brand'], 'at': order['happened_at'],
                         'ordered_size': sku['size'], 'order': order['id']})
    requests.sort(key=lambda r: (r['at'], r['order']))
    return requests


def body_bucket(request):
    height, foot = request.get('height'), request.get('foot')
    if not height:
        band_h = '未知'
    else:
        band_h = ('<160' if height < 160 else '160-169' if height < 170
                  else '170-179' if height < 180 else '≥180')
    if not foot:
        band_f = '未知'
    else:
        band_f = '<24' if foot < 24 else '24-25.9' if foot < 26 else '≥26'
    return f'身高{band_h}/脚长{band_f}'


def verified_count(view, style):
    total = 0
    for order in view.orders:
        if view.style_of(order) != style:
            continue
        if insights.order_label(view, order)['kind'] in ('confirmed', 'resolved'):
            total += 1
    return total


def evaluate(app, k=5, candidates=None):
    store = app.store
    candidates = candidates or insights.CANDIDATES
    requests = build_requests(store)
    final_view = insights.as_of(store, insights.FAR_FUTURE)
    orders_by_id = {o['id']: o for o in store.all('order')}
    folds = insights.assign_folds(requests, k)
    fold_of = {id(request): index for index, fold in enumerate(folds)
               for request in fold}
    labels = {}
    results = {name: [] for name in candidates}
    for request in requests:
        view = insights.as_of(store, request['at'])
        profile = view.profile(request['person'])
        if profile:
            request['height'] = profile['height']
            request['foot'] = profile['foot']
        label = insights.order_label(final_view, orders_by_id[request['order']])
        labels[label['kind']] = labels.get(label['kind'], 0) + 1
        slices = {'体型': body_bucket(request), '品牌': request['brand'],
                  '证据积累': ('证据少(新款)' if verified_count(view, request['style']) < NOVELTY_LIMIT
                            else '有验证积累')}
        for name, model in candidates.items():
            results[name].append({'request': request, 'decision': model(view, request),
                                  'label': label, 'slices': slices,
                                  'fold': fold_of[id(request)]})
    report = {'requests': len(requests), 'folds': k, 'label_distribution': labels,
              'guarantees': ['请求特征只含不晚于请求时刻的记录（售后更正按业务时间生效）',
                          '同一人的请求不跨验证折',
                          '未回访/未退货/未售出不作确认标签'],
              'models': {}}
    for name, rows in results.items():
        model_report = {'overall': insights.score(rows),
                        'folds': [insights.score([r for r in rows if r['fold'] == i])
                                  for i in range(k)],
                        'slices': {}}
        for dimension in ('体型', '品牌', '证据积累'):
            keys = sorted({r['slices'][dimension] for r in rows})
            model_report['slices'][dimension] = {
                key: insights.score([r for r in rows if r['slices'][dimension] == key])
                for key in keys}
        report['models'][name] = model_report
    return report


def main():
    parser = argparse.ArgumentParser(description='候选尺码模型的可比较离线验证')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--folds', type=int, default=5)
    args = parser.parse_args()
    app = Application()
    try:
        app.replay(json.loads(args.input.read_text()))
        report = evaluate(app, k=args.folds)
    finally:
        app.store.close()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
