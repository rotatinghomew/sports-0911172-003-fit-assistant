from .store import require, money

class Orders:
    def __init__(self, store):
        self.store = store

    def place(self, id, person, sku, happened_at, advice=None):
        with self.store.atomic():
            item = self.store.get('sku',sku)
            require(item['count']>0,'已售罄')
            if advice is not None:
                record = self.store.get('advice',advice)
                require(record['person']==person,'建议与下单人不一致')
                require(record['style']==item['style'],'建议与商品款式不一致')
            result = self.store.put('order',id,dict(person=person,sku=sku,cents=item['price'],
                happened_at=happened_at,state='sold',advice=advice),create=True)
            item['count']-=1
            self.store.put('sku',sku,item)
            return result

    def history(self, person):
        return [item for item in self.store.all('order') if item['person']==person]
