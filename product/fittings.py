from .store import require, money

class Fittings:
    def __init__(self, store):
        self.store = store

    def measure(self, id, person, measured_at, height, foot, brand, size, result):
        require(height>0 and foot>0,'测量无效')
        require(result in ('small','fit','large','unknown'),'试穿结果无效')
        return self.store.put('fitting',id,dict(person=person,measured_at=measured_at,
            height=height,foot=foot,brand=brand,size=size,result=result),create=True)

    def for_person(self, person):
        return [item for item in self.store.all('fitting') if item['person']==person]
