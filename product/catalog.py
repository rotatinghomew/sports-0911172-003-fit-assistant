from .store import require, money

class Catalog:
    def __init__(self, store):
        self.store = store

    def stock(self, id, brand, style, size, count, price):
        require(count>=0,'数量无效')
        return self.store.put('sku',id,dict(brand=brand,style=style,size=size,
            count=count,price=money(price)),create=True)

    def available(self, style):
        return [item for item in self.store.all('sku') if item['style']==style and item['count']>0]

    def adjust(self, id, delta):
        record = self.store.get('sku',id)
        require(isinstance(delta,int) and record['count']+delta>=0,'调整后数量无效')
        record['count']+=delta
        return self.store.put('sku',id,record)
