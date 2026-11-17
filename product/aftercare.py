from .store import require, money

class Aftercare:
    def __init__(self, store):
        self.store = store

    def record(self, id, order, outcome, reason, observed_at, replacement=None):
        require(outcome in ('return','exchange','followup'),'售后类型无效')
        with self.store.atomic():
            original = self.store.get('order',order)
            require(original['state']=='sold' or outcome=='followup','该订单已退换')
            if outcome=='exchange':
                require(bool(replacement),'换货缺少新订单')
                target = self.store.get('order',replacement)
                require(target['person']==original['person'] and replacement!=order,'换货人不一致')
            result = self.store.put('aftercare',id,dict(order=order,outcome=outcome,
                reason=reason,observed_at=observed_at,replacement=replacement),create=True)
            if outcome!='followup':
                original['state']=outcome
                self.store.put('order',order,original)
                sku = self.store.get('sku',original['sku'])
                sku['count']+=1
                self.store.put('sku',sku['id'],sku)
            return result

    def correct(self, id, reason, observed_at):
        require(bool(reason),'更正原因为空')
        record = self.store.get('aftercare',id)
        record.update(reason=reason,corrected_at=observed_at)
        return self.store.put('aftercare',id,record)
