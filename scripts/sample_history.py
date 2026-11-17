import random
import json
from datetime import date, timedelta

def generate():
    rng = random.Random(117203)
    operations = []

    def add(command, **arguments):
        operations.append([command, arguments])

    def day(offset):
        return (date(2026, 7, 1) + timedelta(days=offset)).isoformat()
    for brand in ['ridge', 'lake']:
        for size in ['M', 'L', 'XL']:
            add('catalog.stock', id=f'{brand}-{size}', brand=brand, style=f'{brand}-trail', size=size, count=600, price=30000)
    for index in range(260):
        person = f'p{index % 90}'
        brand = ['ridge', 'lake'][index % 2]
        size = ['M', 'L', 'XL'][index % 3]
        at = day(index % 80)
        add('fittings.measure', id=f'f{index}', person=person, measured_at=at, height=155 + index % 40, foot=22 + index % 9 / 2, brand=brand, size=size, result=rng.choice(['small', 'fit', 'large', 'unknown']))
        add('orders.place', id=f'o{index}', person=person, sku=f'{brand}-{size}', happened_at=at)
        if index % 5 == 0:
            replacement = ['M', 'L', 'XL'][(index + 1) % 3]
            add('orders.place', id=f'x{index}', person=person, sku=f'{brand}-{replacement}', happened_at=day(index % 80 + 3))
            add('aftercare.record', id=f'a{index}', order=f'o{index}', outcome='exchange', reason='size_small', observed_at=day(index % 80 + 3), replacement=f'x{index}')
        elif index % 4 == 0:
            add('aftercare.record', id=f'a{index}', order=f'o{index}', outcome='return', reason=rng.choice(['size_large', 'fabric', 'changed_mind']), observed_at=day(index % 80 + 5))
        elif index % 3 == 0:
            add('aftercare.record', id=f'a{index}', order=f'o{index}', outcome='followup', reason='fit', observed_at=day(index % 80 + 7))
        if index % 20 == 0:
            add('aftercare.correct', id=f'a{index}', reason='fabric', observed_at=day(index % 80 + 9))
    return operations

def main():
    import argparse
    parser = argparse.ArgumentParser(description='生成本地合成业务记录，不包含真实个人资料')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    from pathlib import Path
    Path(args.output).write_text(json.dumps(generate(), ensure_ascii=False, indent=2) + '\n')

if __name__=='__main__':
    main()
