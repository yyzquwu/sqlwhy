SELECT o.id, p.status
FROM orders AS o
LEFT JOIN payments AS p ON p.id = o.primary_payment_id
