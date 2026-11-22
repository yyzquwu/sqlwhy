SELECT o.id, p.status
FROM orders AS o
LEFT JOIN payments AS p ON p.order_id = o.id
WHERE p.status = 'settled'
